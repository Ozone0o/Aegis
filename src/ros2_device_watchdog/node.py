"""ROS2 Watchdog Node 入口。"""

from __future__ import annotations

import logging
from pathlib import Path

import rclpy
import rclpy.node
from diagnostic_updater import Updater

from .config import load_config
from .diagnostics import DiagnosticsAdapter
from .monitor import WatchdogMonitor
from .recovery import NoOpRecovery

logger = logging.getLogger(__name__)


class WatchdogNode(rclpy.node.Node):
    """ROS2 Watchdog 节点。

    加载 YAML 配置，创建 WatchdogMonitor，
    周期性地评估设备状态并发布 diagnostics。
    """

    def __init__(self, config_path: str | Path) -> None:
        super().__init__("device_watchdog")

        # 加载配置
        self.config_path = Path(config_path)
        self.devices_config = load_config(self.config_path)
        self.get_logger().info(f"加载配置: {self.config_path}")

        # 创建监控管理器
        self.monitor = WatchdogMonitor()
        for name, config in self.devices_config.items():
            self.monitor.add_device(name, config)

        # 注入节点列表提供者
        self.monitor.set_node_list_provider(lambda: self.list_nodes())

        # 注册默认恢复策略
        recovery = NoOpRecovery()

        async def _recover(device_name: str) -> bool:
            return await recovery.recover(device_name)

        self.monitor.set_recovery_callback(_recover)
        self.get_logger().info("恢复策略: NoOp (不执行自动恢复)")

        # 创建 diagnostics 适配器
        self.diag_adapter = DiagnosticsAdapter(self.monitor)
        self.updater: Updater = Updater(self)
        self.updater.setHardwareID("watchdog")
        self.diag_adapter.set_updater(self.updater)

        # 周期性检查 timer (1Hz)
        self.check_timer = self.create_timer(1.0, self._check_timer_callback)

        self.get_logger().info(f"开始监控 {len(self.devices_config)} 个设备")

    def _check_timer_callback(self) -> None:
        """Timer 回调：执行一次 tick 并更新 diagnostics。"""
        changes = self.monitor.tick()
        if changes:
            for name, old_s, new_s in changes:
                self.get_logger().info(f"状态变化: {name}: {old_s.name} -> {new_s.name}")

        # 更新 diagnostics
        self.diag_adapter.update()

    def get_monitor(self) -> WatchdogMonitor:
        """暴露 monitor 供外部访问。"""
        return self.monitor


def main(args: list[str] | None = None) -> None:
    """入口函数。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    rclpy.init(args=args)
    node = WatchdogNode(config_path="config/example.yaml")

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass
    finally:
        try:
            node.destroy_node()
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
