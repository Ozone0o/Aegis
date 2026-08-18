"""Host resource collector using standard-library probes where possible."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable

from ..models import HealthCheckSpec, HealthObservation, HealthStatus
from .base import HealthCollector


class ResourceCollector(HealthCollector):
    kind = "resource"

    def __init__(self, value_provider: Callable[[HealthCheckSpec], float] | None = None) -> None:
        self.value_provider = value_provider

    @staticmethod
    def _memory_percent() -> float | None:
        try:
            values: dict[str, float] = {}
            with open("/proc/meminfo", encoding="utf-8") as stream:
                for line in stream:
                    key, value = line.split(":", 1)
                    values[key] = float(value.strip().split()[0])
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if total and available is not None:
                return (total - available) / total * 100.0
        except (OSError, ValueError):
            return None
        return None

    def _read_value(self, check: HealthCheckSpec) -> tuple[float | None, str]:
        if self.value_provider is not None:
            resource = str(check.params.get("resource", check.target or "custom"))
            return float(self.value_provider(check)), resource
        resource = str(check.params.get("resource", check.target or "load")).lower()
        if resource in {"cpu", "cpu_percent", "load", "load1", "cpu_load"}:
            load = os.getloadavg()[0]
            return load / max(1, os.cpu_count() or 1) * 100.0, resource
        if resource in {"memory", "memory_percent", "ram"}:
            return self._memory_percent(), "memory_percent"
        if resource in {"disk", "disk_percent", "storage"}:
            path = str(check.params.get("path", "/"))
            usage = shutil.disk_usage(path)
            return usage.used / usage.total * 100.0, "disk_percent"
        return None, resource

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        try:
            value, resource = self._read_value(check)
        except (AttributeError, OSError, ValueError) as exc:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"unable to read resource: {exc}",
                details={"collector": self.kind},
            )
        if value is None:
            return self.unavailable(check, f"resource '{resource}' is unavailable")

        warning = check.warning_threshold
        error = check.error_threshold
        if error is not None and value >= error:
            status = HealthStatus.ERROR
            message = f"{resource} is high ({value:.2f} >= {error:.2f})"
        elif warning is not None and value >= warning:
            status = HealthStatus.WARNING
            message = f"{resource} is elevated ({value:.2f} >= {warning:.2f})"
        else:
            status = HealthStatus.OK
            message = f"{resource} is within limits ({value:.2f})"
        return HealthObservation(
            check_name=check.name,
            status=status,
            message=message,
            observed_value=value,
            details={"collector": self.kind, "resource": resource},
            timestamp=time.time(),
        )
