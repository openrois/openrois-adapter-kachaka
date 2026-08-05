# openrois-adapter-kachaka

OpenRoIS adapter for the Kachaka robot using the gRPC API.

## Install

```bash
pip install openrois-adapter-sdk kachaka-api
```

## Config

Copy `profile.yaml.example` to `profile.yaml` and edit the gRPC target:

```yaml
fleet_id: kachaka
connection:
  ws:
    host: "127.0.0.1"
    port: 8765
robot:
  grpc_server: "192.168.100.203:26400"
  auto_homing: false
  poll_interval_sec: 0.5
```

## Run

```bash
python kachaka_adapter.py --config profile.yaml
```

## Components

- **SystemInformation**: robot position from `get_robot_pose()`.
- **Navigation**: WayPoints from `get_locations()`, navigation via
  `move_to_location()`, status from `get_command_state()`, reached_target
  events via polling.
