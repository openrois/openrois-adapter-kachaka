# openrois-components-kachaka

OpenRoIS components for the [Preferred Robotics Kachaka](https://kachaka.life/). They
implement the basic RoIS components Navigation and SystemInformation, and
NavigationInformation, a component type Coarobo publishes, and translate RoIS messages
into Kachaka calls, over either the gRPC API or ROS 2.

| Component | Backend | Serves |
|-----------|---------|--------|
| `GrpcNavigation` | gRPC API | `start`, `stop`, the `reached_target` event, and the Navigation parameters |
| `Ros2Navigation` | ROS 2 | The same messages, over the `ExecKachakaCommand` action and the location list topic |
| `GrpcSystemInformation` | gRPC API | `robot_position`, `engine_status` |
| `Ros2SystemInformation` | ROS 2 | The same queries, from the odometry topic |
| `GrpcNavigationInformation` | gRPC API | `destinations`, `destination_positions`, `map_info` |
| `Ros2NavigationInformation` | ROS 2 | The same queries, from the location list and map topics |

NavigationInformation has the profile `NAVIGATION_INFORMATION_PROFILE`, authority
`Coarobo` and code `NavigationInformation`, since no basic RoIS type gives a client the
places a robot can go to and the map it moves on. `destinations` answers the names of the
Kachaka's locations, `destination_positions` a JSON array of `{id, name, x, y, theta}`,
and `map_info` a JSON object with the configured `map_name` and the geometry of the map
(`resolution`, `origin_x`, `origin_y`, `origin_theta`, `width`, `height`), which a client
needs to place positions on its own image of the map.

Two backends for one RoIS component type is the point: both classes declare the same
profile constant, so a client sees `Navigation` either way and does not know which one
runs.

`start` drives to the first of `target_positions`, a location of the Kachaka named by its
name or its id, and runs until the Kachaka gets there. Then the component emits
`reached_target`, and the engine completes the command with OK. An unknown location or a
failed drive ends the command with ERROR. `stop`, or a new `start`, cancels the drive,
which ends with ABORT. The engine stores `time_limit` and `routing_policy`, and the
components do not apply them to the Kachaka. The engine answers `component_status` and,
on Navigation, `suspend` and `resume`, and it assigns the command ids.

## Install

Not published to PyPI yet. The package lives in this repository, beside the two
adapters, and needs the OpenRoIS packages from a clone of
[openrois/openrois](https://github.com/openrois/openrois):

```bash
OPENROIS=/path/to/openrois     # the clone
pip install -e $OPENROIS/interfaces/python -e $OPENROIS/engine -e $OPENROIS/components/core
pip install -e "./components[grpc]"    # the gRPC API backend
pip install -e ./components            # the ROS 2 backend
```

The ROS 2 backend needs a sourced ROS 2 installation with `rclpy`, `nav_msgs` and the
`kachaka_interfaces` package.

## Usage

An adapter hosts the components in an engine and reads their settings from its
`adapter.yaml` through the engine's adapter loader, one section per component:

```python
from openrois.components.kachaka import (
    GrpcNavigation,
    GrpcNavigationSettings,
    GrpcSystemInformation,
)
from openrois.engine import Engine, WsClient
from openrois.engine.config import AdapterConfig, load_adapter_config


class KachakaConfig(AdapterConfig):
    engine_id: str = "kachaka_1"
    navigation: GrpcNavigationSettings = GrpcNavigationSettings()


config = load_adapter_config(KachakaConfig)  # robot_address: 192.168.1.100:26400
engine = Engine(config.engine_id)
engine.add_component("navigation", GrpcNavigation(config.robot_address, config.navigation))
engine.add_component("system_information", GrpcSystemInformation(config.robot_address))
WsClient(engine, config.gateway_url).run()
```

Clients see `kachaka_1/navigation` and `kachaka_1/system_information`. Each gRPC
component keeps its own client of the Kachaka gRPC API.

The ROS 2 components share one `RclpyExecutor` from `openrois.components.core.ros2`.
The engine opens it before the first of them connects, which initializes rclpy and spins
their nodes in a background thread, and closes it after the last one disconnects:

```python
from openrois.components.core.ros2 import RclpyExecutor
from openrois.components.kachaka import (
    Ros2Navigation,
    Ros2NavigationSettings,
    Ros2SystemInformation,
    Ros2SystemInformationSettings,
)


class KachakaConfig(AdapterConfig):
    engine_id: str = "kachaka_1"
    navigation: Ros2NavigationSettings = Ros2NavigationSettings()
    system_information: Ros2SystemInformationSettings = Ros2SystemInformationSettings()


config = load_adapter_config(KachakaConfig)
ros = RclpyExecutor()
engine = Engine(config.engine_id)
engine.add_component("navigation", Ros2Navigation(ros, config.navigation))
engine.add_component("system_information", Ros2SystemInformation(ros, config.system_information))
WsClient(engine, config.gateway_url).run()
```

The sections of `adapter.yaml` and their defaults. For `GrpcNavigation`:

```yaml
navigation:
  auto_homing: false        # whether the Kachaka returns to its charger when idle
navigation_information:
  map_name: ""              # the name map_info gives the map
```

For the ROS 2 components, whose defaults match the Kachaka ROS 2 bridge in its `kachaka`
namespace:

```yaml
navigation:
  node_name: openrois_kachaka            # the node is openrois_kachaka_nav
  locations_topic: /kachaka/layout/locations/list
  command_action: /kachaka/kachaka_command/execute
  goal_timeout: 10.0                     # seconds to wait for the goal's answer
system_information:
  node_name: openrois_kachaka            # the node is openrois_kachaka_sysinfo
  pose_topic: /kachaka/odometry/odometry
navigation_information:
  node_name: openrois_kachaka            # the node is openrois_kachaka_navinfo
  locations_topic: /kachaka/layout/locations/list
  map_topic: /kachaka/mapping/map
  map_name: ""
```

## Test

```bash
pip install -e $OPENROIS/interfaces/python -e $OPENROIS/engine \
    -e $OPENROIS/components/core -e "./components[dev]"
cd components && pytest && mypy && ruff check src/ tests/
```

The tests run the components in an engine against stand-ins for the gRPC API, rclpy and
the Kachaka interfaces, so they need neither a Kachaka nor ROS 2. A Kachaka is the only
full check.

## License

Apache-2.0.
