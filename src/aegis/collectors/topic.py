"""Topic freshness and frequency collector."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from ..models import HealthCheckSpec, HealthObservation, HealthStatus
from .base import HealthCollector


class TopicProbe:
    """Small, ROS-independent topic probe.

    A ROS adapter only needs to call :meth:`on_message` from a subscription
    callback.  Tests and non-ROS integrations can do the same.
    """

    def __init__(self, window: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.window = max(0.1, float(window))
        self.clock = clock
        self.timestamps: deque[float] = deque(maxlen=2000)

    def on_message(self, timestamp: float | None = None) -> None:
        self.timestamps.append(self.clock() if timestamp is None else timestamp)

    def _trim(self, now: float) -> None:
        while self.timestamps and now - self.timestamps[0] > self.window:
            self.timestamps.popleft()

    def last_message_age(self, now: float | None = None) -> float:
        if not self.timestamps:
            return math.inf
        current = self.clock() if now is None else now
        return max(0.0, current - self.timestamps[-1])

    def current_hz(self, now: float | None = None) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        current = self.clock() if now is None else now
        self._trim(current)
        if len(self.timestamps) < 2:
            return 0.0
        duration = current - self.timestamps[0]
        return (len(self.timestamps) - 1) / duration if duration > 0 else 0.0

    @property
    def active(self) -> bool:
        return bool(self.timestamps)


class TopicCollector(HealthCollector):
    kind = "topic"

    def __init__(
        self,
        probes: dict[str, TopicProbe] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.probes = probes if probes is not None else {}
        self.clock = clock

    def register(self, name: str, probe: TopicProbe | None = None) -> TopicProbe:
        result = probe or TopicProbe(clock=self.clock)
        self.probes[name] = result
        return result

    def on_message(self, name: str, timestamp: float | None = None) -> None:
        self.probes.setdefault(name, TopicProbe(clock=self.clock)).on_message(timestamp)

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        probe = self.probes.get(check.name)
        if probe is None or not probe.active:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"topic {check.target or ''} has not produced a message",
                details={"collector": self.kind, "stale_age": math.inf, "current_hz": 0.0},
            )

        now = self.clock()
        age = probe.last_message_age(now)
        hz = probe.current_hz(now)
        details = {
            "collector": self.kind,
            "topic": check.target,
            "stale_age": age,
            "current_hz": hz,
        }
        if age > check.stale_timeout:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.WARNING,
                message=f"topic is stale ({age:.3f}s > {check.stale_timeout:.3f}s)",
                observed_value=age,
                details=details,
            )
        if check.expected_rate and hz > 0 and hz < check.expected_rate * 0.7:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.WARNING,
                message=f"topic rate is low ({hz:.2f}Hz < {check.expected_rate * 0.7:.2f}Hz)",
                observed_value=hz,
                details=details,
            )
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK,
            message="topic is fresh",
            observed_value=hz,
            details=details,
        )
