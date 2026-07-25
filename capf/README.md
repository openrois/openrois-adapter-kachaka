# CAPF Kachaka Sub HRI Engine

A Sub HRI Engine that connects the Kachaka robot to the CAPF avatar
application via the RoIS protocol.

## Overview

The avatar app runs a WebSocket server on `localhost:8765`. This script
connects to it as a Sub HRI Engine. When the avatar receives a RoIS
JSON-RPC command from an operator, it forwards it to this script, which
translates it to Kachaka gRPC calls and returns the result.

## Installation

```bash
source .venv/bin/activate
cd openrois-kachaka/capf
pip install -e ../bus-adapter
pip install kachaka-api
pip install websockets
```

## Usage

```bash
source .venv/bin/activate
python main.py --target 192.168.100.203:26400 --port 8765
```

Connects to the avatar's WebSocket server on `ws://localhost:8765`
and the Kachaka robot at the given gRPC target.

## RoIS methods supported

| Method | Kachaka gRPC call |
|---|---|
| `rois.system.connect` | `update_resolver()`, `set_auto_homing_enabled()` |
| `rois.command.search` | Returns static component list |
| `rois.command.execute` | `move_to_location()` (EXECUTE), `cancel_command()` (STOP) |
| `rois.query.query` | `get_robot_pose()`, `get_locations()`, `get_command_state()` |
| `rois.event.subscribe` | Polls `get_command_state()`, emits `reached_target` and `completed` |
| `rois.event.unsubscribe` | Stops the poller |