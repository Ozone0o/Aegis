"""Recovery manager and built-in actions."""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import ActionSpec, ActionType, RecoveryDecision, RecoveryResult

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandExecutor:
    """Small command execution seam for safe tests and robot integrations."""

    def run(
        self,
        command: str | list[str],
        timeout: float,
        *,
        unsafe_shell: bool = False,
    ) -> CommandResult:
        if isinstance(command, str) and not unsafe_shell:
            raise ValueError("string recovery commands require unsafe_shell=true")
        if unsafe_shell and not isinstance(command, str):
            raise ValueError("unsafe_shell=true requires a single reviewed command string")
        if isinstance(command, list):
            if not command:
                raise ValueError("recovery command argument lists cannot be empty")
            if not all(isinstance(item, str) for item in command):
                raise ValueError("recovery command argument lists must contain strings")
        result = subprocess.run(
            command,
            shell=unsafe_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)


class RecoveryManager:
    """Dispatch policy decisions to explicit, bounded recovery handlers."""

    def __init__(
        self,
        executor: CommandExecutor | Callable[[str | list[str], float], CommandResult] | None = None,
        notifier: Callable[..., Any] | None = None,
        shutdown_callback: Callable[..., Any] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.executor = executor or CommandExecutor()
        self.notifier = notifier
        self.shutdown_callback = shutdown_callback
        self.dry_run = dry_run
        self._handlers: dict[str, Callable[[RecoveryDecision], RecoveryResult]] = {
            ActionType.RESTART_NODE.value: self._restart_node,
            ActionType.RESTART_LAUNCH.value: self._restart_launch,
            ActionType.EXECUTE_COMMAND.value: self._execute_command,
            ActionType.NOTIFY_OPERATOR.value: self._notify_operator,
            ActionType.SAFE_SHUTDOWN.value: self._safe_shutdown,
        }

    def register(
        self,
        action_type: str,
        handler: Callable[[RecoveryDecision], RecoveryResult],
    ) -> None:
        self._handlers[str(action_type).lower()] = handler

    def execute(self, decision: RecoveryDecision) -> RecoveryResult:
        started = time.time()
        action_type = decision.action.action_type.lower()
        handler = self._handlers.get(action_type)
        if handler is None:
            return RecoveryResult(
                action=decision.action.name,
                success=False,
                message=f"unsupported recovery action '{action_type}'",
                started_at=started,
                finished_at=time.time(),
            )
        try:
            result = handler(decision)
        except Exception as exc:
            logger.exception("Recovery action %s failed", decision.action.name)
            return RecoveryResult(
                action=decision.action.name,
                success=False,
                message=f"recovery action failed: {exc}",
                started_at=started,
                finished_at=time.time(),
            )
        result.started_at = started
        result.finished_at = time.time()
        return result

    def _run(self, action: ActionSpec, command: str | list[str] | None) -> RecoveryResult:
        if not command:
            return RecoveryResult(
                action=action.name,
                success=False,
                message=f"{action.action_type} requires a command",
            )
        if isinstance(command, str) and not action.unsafe_shell:
            return RecoveryResult(
                action=action.name,
                success=False,
                message=(
                    "string recovery commands are disabled by default; use a command "
                    "argument list or explicitly set unsafe_shell=true"
                ),
                details={"unsafe_shell": False},
            )
        rendered = self._render_command(command, action)
        if self.dry_run:
            logger.info("Aegis dry-run: %s", self._display_command(rendered))
            return RecoveryResult(
                action=action.name,
                success=True,
                message=f"dry-run: {self._display_command(rendered)}",
                details={"dry_run": True, "command": rendered},
            )
        result = self._execute(rendered, action.timeout, unsafe_shell=action.unsafe_shell)
        success = result.returncode == 0
        return RecoveryResult(
            action=action.name,
            success=success,
            message=(result.stdout or "command completed").strip()
            if success
            else (result.stderr or result.stdout or f"command exited {result.returncode}").strip(),
            details={
                "command": rendered,
                "returncode": result.returncode,
                "stdout": result.stdout[-1000:],
                "stderr": result.stderr[-1000:],
            },
        )

    def _execute(
        self,
        command: str | list[str],
        timeout: float,
        *,
        unsafe_shell: bool,
    ) -> CommandResult:
        executor = self.executor
        if isinstance(executor, CommandExecutor):
            return executor.run(command, timeout, unsafe_shell=unsafe_shell)
        if hasattr(executor, "run"):
            return executor.run(command, timeout)  # type: ignore[union-attr]
        return executor(command, timeout)  # type: ignore[operator]

    @staticmethod
    def _render_command(command: str | list[str], action: ActionSpec) -> str | list[str]:
        if isinstance(command, list):
            return [str(item).format(target=action.target or "") for item in command]
        return str(command).format(target=action.target or "")

    @staticmethod
    def _display_command(command: str | list[str]) -> str:
        return command if isinstance(command, str) else shlex.join(command)

    @staticmethod
    def _command_from(action: ActionSpec) -> str | list[str] | None:
        return (
            action.command
            or action.params.get("command")
            or action.params.get("command_template")
        )

    def _restart_node(self, decision: RecoveryDecision) -> RecoveryResult:
        action = decision.action
        command = self._command_from(action)
        if command is None and action.params.get("restart_command"):
            command = action.params["restart_command"]
        return self._run(action, command)

    def _restart_launch(self, decision: RecoveryDecision) -> RecoveryResult:
        return self._run(decision.action, self._command_from(decision.action))

    def _execute_command(self, decision: RecoveryDecision) -> RecoveryResult:
        return self._run(decision.action, self._command_from(decision.action))

    def _notify_operator(self, decision: RecoveryDecision) -> RecoveryResult:
        message = decision.action.params.get(
            "message",
            f"Aegis recovery requested for {decision.source}: {decision.reason}",
        )
        if self.notifier is None:
            logger.warning("Aegis operator notification: %s", message)
            return RecoveryResult(action=decision.action.name, success=True, message=str(message))
        try:
            try:
                self.notifier(str(message), decision)
            except TypeError:
                self.notifier(str(message))
            return RecoveryResult(action=decision.action.name, success=True, message=str(message))
        except Exception as exc:
            return RecoveryResult(
                action=decision.action.name,
                success=False,
                message=f"operator notification failed: {exc}",
            )

    def _safe_shutdown(self, decision: RecoveryDecision) -> RecoveryResult:
        action = decision.action
        command = self._command_from(action)
        if command:
            return self._run(action, command)
        if self.shutdown_callback is None:
            return RecoveryResult(
                action=action.name,
                success=False,
                message="safe_shutdown requires a shutdown callback or command",
            )
        if self.dry_run:
            return RecoveryResult(
                action=action.name,
                success=True,
                message="dry-run: shutdown skipped",
            )
        try:
            try:
                self.shutdown_callback(decision)
            except TypeError:
                self.shutdown_callback()
            return RecoveryResult(
                action=action.name,
                success=True,
                message="safe shutdown requested",
            )
        except Exception as exc:
            return RecoveryResult(
                action=action.name,
                success=False,
                message=f"safe shutdown failed: {exc}",
            )


class NoOpRecovery(RecoveryManager):
    """Compatibility strategy that records but never runs a command."""

    def __init__(self) -> None:
        super().__init__(dry_run=True)

    def execute(self, decision: RecoveryDecision) -> RecoveryResult:
        return RecoveryResult(
            action=decision.action.name,
            success=False,
            message=f"NoOpRecovery skipped {decision.source}",
        )
