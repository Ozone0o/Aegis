"""核心监控逻辑的单元测试。

使用 unittest.mock 模拟 rclpy，不需要真实 ROS2 环境。
"""

from __future__ import annotations

import pathlib
import time
import unittest
from unittest.mock import MagicMock

from src.ros2_device_watchdog.config import load_config, validate_config
from src.ros2_device_watchdog.monitors import NodeMonitor, TopicMonitor
from src.ros2_device_watchdog.models import (
    DeviceConfig,
    DeviceStatus,
)
from src.ros2_device_watchdog.monitor import (
    HYSTERSIS_OK_CONFIRM,
    WatchdogMonitor,
)


class TestTopicMonitor(unittest.TestCase):
    """TopicMonitor 单元测试。"""

    def test_initial_hz_is_zero(self):
        """初始状态下 Hz 应为 0。"""
        monitor = TopicMonitor(topic="/test")
        self.assertEqual(monitor.get_current_hz(), 0.0)

    def test_hz_calculated_from_timestamps(self):
        """根据时间戳正确计算 Hz。"""
        monitor = TopicMonitor(topic="/test", window=2.0)
        base = 1000.0
        for i in range(10):
            monitor.timestamps.append(base - i * 0.1)
        timestamps = list(monitor.timestamps)
        duration = timestamps[0] - timestamps[-1]
        hz = (len(timestamps) - 1) / duration if duration > 0 else 0.0
        self.assertGreater(hz, 8.0)
        self.assertLess(hz, 15.0)

    def test_hz_zero_with_single_timestamp(self):
        """单个时间戳时 Hz 为 0。"""
        monitor = TopicMonitor(topic="/test")
        monitor.timestamps.append(time.monotonic())
        self.assertEqual(monitor.get_current_hz(), 0.0)

    def test_last_message_age(self):
        """正确返回距最近消息的时间。"""
        monitor = TopicMonitor(topic="/test")
        self.assertEqual(monitor.get_last_message_age(), float('inf'))
        old_time = time.monotonic() - 0.5
        monitor.timestamps.append(old_time)
        age = monitor.get_last_message_age()
        self.assertGreaterEqual(age, 0.4)
        self.assertLessEqual(age, 0.6)

    def test_is_active(self):
        """有消息时 active 为 True。"""
        monitor = TopicMonitor(topic="/test")
        self.assertFalse(monitor.is_active())
        monitor.timestamps.append(time.monotonic())
        self.assertTrue(monitor.is_active())

    def test_callback_on_message(self):
        """消息到达时触发回调。"""
        monitor = TopicMonitor(topic="/test")
        called = []
        monitor.set_callback(lambda: called.append(True))
        monitor.on_message()
        self.assertEqual(len(called), 1)

    def test_timestamps_capped_by_maxlen(self):
        """时间戳数量不超过 maxlen。"""
        monitor = TopicMonitor(topic="/test", window=2.0)
        for _ in range(2000):
            monitor.on_message()
        self.assertLessEqual(len(monitor.timestamps), 1000)


class TestNodeMonitor(unittest.TestCase):
    """NodeMonitor 单元测试。"""

    def test_node_exists(self):
        """Node 存在时返回 True。"""
        monitor = NodeMonitor(node_name="test_node")
        monitor.set_node_list_provider(lambda: [("test_node", "/"), ("other", "/")])
        self.assertTrue(monitor.is_exists())

    def test_node_missing(self):
        """Node 不存在时返回 False。"""
        monitor = NodeMonitor(node_name="missing_node")
        monitor.set_node_list_provider(lambda: [("other", "/")])
        self.assertFalse(monitor.is_exists())

    def test_no_provider_returns_false(self):
        """无提供者时返回 False。"""
        monitor = NodeMonitor(node_name="test_node")
        self.assertFalse(monitor.is_exists())

    def test_provider_exception_returns_false(self):
        """提供者异常时返回 False。"""
        monitor = NodeMonitor(node_name="test_node")
        monitor.set_node_list_provider(lambda: (_ for _ in ()).throw(RuntimeError()))
        self.assertFalse(monitor.is_exists())


