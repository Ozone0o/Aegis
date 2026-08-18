"""Focused tests for the Aegis runtime reliability loop."""

from __future__ import annotations

from aegis.collectors import (
    CallableCollector,
    CollectorRegistry,
    HardwareCollector,
    TopicCollector,
    TopicProbe,
)
from aegis.core import AegisCore
from aegis.dependencies import DependencyCycleError, DependencyGraph
from aegis.models import (
    ActionSpec,
    HealthCheckSpec,
    HealthObservation,
    HealthStatus,
    PolicyRule,
)
from aegis.policy import ConditionEvaluator, PolicyEngine, parse_duration
from aegis.recovery import CommandResult, RecoveryManager


def test_parse_duration_and_expression_condition() -> None:
    assert parse_duration("500ms") == 0.5
    assert parse_duration("2m") == 120.0

    check = HealthCheckSpec(name="camera_topic", type="topic")
    from aegis.models import HealthState

    state = HealthState(
        name=check.name,
        spec=check,
        status=HealthStatus.WARNING,
        details={"stale_age": 3.0},
    )
    observation = HealthObservation(
        check_name=check.name,
        status=HealthStatus.WARNING,
        details={"stale_age": 3.0},
    )
    matched, source, _ = ConditionEvaluator().evaluate(
        "camera_topic.stale_age > 2s", {check.name: state}, {check.name: observation}
    )
    assert matched is True
    assert source == "camera_topic"
    matched, _, _ = ConditionEvaluator().evaluate(
        {"check": "camera_topic", "status": "WARNING"},
        {check.name: state},
        {check.name: observation},
    )
    assert matched is True


def test_topic_collector_detects_stale_topic() -> None:
    current = [0.0]
    probe = TopicProbe(clock=lambda: current[0])
    collector = TopicCollector(probes={"camera": probe}, clock=lambda: current[0])
    check = HealthCheckSpec(name="camera", type="topic", target="/camera", stale_timeout=2.0)

    assert collector.collect(check).status == HealthStatus.ERROR
    probe.on_message()
    assert collector.collect(check).status == HealthStatus.OK
    current[0] = 2.1
    assert collector.collect(check).status == HealthStatus.WARNING


class _FakeExecutor:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[str | list[str]] = []

    def run(self, command: str | list[str], timeout: float) -> CommandResult:
        self.commands.append(command)
        return CommandResult(self.returncode, stdout="done")


def test_recovery_manager_executes_explicit_command() -> None:
    executor = _FakeExecutor()
    manager = RecoveryManager(executor=executor)
    from aegis.models import RecoveryDecision

    decision = RecoveryDecision(
        policy_name="restart-camera",
        source="camera",
        action=ActionSpec(
            name="restart-camera",
            type="execute_command",
            command=["restart-camera", "{target}"],
            target="/camera_node",
        ),
        reason="camera is stale",
    )
    result = manager.execute(decision)
    assert result.success is True
    assert executor.commands == [["restart-camera", "/camera_node"]]


def test_hardware_collector_rejects_implicit_shell_commands() -> None:
    check = HealthCheckSpec(
        name="probe",
        type="hardware",
        params={"command": "echo unsafe"},
    )

    observation = HardwareCollector().collect(check)

    assert observation.status == HealthStatus.ERROR
    assert "unsafe_shell=true" in observation.message


def test_dependency_chain_preserves_one_root_cause_and_edges_recovery() -> None:
    healthy = {"camera": False}

    def probe(check: HealthCheckSpec) -> HealthObservation:
        ok = healthy["camera"] if check.name == "camera" else True
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK if ok else HealthStatus.ERROR,
            message="ok" if ok else "camera failed",
        )

    registry = CollectorRegistry([CallableCollector(probe)])
    registry.register("hardware", registry.get("custom"))
    policy = PolicyEngine(
        [
            PolicyRule(
                name="recover-camera",
                condition={"check": "camera", "status": "ERROR"},
                action={
                    "type": "execute_command",
                    "command": ["recover-camera"],
                },
                cooldown=0,
            )
        ],
        default_cooldown=0,
    )
    executor = _FakeExecutor()
    core = AegisCore(
        collectors=registry,
        policy_engine=policy,
        recovery_manager=RecoveryManager(executor=executor),
        dependency_graph=DependencyGraph({"detector": ["camera"], "tracking": ["detector"]}),
    )
    for name in ("camera", "detector", "tracking"):
        core.add_check(HealthCheckSpec(name=name, type="hardware"))

    first = core.tick(now=1.0)
    assert first.decisions[0].source == "camera"
    assert core.states["camera"].status == HealthStatus.RECOVERING
    assert core.states["detector"].status == HealthStatus.ERROR
    assert core.states["tracking"].root_cause == "camera"

    second = core.tick(now=2.0)
    assert second.decisions == []
    assert len([event for event in core.events() if event.kind == "recovery.started"]) == 1

    healthy["camera"] = True
    third = core.tick(now=3.0)
    assert all(state.status == HealthStatus.OK for state in third.states.values())
    assert all(state.root_cause is None for state in third.states.values())


def test_dependency_cycle_is_rejected() -> None:
    try:
        DependencyGraph({"a": ["b"], "b": ["a"]})
    except DependencyCycleError:
        pass
    else:
        raise AssertionError("cycle should be rejected")
