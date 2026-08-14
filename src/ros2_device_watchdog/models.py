"""数据模型：状态枚举、设备配置、设备状态。"""

from __future__ import annotations

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
