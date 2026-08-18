"""Demo: load a canonical Aegis configuration and run one health cycle.

不依赖真实 ROS2 环境，直接加载配置文件并打印设备状态。

用法:
    python examples/demo.py
"""

from __future__ import annotations

import pathlib
import sys

from aegis.config import load_config
from aegis.core import AegisCore


def main() -> None:
    config_path = pathlib.Path(__file__).parent.parent / "config" / "example.yaml"
    print("Aegis 配置加载演示")
    print("=" * 50)

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    core = AegisCore(config)
    result = core.tick()
    print(f"\n加载了 {len(config.checks)} 项健康检查:\n")
    for name, state in result.states.items():
        print(f"  {name}: {state.status.value} — {state.message}")
        print()


if __name__ == "__main__":
    main()
