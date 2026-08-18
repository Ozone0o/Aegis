"""Process liveness collector."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from ..models import HealthCheckSpec, HealthObservation, HealthStatus
from .base import HealthCollector


class ProcessCollector(HealthCollector):
    kind = "process"

    def __init__(self, process_probe: Callable[[HealthCheckSpec], bool] | None = None) -> None:
        self.process_probe = process_probe

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _name_exists(name: str) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", name],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        try:
            if self.process_probe is not None:
                alive = bool(self.process_probe(check))
            else:
                pid = check.params.get("pid")
                if pid is None and check.target and str(check.target).isdigit():
                    pid = int(check.target)
                if pid is not None:
                    alive = self._pid_exists(int(pid))
                else:
                    name = check.params.get("process_name", check.target)
                    alive = bool(name) and self._name_exists(str(name))
        except (TypeError, ValueError) as exc:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"invalid process target: {exc}",
                details={"collector": self.kind},
            )
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK if alive else HealthStatus.ERROR,
            message="process is alive" if alive else "process is not running",
            details={"collector": self.kind, "target": check.target},
        )
