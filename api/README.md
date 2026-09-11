# openrois-adapter-kachaka (API)

OpenRoIS adapter for the Kachaka robot using the gRPC API. Connects
directly to the Kachaka gRPC server. No ROS 2 required.

## Install

```bash
pip install openrois-core openrois-components-kachaka kachaka-api
```

The `kachaka-api` package is vendored in `kachaka-api/python/`. Install
from there if not on PyPI:

```bash
cd kachaka-api/python
pip install -e .
```

## Config

Copy `openrois-profile.yaml.example` to `openrois-profile.yaml` and edit
the gRPC target and engine URL:

```yaml
engine:
  id: kachaka_01
  platform: kachaka
  gateway_url: "ws://127.0.0.1:8765"

environment:
  grpc_server: "192.168.100.203:26400"
  auto_homing: false

components:
  Navigation:
    poll_interval: 0.5
  NavigationInformation: {}
```

Required profile fields:

| Field | Description | Example |
|---|---|---|
| `engine.id` | Engine identifier | `kachaka_01` |
| `engine.gateway_url` | Engine WebSocket URL | `ws://127.0.0.1:8765` |
| `environment.grpc_server` | Kachaka gRPC target | `192.168.100.203:26400` |
| `environment.auto_homing` | Enable auto homing on connect | `false` |

## Run

```bash
cd api
python kachaka_api_adapter.py --config openrois-profile.yaml
```

## Components

| Component | Function | Queries | Commands | Events |
|---|---|---|---|---|
| SystemInformation | - | robot_position, component_status | | |
| Navigation | actuation | get_parameter, component_status | execute, stop, set_parameter | reached_target |
| NavigationInformation | - | locations, location_details, component_status | | |

Robot position is polled from `get_robot_pose()`. Locations are polled
from `get_location_list()`. Navigation commands are sent via
`move_to_location()`. The `reached_target` event fires when
`get_command_state()` reports completion (polled at `poll_interval`).
