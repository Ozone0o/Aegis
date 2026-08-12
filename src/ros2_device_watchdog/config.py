"""YAML 配置加载和验证。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import DeviceConfig

logger = logging.getLogger(__name__)

# 默认配置值
_DEFAULT_STALE_TIMEOUT = 1.0
_DEFAULT_EXPECTED_RATE = 10.0
_DEFAULT_TYPE = "topic"


def load_config(path: str | Path) -> dict[str, DeviceConfig]:
    """加载并解析 YAML 配置文件，返回设备配置字典。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if not data or 'devices' not in data:
        raise ValueError("YAML 配置缺少 'devices' 字段")

    return validate_config(data['devices'])


def validate_config(devices_data: dict) -> dict[str, DeviceConfig]:
    """校验设备配置数据，返回合法的 DeviceConfig 字典。"""
    configs: dict[str, DeviceConfig] = {}

    for name, cfg_data in devices_data.items():
        if not isinstance(cfg_data, dict):
            logger.warning("设备 %s 的配置格式错误，跳过", name)
            continue

        # 确定设备类型
        device_type = cfg_data.get('type', _DEFAULT_TYPE)
        topic = cfg_data.get('topic')
        node_name = cfg_data.get('node_name')

        # topic 类型必须指定 topic
        if device_type == "topic":
            if not topic:
                logger.warning("设备 %s (type=topic) 缺少 topic 字段，跳过", name)
                continue
        elif device_type == "node":
            if not node_name:
                logger.warning("设备 %s (type=node) 缺少 node_name 字段，跳过", name)
                continue
        else:
            logger.warning("设备 %s 的 type=%s 不支持，跳过", name, device_type)
            continue

        configs[name] = DeviceConfig(
            topic=topic,
            node_name=node_name,
            stale_timeout=cfg_data.get('stale_timeout', _DEFAULT_STALE_TIMEOUT),
            expected_rate=cfg_data.get('expected_rate', _DEFAULT_EXPECTED_RATE),
            type=device_type,
        )

    if not configs:
        raise ValueError("校验后没有合法的设备配置")

    logger.info("加载了 %d 个设备配置", len(configs))
    return configs