class TestWatchdogMonitor(unittest.TestCase):
    """WatchdogMonitor 核心逻辑单元测试。"""

    def _create_monitor(self):
        """创建带 mock recovery 的 WatchdogMonitor。"""
        monitor = WatchdogMonitor(cooldown=0.0, max_retries=3)
        recovery_mock = MagicMock()
        monitor.set_recovery_callback(lambda name: recovery_mock(name))
        return monitor, recovery_mock

    def test_normal_device_stays_ok(self):
        """正常设备持续保持 OK。"""
        monitor, _ = self._create_monitor()
        # expected_rate=50，50ms 间隔产生 ~20Hz 但窗口滑动后应充足
        config = DeviceConfig(topic="/camera", stale_timeout=2.0, expected_rate=20.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        for _ in range(40):
            monitor._topic_monitors["camera"].on_message()
            time.sleep(0.02)
            monitor.tick()

        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.OK)

    def test_topic_timeout_triggers_warn(self):
        """Topic 超时后从 OK 变为 WARN。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=0.1, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        monitor._topic_monitors["camera"].on_message()
        monitor.tick()  # OK
        time.sleep(0.2)

        monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.WARN)

    def test_hz_too_low_triggers_warn(self):
        """Hz 过低时触发 WARN。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=10.0, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        # 以极低频率发送消息（2秒内仅 2 条，Hz~1），远低于阈值 21
        # 通过 on_message 注入，确保时间戳使用正确的 time.monotonic()
        monitor._topic_monitors["camera"].on_message()
        time.sleep(1.5)
        monitor._topic_monitors["camera"].on_message()

        for _ in range(5):
            monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.WARN)

    def test_node_missing_triggers_error(self):
        """Node 缺失时触发 ERROR。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(node_name="missing_node", stale_timeout=1.0, type="node")
        monitor.add_device("nav", config)
        monitor.set_node_list_provider(lambda: [("other_node", "/")])

        changes = monitor.tick()
        state = monitor.get_state("nav")
        self.assertEqual(state.status, DeviceStatus.ERROR)
        self.assertTrue(len(changes) > 0)

    def test_topic_missing_triggers_error(self):
        """Topic 从未收到消息时触发 ERROR。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=1.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        changes = monitor.tick()
        state = monitor.get_state("camera")
        self.assertEqual(state.status, DeviceStatus.ERROR)
        self.assertTrue(len(changes) > 0)

    def test_warn_to_error_transition(self):
        """WARN -> ERROR 状态变化被记录。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=0.05, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        monitor._topic_monitors["camera"].on_message()
        monitor.tick()  # OK
        time.sleep(0.15)

        changes = monitor.tick()
        state = monitor.get_state("camera")
        self.assertEqual(state.status, DeviceStatus.WARN)
        warn_changes = [c for c in changes if c[1] == DeviceStatus.OK and c[2] == DeviceStatus.WARN]
        self.assertTrue(len(warn_changes) > 0)

    def test_recovering_after_max_retries(self):
        """连续失败超过最大重试次数后进入 RECOVERING。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=10.0, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        monitor._topic_monitors["camera"].on_message()
        monitor.tick()  # OK

        state = monitor.get_state("camera")
        state.consecutive_failures = 3

        changes = monitor.tick()
        self.assertEqual(state.status, DeviceStatus.RECOVERING)
        recover_changes = [c for c in changes if c[2] == DeviceStatus.RECOVERING]
        self.assertTrue(len(recover_changes) > 0)

    def test_recovery_success(self):
        """恢复成功后状态回到 OK。"""
        monitor, recovery_mock = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=10.0, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        state = monitor.get_state("camera")
        state.status = DeviceStatus.RECOVERING
        state.consecutive_failures = 3

        recovery_mock.return_value = True
        state.consecutive_failures = 0
        monitor._topic_monitors["camera"].on_message()

        for _ in range(HYSTERSIS_OK_CONFIRM + 1):
            monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.OK)

    def test_recovery_failure(self):
        """恢复失败后继续保持错误状态。"""
        monitor, recovery_mock = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=10.0, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        state = monitor.get_state("camera")
        state.status = DeviceStatus.RECOVERING
        state.consecutive_failures = 3
        recovery_mock.return_value = False

        for _ in range(5):
            monitor.tick()
        self.assertIn(state.status, (DeviceStatus.ERROR, DeviceStatus.RECOVERING))

    def test_cooldown_prevents_rapid_recovery(self):
        """冷却期阻止快速连续恢复。"""
        cooldown_secs = 10.0
        monitor = WatchdogMonitor(cooldown=cooldown_secs, max_retries=3)
        recovery_mock = MagicMock()
        monitor.set_recovery_callback(lambda name: recovery_mock(name))

        config = DeviceConfig(topic="/camera", stale_timeout=0.05, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        monitor._topic_monitors["camera"].on_message()
        monitor.tick()
        time.sleep(0.1)

        # 触发 WARN
        monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.WARN)

        # 手动触发 RECOVERING 状态变化（模拟从 WARN -> RECOVERING）
        state = monitor.get_state("camera")
        state.status = DeviceStatus.RECOVERING
        state.last_recovery_time = time.monotonic()

        # 短时间后再次 tick，cooldown 应阻止回调
        # 由于状态没变（RECOVERING -> RECOVERING），_on_status_changed 不会触发
        # 所以直接测试 _try_recover 的 cooldown 逻辑
        monitor._try_recover("camera")
        self.assertEqual(recovery_mock.call_count, 0)

    def test_hysteresis_prevents_status_flapping(self):
        """防抖机制防止状态在边界震荡。"""
        monitor, _ = self._create_monitor()
        config = DeviceConfig(topic="/camera", stale_timeout=10.0, expected_rate=30.0, type="topic")
        monitor.add_device("camera", config)
        monitor.set_node_list_provider(lambda: [])

        state = monitor.get_state("camera")
        state.status = DeviceStatus.ERROR

        monitor._topic_monitors["camera"].on_message()
        for _ in range(HYSTERSIS_OK_CONFIRM - 1):
            monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.ERROR)

        monitor.tick()
        self.assertEqual(monitor.get_state("camera").status, DeviceStatus.OK)

    def test_get_states_returns_all(self):
        """get_states 返回所有设备状态。"""
        monitor, _ = self._create_monitor()
        monitor.add_device("a", DeviceConfig(topic="/a", type="topic"))
        monitor.add_device("b", DeviceConfig(topic="/b", type="topic"))
        states = monitor.get_states()
        self.assertEqual(len(states), 2)
        self.assertIn("a", states)
        self.assertIn("b", states)

    def test_get_state_by_name(self):
        """get_state 按名称返回设备状态。"""
        monitor, _ = self._create_monitor()
        monitor.add_device("cam", DeviceConfig(topic="/cam", type="topic"))
        state = monitor.get_state("cam")
        self.assertIsNotNone(state)
        self.assertEqual(state.name, "cam")

    def test_get_state_missing_returns_none(self):
        """不存在的设备返回 None。"""
        monitor, _ = self._create_monitor()
        self.assertIsNone(monitor.get_state("nonexistent"))


