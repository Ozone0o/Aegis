"""Aegis controller: Observe → Detect → Decide → Recover."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from .collectors import (
    CollectorRegistry,
    HardwareCollector,
    NodeCollector,
    ProcessCollector,
    ResourceCollector,
    TopicCollector,
)
from .config import AegisConfig
from .dependencies import DependencyGraph
from .events import EventBus, JsonEventStore, JsonStateStore
from .models import (
    Event,
    HealthCheckSpec,
    HealthObservation,
    HealthState,
    HealthStatus,
    RecoveryDecision,
    RecoveryResult,
    TickResult,
)
from .policy import PolicyEngine
from .recovery import RecoveryManager

logger = logging.getLogger(__name__)


def default_collectors(
    *,
    node_list_provider: Callable[[], Iterable[object]] | None = None,
) -> CollectorRegistry:
    """Build the standard collector set without importing ROS 2."""

    return CollectorRegistry(
        [
            TopicCollector(),
            NodeCollector(node_list_provider=node_list_provider),
            ProcessCollector(),
            HardwareCollector(),
            ResourceCollector(),
        ]
    )


class AegisCore:
    """Runtime reliability controller.

    The class is intentionally synchronous.  ROS timers, asyncio services,
    or a fleet manager can all call :meth:`tick`; collectors and recovery
    execution have explicit seams for integrations that need async workers.
    """

    def __init__(
        self,
        config: AegisConfig | None = None,
        *,
        collectors: CollectorRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        recovery_manager: RecoveryManager | None = None,
        event_bus: EventBus | None = None,
        dependency_graph: DependencyGraph | None = None,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        state_store: JsonStateStore | None = None,
        event_store: JsonEventStore | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.hysteresis = config.recovery.hysteresis if config else 1
        self.checks: dict[str, HealthCheckSpec] = dict(config.checks) if config else {}
        self.states: dict[str, HealthState] = {
            name: HealthState(name=name, spec=spec) for name, spec in self.checks.items()
        }
        self.collectors = collectors or default_collectors()
        self.policy_engine = policy_engine or PolicyEngine(
            config.policies if config else [],
            config.actions if config else {},
            default_cooldown=config.recovery.cooldown if config else 30.0,
            clock=clock,
        )
        self.recovery_manager = recovery_manager or RecoveryManager(
            dry_run=config.recovery.dry_run if config else False
        )
        self.event_bus = event_bus or EventBus()
        self.dependency_graph = dependency_graph or DependencyGraph(
            config.dependencies if config else {}
        )
        self.state_store = state_store or (
            JsonStateStore(config.state_file) if config is not None else None
        )
        self.event_store = event_store or (
            JsonEventStore(config.event_log) if config is not None else None
        )
        if self.event_store is not None:
            self.event_bus.subscribe(self.event_store.append)
        self._last_tick: float | None = None
        self._last_events: list[Event] = []

    def add_check(self, check: HealthCheckSpec) -> None:
        self.checks[check.name] = check
        self.states.setdefault(check.name, HealthState(name=check.name, spec=check))

    def remove_check(self, name: str) -> None:
        self.checks.pop(name, None)
        self.states.pop(name, None)

    def register_collector(self, kind: str, collector: Any) -> None:
        self.collectors.register(kind, collector)

    def set_node_list_provider(self, provider: Callable[[], Iterable[object]]) -> None:
        collector = self.collectors.get("node")
        if isinstance(collector, NodeCollector):
            collector.set_node_list_provider(provider)

    def topic_message(self, check_name: str, timestamp: float | None = None) -> None:
        collector = self.collectors.get("topic")
        if isinstance(collector, TopicCollector):
            collector.on_message(check_name, timestamp)

    def set_node_presence(self, target: str, exists: bool) -> None:
        collector = self.collectors.get("node")
        if isinstance(collector, NodeCollector):
            collector.set_presence(target, exists)

    def observe(self) -> dict[str, HealthObservation]:
        """Run every enabled collector once."""

        observations: dict[str, HealthObservation] = {}
        for name, check in self.checks.items():
            if not check.enabled:
                observations[name] = HealthObservation(
                    check_name=name,
                    status=HealthStatus.WARNING,
                    message="check is disabled",
                    details={"disabled": True},
                )
            else:
                observations[name] = self.collectors.collect(check)
        return observations

    def tick(self, now: float | None = None) -> TickResult:
        """Run one complete reliability reconciliation cycle."""

        timestamp = self.clock() if now is None else now
        raw_observations = self.observe()

        # Evaluate dependencies on a copy so every real state transition in
        # this tick compares against the same pre-tick state.
        working_states = copy.deepcopy(self.states)
        for name, observation in raw_observations.items():
            self._apply_observation(working_states[name], observation, timestamp)
        effective_observations = self.dependency_graph.propagate(
            working_states, raw_observations
        )

        emitted: list[Event] = []
        for name, observation in effective_observations.items():
            state = self.states[name]
            old_status = state.status
            state.previous_status = old_status
            self._apply_observation(state, observation, timestamp)
            if state.status != old_status:
                state.last_transition = timestamp
                event_kind = "dependency.unavailable" if state.derived else "health.transition"
                severity = self._severity(state.status)
                message = state.message
                event = self.event_bus.emit(
                    event_kind,
                    name,
                    message,
                    status=state.status,
                    previous_status=old_status,
                    severity=severity,
                    root_cause=state.root_cause,
                    affected=sorted(self.dependency_graph.descendants(name))
                    if event_kind == "health.transition"
                    else (),
                    metadata={"details": state.details, "derived": state.derived},
                    dedupe_key=(
                        f"{event_kind}:{name}:{old_status.value}:"
                        f"{state.status.value}:{state.root_cause or ''}"
                    ),
                    timestamp=timestamp,
                )
                if event is not None:
                    emitted.append(event)

        decisions = self.policy_engine.evaluate(self.states, effective_observations, timestamp)
        recoveries: list[RecoveryResult] = []
        for decision in decisions:
            self._mark_recovering(decision, timestamp, emitted)
            result = self.recovery_manager.execute(decision)
            recoveries.append(result)
            self.policy_engine.notify_recovery_result(decision, result)
            kind = "recovery.succeeded" if result.success else "recovery.failed"
            source_state = self.states.get(decision.source)
            event = self.event_bus.emit(
                kind,
                decision.source,
                result.message,
                status=HealthStatus.RECOVERING,
                severity="info" if result.success else "error",
                root_cause=source_state.root_cause if source_state else None,
                metadata={
                    "policy": decision.policy_name,
                    "action": decision.action.action_type,
                    "success": result.success,
                    "details": result.details,
                },
                dedupe_key=f"{kind}:{decision.policy_name}:{decision.source}",
                timestamp=timestamp,
            )
            if event is not None:
                emitted.append(event)

        self._last_tick = timestamp
        self._last_events = emitted
        if self.state_store is not None:
            self.state_store.save(self.states, timestamp=timestamp)
        return TickResult(
            observations=effective_observations,
            states=dict(self.states),
            decisions=decisions,
            recoveries=recoveries,
            events=emitted,
            timestamp=timestamp,
        )

    def status(self, name: str | None = None) -> HealthState | dict[str, HealthState] | None:
        if name is not None:
            return self.states.get(name)
        return dict(self.states)

    def events(self, limit: int | None = None, since: float | None = None) -> list[Event]:
        return self.event_bus.recent(limit=limit, since=since)

    @property
    def last_tick(self) -> float | None:
        return self._last_tick

    def _apply_observation(
        self,
        state: HealthState,
        observation: HealthObservation,
        timestamp: float,
    ) -> None:
        desired = observation.status
        if desired == HealthStatus.OK:
            state.consecutive_ok += 1
            state.consecutive_failures = 0
            if state.status in {
                HealthStatus.WARNING,
                HealthStatus.ERROR,
                HealthStatus.RECOVERING,
            } and state.consecutive_ok < self.hysteresis:
                desired = state.status
        else:
            state.consecutive_ok = 0
            state.consecutive_failures += 1

        state.message = observation.message
        state.observed_value = observation.observed_value
        state.details = dict(observation.details)
        state.last_checked = timestamp
        if desired == HealthStatus.OK:
            state.last_ok = timestamp
            state.root_cause = None
            state.derived = False
        else:
            state.root_cause = observation.details.get("root_cause")
            state.derived = bool(observation.details.get("derived", False))
        state.status = desired

    def _mark_recovering(
        self,
        decision: RecoveryDecision,
        timestamp: float,
        emitted: list[Event],
    ) -> None:
        state = self.states.get(decision.source)
        if state is None:
            return
        old_status = state.status
        state.previous_status = old_status
        state.status = HealthStatus.RECOVERING
        state.recovery_attempts += 1
        state.last_recovery = timestamp
        state.last_transition = timestamp
        event = self.event_bus.emit(
            "recovery.started",
            decision.source,
            f"policy '{decision.policy_name}' selected {decision.action.action_type}",
            status=HealthStatus.RECOVERING,
            previous_status=old_status,
            severity="warning",
            root_cause=state.root_cause,
            metadata={
                "policy": decision.policy_name,
                "action": decision.action.action_type,
                "reason": decision.reason,
            },
            dedupe_key=f"recovery.started:{decision.policy_name}:{decision.source}",
            timestamp=timestamp,
        )
        if event is not None:
            emitted.append(event)

    @staticmethod
    def _severity(status: HealthStatus) -> str:
        return {
            HealthStatus.OK: "info",
            HealthStatus.WARNING: "warning",
            HealthStatus.ERROR: "error",
            HealthStatus.RECOVERING: "warning",
        }[status]
