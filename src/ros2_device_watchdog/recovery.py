"""默认恢复策略：什么都不做。"""

from __future__ import annotations


class NoOpRecovery:
    """不执行任何恢复操作的占位策略。"""

    async def recover(self, device_name: str) -> bool:
        """记录恢复尝试但不执行实际操作。"""
        print(f"[NoOpRecovery] 跳过设备 {device_name} 的恢复")
        return False
