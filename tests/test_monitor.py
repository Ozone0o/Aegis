"""Configuration and safety tests for the canonical Aegis package."""

from __future__ import annotations

from aegis.config import AegisConfig
from aegis.models import ActionSpec, RecoveryDecision
from aegis.recovery import CommandResult, RecoveryManager


def test_canonical_check_layout_normalizes_to_aegis_checks() -> None:
    config = AegisConfig.from_dict(
        {
            "checks": {
                "camera": {
                    "type": "topic",
                    "target": "/camera/image_raw",
                    "stale_timeout": 2,
                }
            }
        }
    )
    assert config.checks["camera"].target == "/camera/image_raw"
    assert config.checks["camera"].stale_timeout == 2.0


class _Executor:
    def __init__(self) -> None:
        self.commands: list[str | list[str]] = []

    def run(self, command, timeout: float) -> CommandResult:
        self.commands.append(command)
        return CommandResult(0, stdout="ok")


def _decision(action: ActionSpec) -> RecoveryDecision:
    return RecoveryDecision(
        policy_name="test",
        source="camera",
        action=action,
        reason="test",
    )


def test_string_recovery_commands_are_rejected_by_default() -> None:
    executor = _Executor()
    result = RecoveryManager(executor=executor).execute(
        _decision(ActionSpec(name="restart", type="execute_command", command="echo {target}"))
    )
    assert result.success is False
    assert "string recovery commands" in result.message
    assert executor.commands == []


def test_argument_list_recovery_command_is_rendered_without_shell() -> None:
    executor = _Executor()
    result = RecoveryManager(executor=executor).execute(
        _decision(
            ActionSpec(
                name="restart",
                type="execute_command",
                target="/camera;touch /tmp/pwned",
                command=["restart-camera", "{target}"],
            )
        )
    )
    assert result.success is True
    assert executor.commands == [["restart-camera", "/camera;touch /tmp/pwned"]]


def test_explicit_unsafe_shell_is_visible_in_result() -> None:
    executor = _Executor()
    result = RecoveryManager(executor=executor).execute(
        _decision(
            ActionSpec(
                name="shell",
                type="execute_command",
                command="echo {target}",
                unsafe_shell=True,
                target="camera",
            )
        )
    )
    assert result.success is True
    assert executor.commands == ["echo camera"]
