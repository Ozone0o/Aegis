"""ROS2 diagnostics 适配器。

将 WatchdogMonitor 的状态映射到 ROS2 diagnostics 体系。
"""

from __future__ import annotations

import logging

from diagnostic_updater import DiagnosticStatusWrapper

from .models import DeviceStatus
from .monitor import WatchdogMonitor

logger = logging.getLogger(__name__)


class DiagnosticsAdapter:
    """ROS2 diagnostics 适配器。

    将 WatchdogMonitor 的设备状态转换为 diagnostics 格式。
    """

    def __init__(self, monitor: WatchdogMonitor) -> None:
        self.monitor = monitor
        self._updater = None
        self._tasks = {}

    def set_updater(self, updater) -> None:
        """设置 diagnostic_updater 实例。"""
        self._updater = updater

    def update(self) -> None:
        """更新 diagnostics 状态。被 node.py 的周期性 timer 调用。"""
        if not self._updater:
            return

        states = self.monitor.get_states()
        for name, state in states.items():
            status_level = self._status_to_diag_level(state.status)
            message = self._make_diag_message(name, state)

            def make_callback(n, msg, level, st):
                def callback(status):
                    status.level = level
                    status.name = n
                    status.message = msg
                    status.hardware_id = "watchdog"
                    status.add("Device", n)
                    status.add("Status", st.status.name)
                    status.add("Last Message Age (s)", f"{st.last_message_age:.3f}")
                    status.add("Current Hz", f"{st.current_hz:.2f}")
                    status.add("Expected Rate", f"{st.config.expected_rate:.2f}")
                    return status
                return callback

            callback = make_callback(name, message, status_level, state)

            if name in self._tasks:
                self._updater.removeByName(name)
            self._updater.add(name, callback)
            self._tasks[name] = callback

        self._updater.force_update()

    @staticmethod
    def _status_to_diag_level(status: DeviceStatus) -> bytes:
        """将 DeviceStatus 映射到 diagnostics 状态码。

        0 = OK, 1 = Warn, 2 = Error (as bytes for ROS2)
        """
        mapping = {
            DeviceStatus.OK: b'\x00',
            DeviceStatus.WARN: b'\x01',
            DeviceStatus.ERROR: b'\x02',
            DeviceStatus.RECOVERING: b'\x01',
        }
        return mapping.get(status, b'\x02')

    @staticmethod
    def _make_diag_message(name: str, state) -> str:
        """生成 diagnostics 消息字符串。"""
        return f"{name}: {state.status.name} (age={state.last_message_age:.3f}s)"
