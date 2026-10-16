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

The adapter is a thin process. It hosts three ROS 2 components of
`openrois-components-kachaka` in an engine and connects the engine to an
OpenRoIS gateway, which it dials on `/adapter`. The components share one
`RclpyExecutor`, which initializes rclpy and spins their three nodes
(`openrois_kachaka_nav`, `openrois_kachaka_sysinfo` and
`openrois_kachaka_navinfo`) in a background thread.

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
3. The OpenRoIS packages, installed from a clone of
   [openrois/openrois](https://github.com/openrois/openrois) into the
   Python that runs ROS 2, since they are not on PyPI yet:
   ```bash
   OPENROIS=/path/to/openrois     # the clone
   pip install -e $OPENROIS/interfaces/python -e $OPENROIS/engine \
       -e $OPENROIS/components/core
   pip install -e components             # this repository's Kachaka components
   ```

### Build the adapter

```bash
cd ros2/ros2_ws
colcon build --packages-select openrois_kachaka_ros2
source install/setup.bash
```

## Configure

Copy `adapter.yaml.example` to `adapter.yaml`. Its defaults match the
bridge in the `kachaka` namespace:

```yaml
gateway_url: ws://127.0.0.1:8765
engine_id: kachaka
log_level: info

navigation:
  node_name: openrois_kachaka
  locations_topic: /kachaka/layout/locations/list
  command_action: /kachaka/kachaka_command/execute
  goal_timeout: 10.0

system_information:
  node_name: openrois_kachaka
  pose_topic: /kachaka/odometry/odometry

navigation_information:
  node_name: openrois_kachaka
  locations_topic: /kachaka/layout/locations/list
  map_topic: /kachaka/mapping/map
  map_name: coarobo-kachaka_01@unicorn
```

If you start the bridge with a custom namespace (e.g.
`./start_bridge.sh <IP> myrobot`), prefix the topics and the action with
`/myrobot/` instead.

Each setting comes from the defaults, then `adapter.yaml`, then the
environment, then the flags. `gateway_url`, `engine_id` and `log_level`
also come from `OPENROIS_ADAPTER_GATEWAY_URL`,
`OPENROIS_ADAPTER_ENGINE_ID` and `OPENROIS_ADAPTER_LOG_LEVEL`, or from
`--gateway-url`, `--engine-id` and `--log-level`. The sections come only
from the file. `openrois-profile.yaml` is no longer read: its
`environment` topics move into the sections above, `robot_pose_topic`
becoming `system_information.pose_topic`, and `poll_interval` is gone.

## Start

Start the `kachaka_grpc_ros2_bridge` on the robot, then:

```bash
source ros2/ros2_ws/install/setup.bash
ros2 run openrois_kachaka_ros2 kachaka_adapter --config adapter.yaml
```

Arguments after `--ros-args` are left to ROS 2. The adapter runs until
Ctrl+C and reconnects when the gateway goes away.

## Components

| Component | Type | Serves |
|---|---|---|
| `Navigation` | `Ros2Navigation`, RoIS Navigation | `start`, `stop`, `suspend`, `resume`, `reached_target`, the Navigation parameters |
| `SystemInformation` | `Ros2SystemInformation`, RoIS SystemInformation | `robot_position`, `engine_status` |
| `NavigationInformation` | `Ros2NavigationInformation`, Coarobo NavigationInformation | `destinations`, `destination_positions`, `map_info` |

The engine answers `component_status` for every component and assigns
the command ids. Robot position is read from the odometry topic
(nav_msgs/Odometry), the destinations from the location list topic
(kachaka_interfaces/LocationList) and the map geometry from the map
topic (nav_msgs/OccupancyGrid). `start` sends a goal to the
ExecKachakaCommand action and completes when the action ends, emitting
`reached_target`; `stop` cancels the goal.

## Test

Without ROS 2, against the installed OpenRoIS packages:

```bash
pip install pytest pytest-asyncio
cd ros2/ros2_ws/src/openrois_kachaka_ros2 && pytest
```
