"""Collector interfaces and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

from ..models import HealthCheckSpec, HealthObservation, HealthStatus

logger = logging.getLogger(__name__)


class HealthCollector(ABC):
    """A source-specific implementation of the Observe phase."""

    kind: str = "custom"

    @abstractmethod
    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        """Return the current health observation for ``check``."""

    def unavailable(self, check: HealthCheckSpec, message: str) -> HealthObservation:
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.ERROR,
            message=message,
            details={"collector": self.kind},
        )


class CallableCollector(HealthCollector):
    """Adapter for applications that want to provide a custom probe."""

    kind = "custom"

    def __init__(self, callback: Callable[[HealthCheckSpec], Any]) -> None:
        self.callback = callback

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        try:
            value = self.callback(check)
        except Exception as exc:
            logger.exception("Custom Aegis collector failed for %s", check.name)
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"collector failed: {exc}",
                details={"collector": self.kind},
            )
        if isinstance(value, HealthObservation):
            return value
        if isinstance(value, bool):
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.OK if value else HealthStatus.ERROR,
                message="probe passed" if value else "probe failed",
            )
        if isinstance(value, dict):
            status = value.get("status", HealthStatus.OK)
            if not isinstance(status, HealthStatus):
                status = HealthStatus(str(status).upper())
            return HealthObservation(
                check_name=check.name,
                status=status,
                message=str(value.get("message", "")),
                observed_value=value.get("observed_value", value.get("value")),
                details=dict(value.get("details", {})),
            )
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK if value else HealthStatus.ERROR,
            message="probe passed" if value else "probe failed",
            observed_value=float(value) if isinstance(value, (int, float)) else None,
        )


class CollectorRegistry:
    """Map health check types to collectors."""

    def __init__(self, collectors: Iterable[HealthCollector] | None = None) -> None:
        self._collectors: dict[str, HealthCollector] = {}
        for collector in collectors or ():
            self.register(collector.kind, collector)

    def register(self, kind: str, collector: HealthCollector) -> None:
        self._collectors[str(kind).lower()] = collector

    def get(self, kind: str) -> HealthCollector | None:
        return self._collectors.get(str(kind).lower())

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        collector = self.get(check.check_type)
        if collector is None:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"no collector registered for type '{check.check_type}'",
                details={"collector": check.check_type},
            )
        try:
            observation = collector.collect(check)
        except Exception as exc:
            logger.exception("Collector %s failed for %s", check.check_type, check.name)
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"collector failed: {exc}",
                details={"collector": check.check_type},
            )
        if observation.check_name != check.name:
            observation.check_name = check.name
        return observation
