"""恢复策略模块。"""

from .base import RecoveryStrategy
from .noop import NoOpRecovery

__all__ = ["RecoveryStrategy", "NoOpRecovery"]
