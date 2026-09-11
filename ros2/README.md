# openrois-adapter-kachaka (ROS 2)

OpenRoIS adapter for the Kachaka robot using ROS 2. Subscribes to Kachaka
ROS 2 topics and calls the ExecKachakaCommand action via rclpy.

## Architecture

The Kachaka robot runs the `kachaka_grpc_ros2_bridge` (C++ rclcpp) which
bridges the Kachaka gRPC API to ROS 2 topics and actions. This adapter
talks to those ROS 2 topics, not to gRPC directly.

```
Avatar (bridge, HriEngine) --WS:8765--> ROS 2 Adapter (rclpy)
                                           |
                                           +-- subscribes: /kachaka/odometry/odometry
                                           +-- subscribes: /kachaka/layout/locations/list
                                           +-- action client: /kachaka/kachaka_command/execute
                                           v
                              kachaka_grpc_ros2_bridge (C++ rclcpp)
                                           | gRPC
                                           v
                                      Kachaka robot
```

The `kachaka_grpc_ros2_bridge` launches all nodes under a namespace
(default: `kachaka`). Topic names include this prefix. If you start the
bridge with a custom namespace, update the topic names in the profile.

The adapter SDK framework spins the rclpy node in a background thread.
The adapter's `@query` and `@invoke` handlers run in the asyncio main loop.
Shared state (latest pose, locations, command state) is protected by
`threading.Lock`. Event emission via `self.emit()` is thread-safe.

## Install

### Requirements

1. ROS 2 (Jazzy or Humble) installed.
2. `kachaka_interfaces` and `kachaka_grpc_ros2_bridge` built from
   `kachaka-api/ros2/`:
   ```bash
   cd kachaka-api/ros2
   colcon build --packages-select kachaka_interfaces kachaka_grpc_ros2_bridge
   source install/setup.bash
   ```
3. OpenRoIS interfaces, adapter SDK, and Kachaka components installed
   from the vendored wheels:
   ```bash
   pip install vendor/openrois_interfaces-*.whl vendor/openrois_core-*.whl vendor/openrois_components_core-*.whl vendor/openrois_components_kachaka-*.whl
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
engine:
  id: kachaka
  platform: kachaka
  gateway_url: "ws://127.0.0.1:8765"

environment:
  ros2_node_name: "openrois_kachaka_ros2"
  robot_pose_topic: "/kachaka/odometry/odometry"
  locations_topic: "/kachaka/layout/locations/list"
  command_action: "/kachaka/kachaka_command/execute"

components:
  Navigation:
    poll_interval: 0.5
  NavigationInformation: {}
```

The default namespace is `kachaka`. If you start the bridge with a custom
namespace (e.g. `./start_bridge.sh <IP> myrobot`), prefix all topics with
`/myrobot/` instead.

## Run

Start the `kachaka_grpc_ros2_bridge` on the robot, then:

```bash
source ros2/ros2_ws/install/setup.bash
ros2 run openrois_kachaka_ros2 kachaka_adapter --config openrois-profile.yaml
```

Or run directly:

```bash
python ros2/ros2_ws/src/openrois_kachaka_ros2/openrois_kachaka_ros2/kachaka_ros2_adapter.py --config ros2/openrois-profile.yaml
```

## Components

| Component | Function | Queries | Commands | Events |
|---|---|---|---|---|
| SystemInformation | - | robot_position, component_status | | |
| Navigation | actuation | get_parameter, component_status | execute, stop, set_parameter | reached_target |
| NavigationInformation | - | locations, location_details, component_status | | |

Robot position is read from the `/kachaka/odometry/odometry` topic
(nav_msgs/Odometry). Locations are read from the
`/kachaka/layout/locations/list` topic (kachaka_interfaces/LocationList).
Navigation commands are sent via the `/kachaka/kachaka_command/execute`
action (kachaka_interfaces/ExecKachakaCommand). The `reached_target` event
fires when the action completes (no polling).
