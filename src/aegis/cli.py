"""The ``aegis`` command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import load_config
from .core import AegisCore
from .node import run_ros


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="Aegis — runtime protection and recovery for ROS 2 robots.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start the ROS 2 reliability controller")
    _add_config_argument(start)

    check = subparsers.add_parser("check", help="run one health reconciliation")
    _add_config_argument(check)
    check.add_argument("--json", action="store_true", help="print machine-readable output")
    check.add_argument(
        "--recover",
        action="store_true",
        help="also execute matching recovery policies (opt-in for one-shot checks)",
    )
    check.add_argument("--no-recovery", action="store_true", help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="show the last persisted health snapshot")
    _add_config_argument(status, required=False)
    status.add_argument("--json", action="store_true", help="print machine-readable output")

    events = subparsers.add_parser("events", help="show structured Aegis events")
    _add_config_argument(events, required=False)
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, ros_args = parser.parse_known_args(argv)
    if args.command != "start" and ros_args:
        parser.error(f"unrecognized arguments: {' '.join(ros_args)}")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if hasattr(args, "config"):
        args.config = str(_resolve_config(args.config))
    try:
        if args.command == "start":
            return run_ros(args.config, ros_args)
        if args.command == "check":
            return _check(args)
        if args.command == "status":
            return _status(args)
        if args.command == "events":
            return _events(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"aegis: {exc}", file=sys.stderr)
        return 2
    return 2


def _add_config_argument(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--config",
        "-c",
        dest="config",
        default="aegis.yaml",
        required=False,
        help="path to aegis.yaml (default: ./aegis.yaml)",
    )


def _resolve_config(path: str) -> Path:
    candidate = Path(path)
    if candidate.exists() or path != "aegis.yaml":
        return candidate
    example = Path("config/aegis.yaml")
    return example if example.exists() else candidate


def _check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    core = AegisCore(config)
    if not args.recover or args.no_recovery:
        core.policy_engine.policies = []
    result = core.tick()
    rows = [_state_row(state) for state in result.states.values()]
    if args.json:
        payload = {"timestamp": result.timestamp, "checks": rows}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_status_rows(rows)
        for recovery in result.recoveries:
            result_word = "OK" if recovery.success else "FAILED"
            print(f"recovery {recovery.action}: {result_word} — {recovery.message}")
    return 0 if all(row["status"] == "OK" for row in rows) else 1


def _status(args: argparse.Namespace) -> int:
    state_path = _state_path(args.config)
    if args.config and Path(args.config).exists():
        state_path = load_config(args.config).state_file
    if not state_path.exists():
        print(f"aegis: no state snapshot at {state_path}", file=sys.stderr)
        return 1
    try:
        snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"aegis: unable to read state snapshot: {exc}", file=sys.stderr)
        return 2
    rows = list(snapshot.get("states", {}).values())
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(f"last check: {snapshot.get('timestamp', 'unknown')}")
        _print_status_rows(rows)
    return 0 if all(row.get("status") == "OK" for row in rows) else 1


def _events(args: argparse.Namespace) -> int:
    event_path = _event_path(args.config)
    if args.config and Path(args.config).exists():
        event_path = load_config(args.config).event_log
    if not event_path.exists():
        print(f"aegis: no event log at {event_path}", file=sys.stderr)
        return 1
    rows: list[dict[str, Any]] = []
    try:
        with event_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"aegis: unable to read event log: {exc}", file=sys.stderr)
        return 2
    rows = [] if args.limit <= 0 else rows[-args.limit:]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            timestamp = row.get("timestamp", "")
            severity = str(row.get("severity", "info")).upper()
            print(
                f"{timestamp} [{severity}] {row.get('kind')} "
                f"{row.get('source')}: {row.get('message')}"
            )
    return 0


def _state_path(config: str | None) -> Path:
    if config and Path(config).exists():
        return load_config(config).state_file
    return Path(".aegis/state.json")


def _event_path(config: str | None) -> Path:
    if config and Path(config).exists():
        return load_config(config).event_log
    return Path(".aegis/events.jsonl")


def _state_row(state: Any) -> dict[str, Any]:
    if hasattr(state, "to_dict"):
        data = state.to_dict()
    elif isinstance(state, dict):
        data = dict(state)
    else:
        data = asdict(state)
    spec = data.get("spec")
    if isinstance(spec, dict) and hasattr(spec.get("type"), "value"):
        spec["type"] = spec["type"].value
    return {
        "name": data.get("name"),
        "status": data.get("status"),
        "message": data.get("message", ""),
        "observed_value": data.get("observed_value"),
        "root_cause": data.get("root_cause"),
        "derived": data.get("derived", False),
        "last_checked": data.get("last_checked"),
    }


def _print_status_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        root = f" root={row['root_cause']}" if row.get("root_cause") else ""
        print(
            f"{row.get('name', '?'):<24} {row.get('status', '?'):<11} "
            f"{row.get('message', '')}{root}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
