"""Optional ROS 2 adapter for the Aegis controller."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AegisConfig, load_config
from .core import AegisCore

try:  # ROS is optional for the core, policy, and offline CLI commands.
    import rclpy
    from rclpy.node import Node
except ImportError:  # pragma: no cover - exercised on non-ROS CI workers.
    rclpy = None
    Node = object  # type: ignore[misc,assignment]


if rclpy is not None:

    class AegisNode(Node):
        """ROS 2 graph adapter that feeds observations into AegisCore."""

        def __init__(self, config: AegisConfig | str | Path) -> None:
            super().__init__("aegis_runtime")
            self.config = load_config(config) if isinstance(config, (str, Path)) else config
            self.core = AegisCore(self.config)
            self.core.recovery_manager.notifier = self._notify_operator
            self.core.recovery_manager.shutdown_callback = self._safe_shutdown
            self.core.set_node_list_provider(self._node_list)
            self._topic_subscriptions: dict[str, Any] = {}
            self._attach_topic_subscriptions()
            self._timer = self.create_timer(self.config.interval, self._tick)
            self.get_logger().info(
                f"Aegis started: {len(self.config.checks)} checks, "
                f"interval={self.config.interval:.2f}s"
            )

        def _node_list(self) -> list[tuple[str, str]]:
            return list(self.get_node_names_and_namespaces())

        def _attach_topic_subscriptions(self) -> None:
            try:
                from rosidl_runtime_py.utilities import get_message
            except ImportError:
                self.get_logger().warning(
                    "rosidl_runtime_py is unavailable; topic checks cannot subscribe"
                )
                return
            topics = dict(self.get_topic_names_and_types())
            for name, check in self.config.checks.items():
                if check.check_type != "topic":
                    continue
                target = check.target
                if not target or target not in topics or not topics[target]:
                    subscription = self._topic_subscriptions.pop(name, None)
                    if subscription is not None:
                        self.destroy_subscription(subscription)
                    continue
                try:
                    message_type = get_message(topics[target][0])
                    current = self._topic_subscriptions.get(name)
                    if current is not None:
                        current_target, current_type = current[1], current[2]
                        if current_target == target and current_type == topics[target][0]:
                            continue
                        self.destroy_subscription(current[0])
                    endpoint_info = self.get_publishers_info_by_topic(target)
                    qos = (
                        endpoint_info[0].qos_profile
                        if endpoint_info and getattr(endpoint_info[0], "qos_profile", None)
                        else self._fallback_qos()
                    )
                    callback = self._make_topic_callback(name)
                    subscription = self.create_subscription(message_type, target, callback, qos)
                    self._topic_subscriptions[name] = (subscription, target, topics[target][0])
                    self.get_logger().debug("Aegis subscribed to %s for check %s", target, name)
                except Exception as exc:
                    self.get_logger().warning("Unable to subscribe to %s: %s", target, exc)

        @staticmethod
        def _fallback_qos():
            from rclpy.qos import qos_profile_sensor_data

            return qos_profile_sensor_data

        def _make_topic_callback(self, check_name: str):
            def callback(_message: Any) -> None:
                self.core.topic_message(check_name)

            return callback

        def _tick(self) -> None:
            self._attach_topic_subscriptions()
            result = self.core.tick()
            for event in result.events:
                if event.severity == "error":
                    self.get_logger().error("%s: %s", event.source, event.message)
                elif event.severity == "warning":
                    self.get_logger().warning("%s: %s", event.source, event.message)
                else:
                    self.get_logger().info("%s: %s", event.source, event.message)

        def _notify_operator(self, message: str, *_args: Any) -> None:
            self.get_logger().warning("Aegis operator notification: %s", message)

        def _safe_shutdown(self, *_args: Any) -> None:
            self.get_logger().error("Aegis requested a safe ROS shutdown")
            if rclpy.ok():
                rclpy.shutdown()

        def get_core(self) -> AegisCore:
            return self.core


    def run_ros(config_path: str | Path, args: list[str] | None = None) -> int:
        """Start the ROS 2 runtime and block until shutdown."""

        rclpy.init(args=args)
        node = AegisNode(config_path)
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        return 0


else:

    class AegisNode:  # pragma: no cover - simple error shim
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Aegis start requires a ROS 2 Python environment (rclpy)")


    def run_ros(_config_path: str | Path, args: list[str] | None = None) -> int:
        raise RuntimeError("Aegis start requires a ROS 2 Python environment (rclpy)")
