# ros2-device-watchdog

ROS2 设备健康监控工具。

监控摄像头 Topic、机器人状态 Topic、Node 是否存在，及时发现异常并通知。

## 最小示例

### 安装

```bash
cd ros2-device-watchdog
pip install -e .
# 或在 ROS2 工作空间中：
colcon build --packages-select ros2_device_watchdog
```

### 创建 YAML 配置

```yaml
# config/example.yaml
devices:
  camera:
    type: topic
    topic: /camera/image_raw
    stale_timeout: 1.0
    expected_rate: 30.0
```

### 启动

```bash
ros2 run ros2_device_watchdog watchdog
# 或指定配置文件：
# ros2 run ros2_device_watchdog watchdog --config-path /path/to/config.yaml
```

### 看状态

通过 `ros2 diagnostic` 命令查看：

```bash
ros2 diagnostic
```

或在 ROS2 日志中看到类似输出：

```
2026-08-10 10:00:00 [INFO] camera: OK -> WARN
2026-08-10 10:00:02 [INFO] camera: WARN -> ERROR
2026-08-10 10:00:05 [INFO] camera: ERROR -> RECOVERING
2026-08-10 10:00:10 [INFO] camera: RECOVERING -> OK
```

## 参数说明

### stale_timeout

超过多久没有收到消息，判定为异常。

- `stale_timeout: 1.0` — 超过 1 秒无消息就 WARN
- 如果 Topic 完全断了（收不到任何消息），会直接 ERROR

### expected_rate

期望的 Topic 频率（Hz）。

- `expected_rate: 30.0` — 期望 30 Hz
- 如果实际频率低于期望值的 70%，判定为 WARN
- 多出的 30% 余量是为了防止边界情况频繁跳动

### WARN 和 ERROR 的区别

| 状态 | 含义 | 触发条件 |
|------|------|----------|
| OK | 正常 | 消息新鲜，频率正常 |
| WARN | 警告 | 消息超时 或 频率偏低 |
| ERROR | 错误 | Topic/Node 不存在，或 WARN 持续未恢复 |
| RECOVERING | 恢复中 | 连续失败达到上限，准备执行恢复 |

### 为什么 recovery 默认不自动重启进程

第一版默认使用 NoOp 恢复策略，不做任何自动恢复操作。

原因：
- 自动重启可能掩盖真正的问题（驱动 bug、硬件故障）
- `kill -9`、`reboot` 等操作有数据丢失风险
- 不同场景的恢复策略差异很大，不适合一刀切

你可以通过继承 `RecoveryStrategy` 来实现自己的恢复逻辑（见下方扩展部分）。

## 扩展指南

| 你想做什么 | 改哪个文件 |
|-----------|-----------|
| 新增设备检测逻辑 | `monitor.py` |
| 修改状态模型 | `models.py` |
| 新增 Recovery Strategy | `recovery/` 目录下新建文件 |
| 修改 YAML 配置格式 | `config.py` |
| 修改 ROS diagnostics 输出 | `diagnostics.py` |
| 修改 ROS Node 行为 | `node.py` |

### 自定义 Recovery Strategy

```python
from ros2_device_watchdog.recovery.base import RecoveryStrategy

class MyRecovery(RecoveryStrategy):
    async def recover(self, device_name: str) -> bool:
        # 你的恢复逻辑
        # 例如：重启某个 ROS2 node
        return True
```

然后在 `node.py` 中使用：

```python
from .recovery import MyRecovery
# ...
recovery = MyRecovery()
```

## Troubleshooting

### 一直显示 Topic Missing

1. 确认 Topic 确实存在：`ros2 topic list | grep <topic_name>`
2. 确认 Topic 有数据发布：`ros2 topic hz <topic_name>`
3. 检查 YAML 中的 `topic` 名称是否拼写正确
4. 检查 ROS2 网络（如果是多机部署）

### expected_rate 设多少合适

- 摄像头：通常等于帧率（15/30/60 Hz）
- 状态信息：通常 1-20 Hz
- 建议先观察实际频率，然后设为实际值或略低

### 为什么 camera 会 WARN 但仍有数据

WARN 通常意味着消息还在到达，但频率不够。可能的原因：
- 摄像头帧率确实降低了
- 网络延迟导致消息堆积
- 系统负载高导致处理延迟

### recovery 为什么没有自动重启

默认配置使用 NoOp 恢复策略，不会执行任何恢复操作。这是设计决定，详见上方说明。如需自动恢复，请自行实现 `RecoveryStrategy`。
