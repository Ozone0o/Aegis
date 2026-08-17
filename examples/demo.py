"""Demo: 演示 ros2-device-watchdog 的配置加载和设备校验。

不依赖真实 ROS2 环境，直接加载配置文件并打印设备状态。

用法:
    python examples/demo.py
"""

from __future__ import annotations

import pathlib
import sys

from ros2_device_watchdog.config import load_config, validate_config


def main() -> None:
    config_path = pathlib.Path(__file__).parent.parent / "config" / "example.yaml"
    print("ros2-device-watchdog 配置加载演示")
    print("=" * 50)

    try:
        configs = load_config(config_path)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n加载了 {len(configs)} 个设备配置:\n")
    for name, cfg in configs.items():
        print(f"  设备: {name}")
        print(f"    类型: {cfg.type}")
        if cfg.topic:
            print(f"    Topic: {cfg.topic}")
        if cfg.node_name:
            print(f"    Node: {cfg.node_name}")
        print(f"    超时阈值: {cfg.stale_timeout}s")
        print(f"    预期频率: {cfg.expected_rate} Hz")
        print()


if __name__ == "__main__":
    main()
