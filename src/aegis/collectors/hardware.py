"""Hardware health collector.

Hardware access is vendor-specific, so the collector accepts an injected
probe or a bounded command check.  This keeps the Aegis core generic while
still providing a useful default for devices exposing a command-line health
probe.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from ..models import HealthCheckSpec, HealthObservation, HealthStatus
from .base import HealthCollector


class HardwareCollector(HealthCollector):
    kind = "hardware"

    def __init__(self, hardware_probe: Callable[[HealthCheckSpec], object] | None = None) -> None:
        self.hardware_probe = hardware_probe

    def collect(self, check: HealthCheckSpec) -> HealthObservation:
        command = check.params.get("command")
        try:
            if self.hardware_probe is not None:
                value = self.hardware_probe(check)
                if isinstance(value, HealthObservation):
                    return value
                ok = bool(value)
            elif command:
                unsafe_shell = bool(check.params.get("unsafe_shell", False))
                if isinstance(command, str) and not unsafe_shell:
                    return HealthObservation(
                        check_name=check.name,
                        status=HealthStatus.ERROR,
                        message=(
                            "string hardware probe commands are disabled by default; "
                            "use an argument list or explicitly set unsafe_shell=true"
                        ),
                        details={"collector": self.kind, "unsafe_shell": False},
                    )
                if unsafe_shell and not isinstance(command, str):
                    return HealthObservation(
                        check_name=check.name,
                        status=HealthStatus.ERROR,
                        message="unsafe_shell=true requires a single command string",
                        details={"collector": self.kind, "unsafe_shell": True},
                    )
                if not isinstance(command, (str, list)):
                    return HealthObservation(
                        check_name=check.name,
                        status=HealthStatus.ERROR,
                        message="hardware probe command must be an argument list or string",
                        details={"collector": self.kind, "unsafe_shell": False},
                    )
                if isinstance(command, list) and (
                    not command or not all(isinstance(item, str) for item in command)
                ):
                    return HealthObservation(
                        check_name=check.name,
                        status=HealthStatus.ERROR,
                        message="hardware probe argument lists must contain strings",
                        details={"collector": self.kind, "unsafe_shell": False},
                    )
                result = subprocess.run(
                    command,
                    shell=unsafe_shell,
                    capture_output=True,
                    text=True,
                    timeout=check.timeout,
                    check=False,
                )
                ok = result.returncode == 0
                details = {
                    "collector": self.kind,
                    "unsafe_shell": unsafe_shell,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-500:],
                    "stderr": result.stderr[-500:],
                }
                return HealthObservation(
                    check_name=check.name,
                    status=HealthStatus.OK if ok else HealthStatus.ERROR,
                    message="hardware probe passed" if ok else "hardware probe failed",
                    details=details,
                )
            else:
                return self.unavailable(check, "hardware check requires a probe or command")
        except subprocess.TimeoutExpired:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"hardware probe timed out after {check.timeout:.1f}s",
                details={"collector": self.kind, "timeout": check.timeout},
            )
        except Exception as exc:
            return HealthObservation(
                check_name=check.name,
                status=HealthStatus.ERROR,
                message=f"hardware probe failed: {exc}",
                details={"collector": self.kind},
            )
        return HealthObservation(
            check_name=check.name,
            status=HealthStatus.OK if ok else HealthStatus.ERROR,
            message="hardware probe passed" if ok else "hardware probe failed",
            details={"collector": self.kind},
        )
