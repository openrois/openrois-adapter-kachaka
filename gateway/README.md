# OpenRoIS Kachaka Thin Gateway

WebSocket + JSON-RPC 2.0 gateway for the OpenRoIS Kachaka integration. This is
a **thin placeholder** that wires the `KachakaAdapter` (or `MockAdapter`) to a
WebSocket server. When the monorepo's `openrois-engine` and `openrois-gateway`
packages land (M1/M2), `handler.py` and `server.py` are replaced by monorepo
imports. The adapter does not change.

## Architecture

```
WebSocket Client
    │  JSON-RPC 2.0 over WebSocket
    ▼
WSServer (server.py)     ── accepts connections, parses JSON-RPC, sends responses
    │
    ▼
RoISHandler (handler.py) ── maps method names to BusAdapter calls
    │
    ▼
KachakaAdapter (bus-adapter/) ── gRPC to Kachaka robot
    │
    ▼
Kachaka robot (port 26400)
```

## Files

| File | Responsibility |
|------|----------------|
| `main.py` | Composition root: argparse, create adapter (real or mock), handler, server |
| `handler.py` | RoISHandler: JSON-RPC method dispatch to BusAdapter |
| `server.py` | WSServer: WebSocket accept, JSON-RPC framing, event push |
| `mock_adapter.py` | MockAdapter: hardcoded BusAdapter responses for testing without a robot |

## Prerequisites

- Python 3.12+
- `openrois-interfaces` (vendored wheel in `vendor/`)
- `websockets` (`pip install websockets`)
- `openrois-bus-kachaka` (from `bus-adapter/`, only needed without `--mock`)

## Usage

### With a real Kachaka robot

```bash
pip install websockets
pip install ../vendor/openrois_interfaces-0.1.0a2-py3-none-any.whl
pip install -e ../bus-adapter/

python main.py --target 192.168.1.100:26400 --port 8765
```

### With the mock adapter (no robot needed)

```bash
pip install websockets
pip install ../vendor/openrois_interfaces-0.1.0a2-py3-none-any.whl

python main.py --mock --port 8765
```

## JSON-RPC Methods

### Implemented

| Method | Behavior |
|--------|----------|
| `rois.system.connect` | No-op, returns `OK` |
| `rois.system.disconnect` | No-op, returns `OK` |
| `rois.command.search` | Routes to `adapter.discover()` |
| `rois.command.bind` | No-op, returns `OK` (no lifecycle in thin gateway) |
| `rois.command.release` | No-op, returns `OK` |
| `rois.command.set_parameter` | Routes to `adapter.invoke(SET_PARAMETER)` |
| `rois.command.execute` | Routes to `adapter.invoke(EXECUTE)` |
| `rois.query.query` | Routes to `adapter.query()` |
| `rois.event.subscribe` | Routes to `adapter.subscribe()`, pushes events to client |
| `rois.event.unsubscribe` | Routes to `adapter.unsubscribe()` |

### Server-to-client notifications (push)

| Notification | When |
|--------------|------|
| `rois.event.notify` | Component event (e.g. `reached_target`) |
| `rois.command.completed` | Command completion |

### Not implemented (returns `UNSUPPORTED`)

`rois.system.get_profile`, `rois.command.get_parameter`,
`rois.command.get_command_result`, `rois.command.bind_any`,
`rois.event.get_event_detail`, `rois.stream.*`

## End-to-end test

Connect a WebSocket client and send these JSON-RPC requests:

1. `rois.system.connect` -> `OK`
2. `rois.command.search` -> `["kachaka/Navigation", "kachaka/SystemInformation"]`
3. `rois.command.bind` with `component_ref: "kachaka/Navigation"` -> `OK`
4. `rois.query.query` with `query_type: "robot_position"` -> x, y, theta
5. `rois.query.query` with `query_type: "waypoints"` -> list of locations
6. `rois.event.subscribe` with `event_type: "reached_target"` -> `subscribe_id`
7. `rois.command.execute` with `target_positions: ["Kitchen"]` -> `command_id`
8. Receive `rois.event.notify` notification when robot arrives
9. Receive `rois.command.completed` notification
10. `rois.event.unsubscribe` -> `OK`
11. `rois.system.disconnect` -> `OK`

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | `192.168.1.100:26400` | Kachaka gRPC target address |
| `--host` | `0.0.0.0` | WebSocket bind host |
| `--port` | `8765` | WebSocket bind port |
| `--poll-interval` | `0.5` | Event poll interval (seconds) |
| `--mock` | off | Use mock adapter instead of real Kachaka |

## License

Copyright 2026 Coarobo GK. See repository LICENSE.