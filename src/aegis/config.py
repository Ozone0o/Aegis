"""Load and normalize ``aegis.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import ActionSpec, HealthCheckSpec, PolicyRule
from .policy import parse_duration


@dataclass
class RecoverySettings:
    cooldown: float = 30.0
    max_attempts: int = 3
    dry_run: bool = False
    hysteresis: int = 1


@dataclass
class AegisConfig:
    """Fully normalized runtime configuration."""

    checks: dict[str, HealthCheckSpec]
    policies: list[PolicyRule] = field(default_factory=list)
    actions: dict[str, ActionSpec] = field(default_factory=dict)
    dependencies: dict[str, set[str]] = field(default_factory=dict)
    interval: float = 1.0
    recovery: RecoverySettings = field(default_factory=RecoverySettings)
    runtime_dir: Path = Path(".aegis")
    event_log: Path = Path(".aegis/events.jsonl")
    state_file: Path = Path(".aegis/state.json")
    version: str = "aegis/v1"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, base_dir: Path | None = None) -> AegisConfig:
        if not isinstance(data, Mapping):
            raise ValueError("Aegis configuration must be a YAML mapping")
        checks_data = data.get("checks")
        if not checks_data:
            raise ValueError("Aegis configuration requires a 'checks' section")
        checks = _parse_checks(checks_data)
        actions = _parse_actions(data.get("actions", {}))
        recovery_data = data.get("recovery", {}) or {}
        if not isinstance(recovery_data, Mapping):
            raise ValueError("'recovery' must be a mapping")
        recovery = RecoverySettings(
            cooldown=parse_duration(recovery_data.get("cooldown"), 30.0),
            max_attempts=int(recovery_data.get("max_attempts", 3)),
            dry_run=bool(recovery_data.get("dry_run", False)),
            hysteresis=max(1, int(recovery_data.get("hysteresis", 1))),
        )
        policies = _parse_policies(data.get("policies", data.get("rules", [])), actions, recovery)
        dependencies = _parse_dependencies(data.get("dependencies", {}))
        interval = parse_duration(data.get("interval", data.get("poll_interval")), 1.0)

        base = (base_dir or Path.cwd()).resolve()
        storage = data.get("storage", {}) or {}
        if not isinstance(storage, Mapping):
            raise ValueError("'storage' must be a mapping")
        runtime_dir = _resolve_path(storage.get("runtime_dir", ".aegis"), base)
        event_log = _resolve_path(storage.get("event_log", runtime_dir / "events.jsonl"), base)
        state_file = _resolve_path(storage.get("state_file", runtime_dir / "state.json"), base)
        return cls(
            checks=checks,
            policies=policies,
            actions=actions,
            dependencies=dependencies,
            interval=max(0.05, interval),
            recovery=recovery,
            runtime_dir=runtime_dir,
            event_log=event_log,
            state_file=state_file,
            version=str(data.get("apiVersion", data.get("version", "aegis/v1"))),
            raw=dict(data),
        )


def load_config(path: str | Path) -> AegisConfig:
    """Read, validate and normalize an Aegis YAML file."""

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Aegis configuration does not exist: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid Aegis YAML: {exc}") from exc
    return AegisConfig.from_dict(data or {}, base_dir=config_path.parent)


def _parse_checks(raw: Any) -> dict[str, HealthCheckSpec]:
    if isinstance(raw, list):
        entries = []
        for item in raw:
            if not isinstance(item, Mapping) or "name" not in item:
                raise ValueError("each check list entry requires a name")
            entries.append((str(item["name"]), item))
    elif isinstance(raw, Mapping):
        entries = [(str(name), value) for name, value in raw.items()]
    else:
        raise ValueError("'checks' must be a mapping or list")

    checks: dict[str, HealthCheckSpec] = {}
    for name, value in entries:
        if not isinstance(value, Mapping):
            raise ValueError(f"check '{name}' must be a mapping")
        check_type = str(value.get("type", "topic")).lower()
        target = value.get("target")
        if target is None:
            raise ValueError(f"check '{name}' requires a target")
        params = dict(value.get("params", {}) or {})
        reserved = {
            "name", "type", "target", "stale_timeout", "expected_rate", "warning_threshold",
            "error_threshold", "timeout", "enabled", "labels", "params",
        }
        params.update({str(key): item for key, item in value.items() if key not in reserved})
        expected_rate = value.get("expected_rate")
        checks[name] = HealthCheckSpec(
            name=name,
            type=check_type,
            target=str(target) if target is not None else None,
            stale_timeout=parse_duration(value.get("stale_timeout"), 1.0),
            expected_rate=float(expected_rate) if expected_rate is not None else None,
            warning_threshold=_number(value.get("warning_threshold", value.get("warning"))),
            error_threshold=_number(value.get("error_threshold", value.get("error"))),
            timeout=parse_duration(value.get("timeout"), 5.0),
            enabled=bool(value.get("enabled", True)),
            labels={str(k): str(v) for k, v in dict(value.get("labels", {}) or {}).items()},
            params=params,
        )
    if not checks:
        raise ValueError("Aegis configuration contains no checks")
    return checks


def _parse_actions(raw: Any) -> dict[str, ActionSpec]:
    if not raw:
        return {}
    if isinstance(raw, list):
        entries = []
        for item in raw:
            if not isinstance(item, Mapping) or "name" not in item:
                raise ValueError("each action list entry requires a name")
            entries.append((str(item["name"]), item))
    elif isinstance(raw, Mapping):
        entries = [(str(name), value) for name, value in raw.items()]
    else:
        raise ValueError("'actions' must be a mapping or list")
    actions: dict[str, ActionSpec] = {}
    for name, value in entries:
        if isinstance(value, str):
            value = {"type": value}
        if not isinstance(value, Mapping):
            raise ValueError(f"action '{name}' must be a mapping")
        params = dict(value.get("params", {}) or {})
        reserved = {
            "name", "type", "action", "target", "command", "timeout", "cooldown",
            "max_attempts", "unsafe_shell", "params",
        }
        params.update({str(key): item for key, item in value.items() if key not in reserved})
        actions[name] = ActionSpec(
            name=name,
            type=str(value.get("type", value.get("action", name))),
            target=value.get("target"),
            command=value.get("command"),
            unsafe_shell=bool(value.get("unsafe_shell", False)),
            timeout=parse_duration(value.get("timeout"), 30.0),
            cooldown=(
                parse_duration(value.get("cooldown"), None)
                if value.get("cooldown") is not None
                else None
            ),
            max_attempts=(
                int(value["max_attempts"]) if value.get("max_attempts") is not None else None
            ),
            params=params,
        )
    return actions


def _parse_policies(
    raw: Any,
    actions: dict[str, ActionSpec],
    recovery: RecoverySettings,
) -> list[PolicyRule]:
    if not raw:
        return []
    if isinstance(raw, Mapping):
        entries = []
        for name, value in raw.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("name", name)
            else:
                item = {"name": name, "when": value, "then": name}
            entries.append(item)
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError("'policies' must be a mapping or list")
    policies: list[PolicyRule] = []
    for index, value in enumerate(entries):
        if not isinstance(value, Mapping):
            raise ValueError(f"policy at index {index} must be a mapping")
        name = str(value.get("name", f"policy-{index + 1}"))
        condition = value.get("when", value.get("if", value.get("condition")))
        action = value.get("then", value.get("action", value.get("do")))
        if condition is None or action is None:
            raise ValueError(f"policy '{name}' requires when/if and then/action")
        policies.append(
            PolicyRule(
                name=name,
                condition=condition,
                action=action,
                cooldown=parse_duration(value.get("cooldown"), recovery.cooldown),
                max_attempts=(
                    int(value["max_attempts"])
                    if value.get("max_attempts") is not None
                    else recovery.max_attempts
                ),
                enabled=bool(value.get("enabled", True)),
                repeat=bool(value.get("repeat", False)),
                labels={str(k): str(v) for k, v in dict(value.get("labels", {}) or {}).items()},
            )
        )
    return policies


def _parse_dependencies(raw: Any) -> dict[str, set[str]]:
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        result: dict[str, set[str]] = {}
        for dependent, prerequisites in raw.items():
            if isinstance(prerequisites, str):
                prerequisites = [prerequisites]
            if not isinstance(prerequisites, (list, tuple, set)):
                raise ValueError(f"dependencies for '{dependent}' must be a list")
            result[str(dependent)] = {str(item) for item in prerequisites}
        return result
    if isinstance(raw, list):
        result = {}
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("dependency list entries must be mappings")
            dependent = item.get("dependent", item.get("name"))
            prerequisite = item.get("depends_on", item.get("dependency"))
            if dependent is None or prerequisite is None:
                raise ValueError("dependency entry requires dependent and depends_on")
            values = [prerequisite] if isinstance(prerequisite, str) else prerequisite
            result.setdefault(str(dependent), set()).update(str(value) for value in values)
        return result
    raise ValueError("'dependencies' must be a mapping or list")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("threshold must be numeric")
    return float(value)


def _resolve_path(value: Any, base: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path).resolve()