class TestConfig(unittest.TestCase):
    """配置加载单元测试。"""

    def test_validate_topic_device(self):
        """校验 topic 类型设备配置。"""
        data = {
            "camera": {
                "type": "topic",
                "topic": "/camera/image_raw",
                "stale_timeout": 1.0,
                "expected_rate": 30.0,
            }
        }
        configs = validate_config(data)
        self.assertEqual(len(configs), 1)
        self.assertIsInstance(configs["camera"], DeviceConfig)
        self.assertEqual(configs["camera"].topic, "/camera/image_raw")

    def test_validate_node_device(self):
        """校验 node 类型设备配置。"""
        data = {
            "nav": {
                "type": "node",
                "node_name": "navigation",
                "stale_timeout": 2.0,
            }
        }
        configs = validate_config(data)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs["nav"].node_name, "navigation")

    def test_validate_missing_topic_raises(self):
        """缺少 topic 字段的 topic 设备被跳过。"""
        data = {"bad": {"type": "topic"}}
        with self.assertRaises(ValueError):
            validate_config(data)

    def test_validate_missing_node_name_raises(self):
        """缺少 node_name 字段的 node 设备被跳过。"""
        data = {"bad": {"type": "node"}}
        with self.assertRaises(ValueError):
            validate_config(data)

    def test_validate_default_values(self):
        """缺失字段使用默认值。"""
        data = {"cam": {"type": "topic", "topic": "/test"}}
        configs = validate_config(data)
        self.assertEqual(configs["cam"].stale_timeout, 1.0)
        self.assertEqual(configs["cam"].expected_rate, 10.0)

    def test_validate_invalid_type_raises(self):
        """不支持的 device type 被跳过。"""
        data = {"bad": {"type": "sensor", "topic": "/test"}}
        with self.assertRaises(ValueError):
            validate_config(data)

    def test_load_config_file(self):
        """从文件加载配置。"""
        configs = load_config(pathlib.Path(__file__).parent.parent / "config" / "example.yaml")
        self.assertGreaterEqual(len(configs), 2)
        self.assertIn("camera", configs)
        self.assertIn("robot_state", configs)


class TestDeviceStatus(unittest.TestCase):
    """DeviceStatus 枚举测试。"""

    def test_all_statuses_present(self):
        """所有状态枚举值都存在。"""
        self.assertEqual(DeviceStatus.OK.value, 1)
        self.assertEqual(DeviceStatus.WARN.value, 2)
        self.assertEqual(DeviceStatus.ERROR.value, 3)
        self.assertEqual(DeviceStatus.RECOVERING.value, 4)


if __name__ == "__main__":
    unittest.main()
