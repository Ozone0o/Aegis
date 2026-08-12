"""空恢复策略：默认不执行任何恢复操作。"""

from __future__ import annotations

from .base import RecoveryStrategy


class NoOpRecovery(RecoveryStrategy):
    """默认恢复策略，不执行任何操作。"""

    async def recover(self, device_name: str) -> bool:
        # 空恢复：不执行任何操作
        return False
