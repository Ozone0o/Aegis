"""核心监控逻辑：管理所有设备，周期评估状态。

核心判断逻辑与 ROS2 Node 分离，方便单元测试。
"""

from __future__ import annotations

import logging
import time

from .models import DeviceConfig, DeviceState, DeviceStatus
from .monitors import NodeMonitor, TopicMonitor

logger = logging.getLogger(__name__)

# 防抖参数：从 ERROR 恢复到 OK 需要连续确认次数
HYSTERSIS_OK_CONFIRM = 3
# 恢复冷却期（秒）：两次恢复之间的最小间隔
DEFAULT_RECOVERY_COOLDOWN = 30.0
# 最大连续重试次数
DEFAULT_MAX_RETRIES = 3


class WatchdogMonitor:
    """设备监控管理器。

    周期调用 tick() 来更新所有设备的状态。
    只在状态变化时输出日志。
    """

    def __init__(
        self,
        cooldown: float = DEFAULT_RECOVERY_COOLDOWN,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.cooldown = cooldown
        self.max_retries = max_retries
        self._devices: dict[str, DeviceState] = {}
        self._topic_monitors: dict[str, TopicMonitor] = {}
        self._node_monitors: dict[str, NodeMonitor] = {}
        self._previous_status: dict[str, DeviceStatus] = {}
        self._recovery_callback = None  # 被外部设置

    def add_device(self, name: str, config: DeviceConfig) -> None:
        """添加一个被监控的设备。"""
        state = DeviceState(name=name, config=config)
        self._devices[name] = state
        self._previous_status[name] = state.status

        if config.type == "topic":
            monitor = TopicMonitor(topic=config.topic)
            self._topic_monitors[name] = monitor
        elif config.type == "node":
            monitor = NodeMonitor(node_name=config.node_name)
            self._node_monitors[name] = monitor

    def set_node_list_provider(self, provider) -> None:
        """为所有 NodeMonitor 注入节点列表提供者。"""
        for monitor in self._node_monitors.values():
            monitor.set_node_list_provider(provider)

    def set_recovery_callback(self, callback) -> None:
        """设置恢复回调。"""
        self._recovery_callback = callback

    def _on_topic_message(self, name: str) -> None:
        """Topic 消息到达时的回调。"""
        if name in self._topic_monitors:
            self._topic_monitors[name].on_message()

    def tick(self) -> list[tuple[str, DeviceStatus, DeviceStatus]]:
        """执行一次Tick，评估所有设备状态。

        Returns:
            状态变化列表 [(设备名, 旧状态, 新状态), ...]
        """
        changes: list[tuple[str, DeviceStatus, DeviceStatus]] = []

        for name, state in self._devices.items():
            old_status = state.status
            new_status = self._evaluate(name, state)

            if new_status != old_status:
                changes.append((name, old_status, new_status))
                logger.info("%s: %s -> %s", name, old_status.name, new_status.name)
                self._on_status_changed(name, old_status, new_status)

            self._previous_status[name] = new_status
            state.status = new_status

        return changes

    def _evaluate(self, name: str, state: DeviceState) -> DeviceStatus:
        """评估单个设备的状态。

        规则：
        - topic/node 不存在 -> ERROR
        - 消息过期 > stale_timeout -> WARN
        - Hz < expected_rate * 0.7 -> WARN
        - 连续失败超过最大重试次数且未恢复 -> RECOVERING
        - 否则 -> OK
        """
        config = state.config

        # 检查设备是否存在
        if config.type == "topic":
            monitor = self._topic_monitors.get(name)
            if not monitor or not monitor.is_active():
                return DeviceStatus.ERROR
            state.last_message_age = monitor.get_last_message_age()
            state.current_hz = monitor.get_current_hz()
        elif config.type == "node":
            monitor = self._node_monitors.get(name)
            if not monitor or not monitor.is_exists():
                return DeviceStatus.ERROR

        # 检查消息新鲜度
        if state.last_message_age > config.stale_timeout:
            return DeviceStatus.WARN

        # 检查频率
        threshold = config.expected_rate * 0.7
        if state.current_hz > 0 and state.current_hz < threshold:
            return DeviceStatus.WARN

        # 检查连续失败（用于判断是否需要进入 RECOVERING）
        if state.consecutive_failures >= self.max_retries:
            return DeviceStatus.RECOVERING

        # 防抖：从 ERROR 恢复需要连续确认
        if state.status == DeviceStatus.ERROR:
            state.consecutive_ok_count += 1
            if state.consecutive_ok_count >= HYSTERSIS_OK_CONFIRM:
                return DeviceStatus.OK
            return DeviceStatus.ERROR

        # OK
        state.consecutive_ok_count = 0
        state.consecutive_failures = 0
        return DeviceStatus.OK

    def _on_status_changed(
        self,
        name: str,
        old_status: DeviceStatus,
        new_status: DeviceStatus,
    ) -> None:
        """状态变化时的额外处理。"""
        if new_status == DeviceStatus.RECOVERING and self._recovery_callback:
            self._try_recover(name)

    def _try_recover(self, name: str) -> None:
        """尝试恢复设备，受 cooldown 和 max_retries 限制。"""
        state = self._devices.get(name)
        if not state:
            return

        now = time.monotonic()
        elapsed = now - state.last_recovery_time

        # 检查冷却期
        if elapsed < self.cooldown:
            logger.debug(
                "%s: 恢复冷却中，剩余 %.1fs",
                name,
                self.cooldown - elapsed,
            )
            return

        # 检查最大重试次数
        if state.consecutive_failures >= self.max_retries:
            logger.warning("%s: 已达到最大重试次数 %d，跳过恢复", name, self.max_retries)
            return

        # 执行恢复
        state.last_recovery_time = now
        try:
            self._recovery_callback(name)
        except Exception:
            logger.exception("%s: 恢复回调执行失败", name)

    def get_states(self) -> dict[str, DeviceState]:
        """返回所有设备的当前状态。"""
        return dict(self._devices)

    def get_state(self, name: str) -> DeviceState | None:
        """返回指定设备的状态。"""
        return self._devices.get(name)
