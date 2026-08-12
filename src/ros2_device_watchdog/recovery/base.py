"""恢复策略抽象基类。"""

from __future__ import annotations

import abc


class RecoveryStrategy(abc.ABC):
    """设备恢复策略接口。

    用户可以继承此类实现自己的恢复逻辑。
    第一版不提供危险操作的默认实现。
    """

    @abc.abstractmethod
    async def recover(self, device_name: str) -> bool:
        """尝试恢复指定设备。

        Args:
            device_name: 设备名称。

        Returns:
            True 表示恢复成功，False 表示恢复失败或跳过。
        """
        ...
