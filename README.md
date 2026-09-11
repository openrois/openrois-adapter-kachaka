# Kachaka - OpenRoIS Adapters

OpenRoIS adapter for the Kachaka robot (Preferred Robotics). Provides two
integration paths: a direct Python adapter using the vendor API library,
and a ROS 2 adapter using the `kachaka_grpc_ros2_bridge`.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![ROS](https://img.shields.io/badge/ROS_2-Jazzy-22314E?logo=ros&logoColor=white)

## Repository structure

```
openrois-adapter-kachaka/
├── api/                 # direct adapter using the kachaka-api Python library
│   ├── kachaka_api_adapter.py
│   ├── openrois-profile.yaml.example
│   └── README.md
├── ros2/                # ROS 2 adapter using the kachaka_grpc_ros2_bridge
│   ├── ros2_ws/
│   │   └── src/openrois_kachaka_ros2/
│   │       └── openrois_kachaka_ros2/
│   │           ├── kachaka_ros2_adapter.py    # KachakaRos2Adapter
│   │           └── kachaka_ros2_client.py     # KachakaRos2Client
│   ├── openrois-profile.yaml.example
│   └── README.md
├── kachaka-api/         # vendored kachaka-api submodule (gRPC SDK + ROS 2 bridge)
└── utils/
```

## Integration paths

| Path | Directory | Package | Executable | Connects to |
|---|---|---|---|---|
| API | `api/` | (flat script) | `kachaka_api_adapter.py` | Kachaka gRPC server via kachaka-api Python library |
| ROS 2 | `ros2/` | `openrois_kachaka_ros2` | `kachaka_adapter` | ROS 2 topics and actions via `kachaka_grpc_ros2_bridge` |

Both paths reach the same robot. The API path uses the vendor Python library
directly. The ROS 2 path goes through a ROS 2 bridge that translates gRPC to
topics and actions.

## Requirements

- Python 3.12+
- OpenRoIS adapter SDK and Kachaka components installed:
  ```bash
  pip install openrois-core openrois-components-kachaka
  ```
- For the API path: `kachaka-api` Python library (vendored in `kachaka-api/python/`)
- For the ROS 2 path: ROS 2 Jazzy Jalisco

## Quick start

### API path (gRPC direct)

The API path connects directly to the Kachaka gRPC server. No ROS 2
required.

```bash
cd api
pip install -r requirements.txt
cp openrois-profile.yaml.example openrois-profile.yaml
# Edit openrois-profile.yaml:
#   environment.grpc_server -> <robot IP>:26400
#   engine.gateway_url -> ws://<engine host>:8765
python kachaka_api_adapter.py --config openrois-profile.yaml
```

Required profile fields:

| Field | Description | Example |
|---|---|---|
| `engine.id` | Engine identifier | `kachaka_01` |
| `engine.gateway_url` | Engine WebSocket URL | `ws://127.0.0.1:8765` |
| `environment.grpc_server` | Kachaka gRPC target | `192.168.100.203:26400` |
| `environment.auto_homing` | Enable auto homing on connect | `false` |

### ROS 2 path (via bridge)

The ROS 2 path requires building the `kachaka_grpc_ros2_bridge` from
the vendored `kachaka-api` submodule. Both the bridge and the adapter
must use the same ROS 2 version (Jazzy). Mixing ROS 2 versions (e.g.
Humble bridge with Jazzy adapter) causes CDR serialization errors.

**1. Build the bridge from source:**

```bash
# Install build dependencies
sudo apt install -y \
  libprotobuf-dev libgrpc++-dev nlohmann-json3-dev \
  protobuf-compiler-grpc ros-jazzy-xacro

# Generate protobuf C++ source (normally done in Docker build)
cd kachaka-api
mkdir -p ros2/kachaka_grpc_ros2_bridge/gen-src
protoc -I protos \
  --grpc_out=ros2/kachaka_grpc_ros2_bridge/gen-src \
  --plugin=protoc-gen-grpc=$(which grpc_cpp_plugin) \
  --cpp_out=ros2/kachaka_grpc_ros2_bridge/gen-src \
  protos/*.proto

# Build all bridge packages
cd ros2
source /opt/ros/jazzy/setup.bash
colcon build --packages-select kachaka_interfaces kachaka_description kachaka_grpc_ros2_bridge
source install/setup.bash
```

**2. Build the adapter:**

```bash
cd ros2/ros2_ws
colcon build --packages-select openrois_kachaka_ros2
source install/setup.bash
```

**3. Start the bridge:**

```bash
source /opt/ros/jazzy/setup.bash
source kachaka-api/ros2/install/setup.bash
ros2 launch kachaka_grpc_ros2_bridge grpc_ros2_bridge.launch.xml \
  server_uri:=192.168.100.203:26400
```

**4. Run the adapter:**

```bash
cd ros2
cp openrois-profile.yaml.example openrois-profile.yaml
# Edit openrois-profile.yaml:
#   engine.gateway_url -> ws://<engine host>:8765
source /opt/ros/jazzy/setup.bash
source kachaka-api/ros2/install/setup.bash
source ros2_ws/install/setup.bash
ros2 run openrois_kachaka_ros2 kachaka_adapter --config openrois-profile.yaml
```

Required profile fields:

| Field | Description | Example |
|---|---|---|
| `engine.id` | Engine identifier | `kachaka_01` |
| `engine.gateway_url` | Engine WebSocket URL | `ws://127.0.0.1:8765` |
| `environment.ros2_node_name` | rclpy node name | `openrois_kachaka_ros2` |
| `environment.robot_pose_topic` | Odometry topic | `/kachaka/odometry/odometry` |
| `environment.locations_topic` | Location list topic | `/kachaka/layout/locations/list` |
| `environment.command_action` | Navigation action | `/kachaka/kachaka_command/execute` |

Topic names include the `/kachaka/` namespace used by the bridge
(default namespace: `kachaka`). If you start the bridge with a custom
namespace, update the topic names in the profile.

See `api/README.md` and `ros2/README.md` for detailed instructions.

## Components

| Component | Function | Queries | Commands | Events |
|---|---|---|---|---|
| SystemInformation | - | robot_position, component_status | | |
| Navigation | actuation | get_parameter, component_status | execute, stop, set_parameter | reached_target |
| NavigationInformation | - | locations, location_details, component_status | | |

## License

```
Copyright 2026 Coarobo GK. All rights reserved.

No License.
```

## Project status

> [!WARNING]
> This project is in alpha stage and is not production-ready. APIs, behavior, and structure may change without notice.
