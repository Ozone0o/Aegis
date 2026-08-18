"""Public data model for the Aegis runtime.

The model deliberately contains no ROS 2 imports.  Aegis can therefore be
used as a small in-process reliability controller in tests, in a launch
manager, or next to a ROS 2 node.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    """The state of a health check."""

    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"


class CheckType(str, Enum):
    """Supported health signal kinds."""

    TOPIC = "topic"
    NODE = "node"
    PROCESS = "process"
    HARDWARE = "hardware"
    RESOURCE = "resource"


class ActionType(str, Enum):
    """Built-in recovery actions."""

    RESTART_NODE = "restart_node"
    RESTART_LAUNCH = "restart_launch"
    EXECUTE_COMMAND = "execute_command"
    NOTIFY_OPERATOR = "notify_operator"
    SAFE_SHUTDOWN = "safe_shutdown"


@dataclass
class HealthCheckSpec:
    """Configuration for one observed health signal."""

    name: str
    type: CheckType | str
    target: str | None = None
    stale_timeout: float = 1.0
    expected_rate: float | None = None
    warning_threshold: float | None = None
    error_threshold: float | None = None
    timeout: float = 5.0
    enabled: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.type, CheckType):
            return
        try:
            self.type = CheckType(str(self.type).lower())
        except ValueError:
            # Leave custom types available to applications registering their
            # own collector.
            self.type = str(self.type).lower()

    @property
    def check_type(self) -> str:
        """Return the normalized collector key."""

        return self.type.value if isinstance(self.type, CheckType) else str(self.type)


@dataclass
class HealthObservation:
    """One collector result."""

    check_name: str
    status: HealthStatus
    message: str = ""
    observed_value: float | None = None
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Alias useful to callers that use ``name`` for all signals."""

        return self.check_name

    @property
    def value(self) -> float | None:
        return self.observed_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "status": self.status.value,
            "message": self.message,
            "observed_value": self.observed_value,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class HealthState:
    """Mutable state accumulated by the controller for one check."""

    name: str
    spec: HealthCheckSpec
    status: HealthStatus = HealthStatus.OK
    previous_status: HealthStatus | None = None
    message: str = "Not checked yet"
    observed_value: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    last_checked: float | None = None
    last_ok: float | None = None
    last_transition: float | None = None
    consecutive_failures: int = 0
    consecutive_ok: int = 0
    recovery_attempts: int = 0
    last_recovery: float | None = None
    root_cause: str | None = None
    derived: bool = False

    @property
    def stale_age(self) -> float | None:
        value = self.details.get("stale_age")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def current_hz(self) -> float | None:
        value = self.details.get("current_hz")
        return float(value) if isinstance(value, (int, float)) else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["previous_status"] = (
            self.previous_status.value if self.previous_status is not None else None
        )
        spec = data["spec"]
        if isinstance(spec.get("type"), CheckType):
            spec["type"] = spec["type"].value
        return data


@dataclass
class ActionSpec:
    """A configured recovery operation."""

    name: str
    type: ActionType | str
    target: str | None = None
    command: str | list[str] | None = None
    unsafe_shell: bool = False
    timeout: float = 30.0
    cooldown: float | None = None
    max_attempts: int | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.type, ActionType):
            return
        try:
            self.type = ActionType(str(self.type).lower())
        except ValueError:
            self.type = str(self.type).lower()

    @property
    def action_type(self) -> str:
        return self.type.value if isinstance(self.type, ActionType) else str(self.type)


@dataclass
class PolicyRule:
    """A declarative condition and the recovery action it selects."""

    name: str
    condition: str | dict[str, Any] | list[Any]
    action: ActionSpec | str | dict[str, Any]
    cooldown: float = 30.0
    max_attempts: int | None = None
    enabled: bool = True
    repeat: bool = False
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class RecoveryResult:
    """Outcome of one recovery attempt."""

    action: str
    success: bool
    message: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryDecision:
    """Policy engine output consumed by the recovery manager."""

    policy_name: str
    source: str
    action: ActionSpec
    reason: str
    created_at: float = field(default_factory=time.time)
    dedupe_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """Structured event emitted by Aegis."""

    kind: str
    source: str
    message: str
    status: HealthStatus | None = None
    previous_status: HealthStatus | None = None
    severity: str = "info"
    root_cause: str | None = None
    affected: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.status is not None:
            result["status"] = self.status.value
        if self.previous_status is not None:
            result["previous_status"] = self.previous_status.value
        return result


@dataclass
class TickResult:
    """Result of one Observe → Detect → Decide → Recover cycle."""

    observations: dict[str, HealthObservation]
    states: dict[str, HealthState]
    decisions: list[RecoveryDecision] = field(default_factory=list)
    recoveries: list[RecoveryResult] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def changes(self) -> list[tuple[str, HealthStatus, HealthStatus]]:
        changes: list[tuple[str, HealthStatus, HealthStatus]] = []
        for name, state in self.states.items():
            if state.previous_status is not None and state.previous_status != state.status:
                changes.append((name, state.previous_status, state.status))
        return changes
