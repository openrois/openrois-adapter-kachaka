# openrois-adapter-kachaka (ROS 2)

OpenRoIS adapter for the Kachaka robot using ROS 2. Subscribes to Kachaka
ROS 2 topics and calls the ExecKachakaCommand action via rclpy.

## Architecture

The Kachaka robot runs the `kachaka_grpc_ros2_bridge` (C++ rclcpp) which
bridges the Kachaka gRPC API to ROS 2 topics and actions. This adapter
talks to those ROS 2 topics, not to gRPC directly.

```
Avatar (bridge, HriEngine) --WS:31166--> ROS 2 Adapter (rclpy)
                                           |
                                           +-- subscribes: /odometry/odometry
                                           +-- subscribes: /layout/locations/list
                                           +-- action client: /kachaka_command/execute
                                           v
                              kachaka_grpc_ros2_bridge (C++ rclcpp)
                                           | gRPC
                                           v
                                      Kachaka robot
```

The adapter SDK framework spins the rclpy node in a background thread.
The adapter's `@query` and `@invoke` handlers run in the asyncio main loop.
Shared state (latest pose, locations, command state) is protected by
`threading.Lock`. Event emission via `self.emit()` is thread-safe.

## Install

### Prerequisites

1. ROS 2 (Jazzy or Humble) installed.
2. `kachaka_interfaces` and `kachaka_grpc_ros2_bridge` built from
   `kachaka-api/ros2/`:
   ```bash
   cd kachaka-api/ros2
   colcon build --packages-select kachaka_interfaces kachaka_grpc_ros2_bridge
   source install/setup.bash
   ```
3. `openrois-adapter-sdk` installed:
   ```bash
   pip install openrois-adapter-sdk
   ```

### Build the adapter

```bash
cd ros2/ros2_ws
colcon build --packages-select openrois_kachaka_ros2
source install/setup.bash
```

## Config

Copy `openrois-profile.yaml.example` to `openrois-profile.yaml` and edit the topic names
to match your `kachaka_grpc_ros2_bridge` configuration:

```yaml
fleet_id: kachaka

connection:
  ws:
    host: "127.0.0.1"
    port: 31166

ros2:
  robot_pose_topic: "/odometry/odometry"
  locations_topic: "/layout/locations/list"
  command_action: "/kachaka_command/execute"
```

## Run

Start the `kachaka_grpc_ros2_bridge` on the robot, then:

```bash
source ros2/ros2_ws/install/setup.bash
ros2 run openrois_kachaka_ros2 kachaka_adapter
```

Or run directly:

```bash
python ros2/ros2_ws/src/openrois_kachaka_ros2/openrois_kachaka_ros2/kachaka_ros2_adapter.py --config ros2/openrois-profile.yaml
```

## Components

| Component | bind_required | Queries | Commands | Events |
|---|---|---|---|---|
| SystemInformation | No | robot_position, component_status | | |
| Navigation | Yes | waypoints, component_status | execute, stop | reached_target |

Robot position is read from the `/odometry/odometry` topic (nav_msgs/Odometry).
Waypoints are read from the `/layout/locations/list` topic
(kachaka_interfaces/LocationList). Navigation commands are sent via the
`/kachaka_command/execute` action (kachaka_interfaces/ExecKachakaCommand).
The `reached_target` event fires when the action completes (no polling).
