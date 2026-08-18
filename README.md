# Aegis

**Aegis is a runtime protection and recovery framework for ROS 2 robots.**

Its job is bigger than a watchdog:

```text
Observe → Detect → Decide → Recover
```

Aegis continuously evaluates robot health, applies declarative policies, and
records the reason for every state transition and recovery attempt.

## Capabilities

- health checks for `topic`, `node`, `process`, `hardware`, and `resource`;
- explicit `OK`, `WARNING`, `ERROR`, and `RECOVERING` states;
- policy rules in `aegis.yaml`;
- restart node, restart launch, execute command, notify operator, and safe shutdown actions;
- dependency-aware root-cause propagation;
- structured event history and persisted status snapshots;
- ROS-independent core that is straightforward to test or embed.

## Install and run

```bash
cd <checkout>
pip install -e .

# In a ROS 2 environment, install ROS dependencies with rosdep/apt from the
# sourced distribution. rclpy is not assumed to be available on PyPI:
rosdep install --from-paths . --ignore-src -r -y

# In ROS 2, the canonical runtime is started through the Aegis CLI:
aegis start --config config/aegis.yaml
```

The offline commands are useful for validation and automation:

```bash
aegis check --config config/aegis.yaml
aegis status --config config/aegis.yaml
aegis events --config config/aegis.yaml
```

`aegis check` performs one read-only reconciliation. It exits `0` only when
all checks are `OK`; `WARNING` and `ERROR` produce exit code `1`. Add
`--recover` to explicitly execute matching policies during a one-shot check;
the long-running `aegis start` loop always runs the full recovery cycle.

## Configuration

```yaml
apiVersion: aegis/v1
interval: 1s

checks:
  camera_topic:
    type: topic
    target: /camera/image_raw
    stale_timeout: 2s
    expected_rate: 30

  camera_node:
    type: node
    target: /camera_node

  cpu:
    type: resource
    target: cpu
    warning_threshold: 80
    error_threshold: 95

policies:
  - name: restart-camera-when-stale
    when:
      check: camera_topic
      field: stale_age
      operator: ">"
      value: 2s
    then:
      type: restart_node
      target: /camera_node
      command: ["ros2", "lifecycle", "set", "{target}", "restart"]

dependencies:
  detector_node: [camera_topic]
  tracking_node: [detector_node]
```

The expression form is also supported:

```yaml
when: "camera_topic.stale_age > 2s"
then: restart node /camera_node
```

Actions are edge-triggered by default. A policy fires once while its
condition remains true, so a dead camera does not create one recovery alarm
per timer tick. Set `repeat: true` and a `cooldown` when bounded retries are
desired. Failed actions become eligible again after their cooldown.

Recovery commands are explicit argument lists. Aegis rejects string commands
by default, so YAML interpolation cannot become a shell injection. A deployment
that truly needs shell syntax must set `unsafe_shell: true` and should isolate
that action behind a reviewed allowlist and least-privilege service. If a node
or launch has no configured command, Aegis reports a failed recovery rather
than guessing how that robot starts processes. Set `recovery.dry_run: true`
while validating a deployment.

## Dependency awareness

Dependencies use the form `dependent: [prerequisite]`. When `camera_topic`
fails, Aegis can mark `detector_node` and `tracking_node` unavailable while
retaining `camera_topic` as the root cause. The event stream consequently
contains one actionable root-cause transition and a small number of derived
availability events instead of an alarm storm.

## Architecture

| Layer | Responsibility |
| --- | --- |
| `aegis-core` | reconciliation loop and health state |
| collectors | topic/node/process/hardware/resource observations |
| policy engine | declarative condition matching and decision deduplication |
| recovery manager | bounded, injectable action execution |
| event system | transitions, root causes, recovery history, persistence |
| ROS adapter | graph discovery, topic subscriptions, ROS timer integration |

Aegis has one supported executable, `aegis`, and one supported Python package,
`aegis`. ROS 2 deployments should use `aegis start` so the same configuration
and recovery policy are exercised in development and production.
