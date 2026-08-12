"""ROS2 diagnostics 适配器。

将 WatchdogMonitor 的状态映射到 ROS2 diagnostics 体系。
"""

from __future__ import annotations

import logging

from .models import DeviceStatus
from .monitor import WatchdogMonitor

logger = logging.getLogger(__name__)


class DiagnosticsAdapter:
    """ROS2 diagnostics 适配器。

    将 WatchdogMonitor 的设备状态转换为 diagnostics 格式。
    """

    def __init__(self, monitor: WatchdogMonitor) -> None:
        self.monitor = monitor
        self._updater = None  # 被 node.py 注入 diagnostic_updater

    def set_updater(self, updater) -> None:
        """设置 diagnostic_updater 实例。"""
        self._updater = updater

    def update(self) -> None:
        """更新 diagnostics 状态。被 node.py 的周期性 timer 调用。"""
        if not self._updater:
            return

        states = self.monitor.get_states()
        for name, state in states.items():
            self._updater.push_fn(
                self._make_diag_callback(name, state),
                name,
            )

    def _make_diag_callback(self, name: str, state) -> callable:
        """生成单个设备的 diagnostics 回调。"""
        status_level = self._status_to_diag_level(state.status)
        message = self._make_diag_message(name, state)

        def callback(diag):
            diag.state = status_level
            diag.message = message
            # 添加关键指标
            diag.add("Device", name)
            diag.add("Status", state.status.name)
            diag.add("Last Message Age (s)", f"{state.last_message_age:.3f}")
            diag.add("Current Hz", f"{state.current_hz:.2f}")
            diag.add("Expected Rate", f"{state.config.expected_rate:.2f}")

        return callback

    @staticmethod
    def _status_to_diag_level(status: DeviceStatus) -> int:
        """将 DeviceStatus 映射到 diagnostics 状态码。

        0 = OK, 1 = Warn, 2 = Error
        """
        mapping = {
            DeviceStatus.OK: 0,
            DeviceStatus.WARN: 1,
            DeviceStatus.ERROR: 2,
            DeviceStatus.RECOVERING: 1,
        }
        return mapping.get(status, 2)

    @staticmethod
    def _make_diag_message(name: str, state) -> str:
        """生成 diagnostics 消息字符串。"""
        return f"{name}: {state.status.name} (age={state.last_message_age:.3f}s)"
