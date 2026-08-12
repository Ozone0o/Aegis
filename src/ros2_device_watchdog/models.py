"""数据模型：状态枚举、设备配置、设备状态。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto


class DeviceStatus(Enum):
    """设备状态枚举。"""
    OK = auto()
    WARN = auto()
    ERROR = auto()
    RECOVERING = auto()


@dataclass
class DeviceConfig:
    """单个设备的监控配置。"""
    topic: str | None = None
    node_name: str | None = None
    stale_timeout: float = 1.0
    expected_rate: float = 10.0
    type: str = "topic"  # "topic" | "node"


@dataclass
class DeviceState:
    """单个设备的运行时状态。"""
    name: str
    config: DeviceConfig
    status: DeviceStatus = DeviceStatus.OK
    last_message_age: float = 0.0
    current_hz: float = 0.0
    consecutive_ok_count: int = 0  # 防抖计数器
    consecutive_failures: int = 0
    last_recovery_time: float = 0.0


class TopicMonitor:
    """监控单个 Topic 的消息新鲜度和频率。"""

    def __init__(self, topic: str, window: float = 2.0) -> None:
        self.topic = topic
        self.window = window
        self.timestamps: deque[float] = deque(maxlen=1000)
        self._callback = None  # 被 rclpy 订阅回调设置

    def set_callback(self, callback) -> None:
        """设置消息到达时的回调。"""
        self._callback = callback

    def on_message(self) -> None:
        """消息到达时调用。"""
        now = time.monotonic()
        self.timestamps.append(now)
        if self._callback:
            self._callback()

    def get_current_hz(self) -> float:
        """基于时间窗口计算当前 Hz。"""
        if len(self.timestamps) < 2:
            return 0.0
        now = time.monotonic()
        # 过滤掉超出窗口的记录
        while self.timestamps and (now - self.timestamps[0]) > self.window:
            self.timestamps.popleft()
        if len(self.timestamps) < 2:
            return 0.0
        duration = now - self.timestamps[0]
        if duration <= 0:
            return 0.0
        return (len(self.timestamps) - 1) / duration

    def get_last_message_age(self) -> float:
        """返回距最近一次消息的经过时间。"""
        if not self.timestamps:
            return float('inf')
        return time.monotonic() - self.timestamps[-1]

    def is_active(self) -> bool:
        """判断 Topic 是否有消息到达。"""
        return len(self.timestamps) > 0


class NodeMonitor:
    """监控单个 Node 是否存在。"""

    def __init__(self, node_name: str) -> None:
        self.node_name = node_name
        self._node_list_provider = None  # 被注入的 node.list_nodes 替代

    def set_node_list_provider(self, provider) -> None:
        """设置提供节点列表的函数。"""
        self._node_list_provider = provider

    def is_exists(self) -> bool:
        """检查 Node 是否存在。"""
        if not self._node_list_provider:
            return False
        try:
            nodes = self._node_list_provider()
            # list_nodes 返回 (name, namespace) 元组列表
            return any(n[0] == self.node_name for n in nodes)
        except Exception:
            return False
