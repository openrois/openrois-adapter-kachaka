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

## Prerequisites

- Python 3.12+
- For the ROS 2 path: ROS 2 Jazzy Jalisco

## Quick start

### API path

```bash
cd api
pip install -r requirements.txt
cp openrois-profile.yaml.example openrois-profile.yaml
# Edit openrois-profile.yaml to set the gRPC target
python kachaka_api_adapter.py --config openrois-profile.yaml
```

### ROS 2 path

```bash
cd ros2/ros2_ws
colcon build --packages-select openrois_kachaka_ros2
source install/setup.bash
ros2 run openrois_kachaka_ros2 kachaka_adapter
```

See `api/README.md` and `ros2/README.md` for detailed instructions.

## Components

| Component | bind_required | Queries | Commands | Events |
|---|---|---|---|---|
| SystemInformation | No | robot_position, engine_status | | |
| Navigation | Yes | get_parameter, component_status | execute, stop | reached_target |
| MapObserver | No | waypoints, map_info, component_status | | |

## License

```
Copyright 2026 Coarobo GK. All rights reserved.

No License.
```

## Project status

> [!WARNING]
> This project is in alpha stage and is not production-ready. APIs, behavior, and structure may change without notice.
