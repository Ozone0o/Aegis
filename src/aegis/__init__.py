"""Aegis: runtime protection and recovery for ROS 2 robots."""

from .config import AegisConfig, RecoverySettings, load_config
from .core import AegisCore
from .dependencies import DependencyCycleError, DependencyGraph
from .events import EventBus, JsonEventStore, JsonStateStore
from .models import (
    ActionSpec,
    ActionType,
    CheckType,
    Event,
    HealthCheckSpec,
    HealthObservation,
    HealthState,
    HealthStatus,
    PolicyRule,
    RecoveryDecision,
    RecoveryResult,
    TickResult,
)
from .policy import ConditionEvaluator, PolicyEngine, parse_duration
from .recovery import CommandExecutor, NoOpRecovery, RecoveryManager

__version__ = "0.2.0"

__all__ = [
    "ActionSpec",
    "ActionType",
    "AegisConfig",
    "AegisCore",
    "CheckType",
    "CommandExecutor",
    "ConditionEvaluator",
    "DependencyCycleError",
    "DependencyGraph",
    "Event",
    "EventBus",
    "HealthCheckSpec",
    "HealthObservation",
    "HealthState",
    "HealthStatus",
    "JsonEventStore",
    "JsonStateStore",
    "NoOpRecovery",
    "PolicyEngine",
    "PolicyRule",
    "RecoveryDecision",
    "RecoveryManager",
    "RecoveryResult",
    "RecoverySettings",
    "TickResult",
    "load_config",
    "parse_duration",
]
