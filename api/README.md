# openrois-adapter-kachaka (API)

OpenRoIS adapter for the Kachaka robot over its gRPC API. No ROS 2 needed.

The adapter is a thin process. It hosts three components of
`openrois-components-kachaka` in an engine and connects the engine to an
OpenRoIS gateway, which it dials on `/adapter`:

| Component | Type | Serves |
|---|---|---|
| `Navigation` | `GrpcNavigation`, RoIS Navigation | `start`, `stop`, `suspend`, `resume`, `reached_target`, the Navigation parameters |
| `SystemInformation` | `GrpcSystemInformation`, RoIS SystemInformation | `robot_position`, `engine_status` |
| `NavigationInformation` | `GrpcNavigationInformation`, Coarobo NavigationInformation | `destinations`, `destination_positions`, `map_info` |

The engine answers `component_status` for every component and assigns
the command ids. Each component keeps its own client of the gRPC API.

## Install

Python 3.12 or later. The OpenRoIS packages are not on PyPI yet, so
install them from a clone of
[openrois/openrois](https://github.com/openrois/openrois):

```bash
OPENROIS=/path/to/openrois     # the clone
pip install -e $OPENROIS/interfaces/python -e $OPENROIS/engine \
    -e $OPENROIS/components/core
pip install -e "../components[grpc]"    # this repository's Kachaka components
pip install -e ../kachaka-api/python   # kachaka-api, if it is not on PyPI
```

## Configure

Copy `adapter.yaml.example` to `adapter.yaml` and set `robot_address`
to the Kachaka's gRPC API:

```yaml
gateway_url: ws://127.0.0.1:8765
engine_id: kachaka_01
robot_address: 192.168.1.100:26400
log_level: info

navigation:
  auto_homing: false

navigation_information:
  map_name: coarobo-kachaka_01@unicorn
```

Each setting comes from the defaults, then `adapter.yaml`, then the
environment, then the flags. The four settings at the top also come
from `OPENROIS_ADAPTER_GATEWAY_URL`, `OPENROIS_ADAPTER_ENGINE_ID`,
`OPENROIS_ADAPTER_ROBOT_ADDRESS` and `OPENROIS_ADAPTER_LOG_LEVEL`, or
from `--gateway-url`, `--engine-id`, `--robot-address` and
`--log-level`. The sections come only from the file. A setting the
components do not have, such as the old `poll_interval`, is refused.

`openrois-profile.yaml` is no longer read. Its `engine.id`,
`engine.gateway_url` and `environment.grpc_server` become `engine_id`,
`gateway_url` and `robot_address`, `environment.auto_homing` becomes
`navigation.auto_homing`, and `components.NavigationInformation.map_name`
becomes `navigation_information.map_name`.

## Start

```bash
cd api
python kachaka_api_adapter.py --config adapter.yaml
```

The adapter runs until Ctrl+C and reconnects when the gateway goes away.

## Test

```bash
pip install pytest pytest-asyncio
cd api && pytest
```
