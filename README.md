# Kachaka - OpenRoIS Integration

Integration of Kachaka (Preferred Robotics) with OpenRoIS.
This implementation is meant to illustrate the 

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![ROS](https://img.shields.io/badge/ROS_2-Jazzy-22314E?logo=ros&logoColor=white)

## Repository structure

```
openrois-kachaka/
├── bus-adapter/     # gRPC BusAdapter package (openrois-bus-kachaka)
├── gateway/         # thin WebSocket gateway (JSON-RPC 2.0)
├── kachaka-api/     # vendored kachaka-api submodule (gRPC SDK)
├── maps/            # map assets
├── vendor/          # vendored wheels (openrois-interfaces)
└── ros2_ws/         # ROS 2 workspace (canonical path for Teleco PAO)
```

## Installation

### Prerequisites

- Python 3.12+
- For the ROS 2 path: ROS 2 Jazzy Jalisco

### gRPC adapter path (no ROS 2)

```bash
git submodule update --init
pip install websockets
pip install vendor/openrois_interfaces-0.1.0a2-py3-none-any.whl
pip install -e bus-adapter/

python gateway/main.py --target <kachaka-ip>:26400 --port 8765
# or with the mock adapter (no robot needed):
python gateway/main.py --mock --port 8765
```

### ROS 2 path (canonical, for Teleco PAO reuse)

```
colcon build
rosrun
```

## License

```
Copyright 2026 Coarobo GK. All rights reserved.

No License.
```

## Project Status

> [!WARNING]
> This project is in _alpha_ stage and is not production-ready. APIs, behavior, and structure may change without notice.
