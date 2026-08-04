"""CAPF Kachaka Sub HRI Engine.

Connects to the avatar app's WebSocket server and translates RoIS JSON-RPC
commands to Kachaka gRPC API calls. This is the Sub HRI Engine in the RoIS
framework: the avatar is the (main) HRI Engine, and this script is the
physical unit that provides the HRI Components (Navigation, SystemInformation).

Architecture:

    Operator (Service Application)
        ↓ cmdWs v2 envelope
    Avatar app (HRI Engine, main process)
        ↓ WebSocket localhost:PORT
    This script (Sub HRI Engine)
        ↓ gRPC
    Kachaka robot

This script does NOT import from the existing openrois-kachaka components
(bus-adapter, gateway). It reimplements the BusAdapter logic directly,
constrained to this directory, using the kachaka_api package for gRPC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from typing import Any

import websockets
from kachaka_api.aio import KachakaApiClient
from kachaka_api.generated import kachaka_api_pb2 as pb2

logger = logging.getLogger(__name__)

# Static component list advertised by discover().
COMPONENT_REFS = ["kachaka/Navigation", "kachaka/SystemInformation"]

# JSON-RPC version string.
JSONRPC_VERSION = "2.0"


class KachakaSubHriEngine:
    """Sub HRI Engine that bridges RoIS JSON-RPC to Kachaka gRPC.

    Receives JSON-RPC requests over WebSocket from the avatar's
    RobotBusAdapterServer, translates them to Kachaka gRPC calls,
    and sends JSON-RPC responses back. Also polls command state and
    pushes event notifications for active subscriptions.
    """

    def __init__(self, target: str, poll_interval: float = 0.5) -> None:
        self._target = target
        self._client = KachakaApiClient(target=target)
        self._poll_interval = poll_interval
        self._connected = False
        # Stored parameters from SET_PARAMETER, used by later EXECUTE.
        self._stored_target_positions: list[str] = []
        self._stored_time_limit: int = 0
        self._stored_routing_policy: str = "time"
        # Active subscriptions: subscribe_id → asyncio.Task.
        self._pollers: dict[str, asyncio.Task] = None  # type: ignore
        # WebSocket connection (set on connect).
        self._ws: Any = None

    async def connect(self) -> None:
        """Connect to the Kachaka robot and initialize the resolver."""
        await self._client.update_resolver()
        await self._client.set_auto_homing_enabled(False)
        self._connected = True
        self._pollers = {}
        logger.info("Connected to Kachaka at %s", self._target)

    async def close(self) -> None:
        """Close the gRPC connection and stop all pollers."""
        for sub_id in list(self._pollers or {}):
            await self.unsubscribe(sub_id)
        channel = getattr(self._client.stub, "_channel", None)
        if channel is not None:
            await channel.close()
        self._connected = False

    async def handle_message(self, raw: str) -> str | None:
        """Handle a JSON-RPC request from the avatar and return the response.

        Returns None for notifications (no response expected).
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Parse error: %s", raw)
            return None

        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if msg_id is None or method is None:
            # Notification (no id). Not expected from the avatar.
            return None

        try:
            result = await self.dispatch(method, params)
        except Exception:
            logger.exception("Dispatch error for %s", method)
            result = {"return_code": "ERROR"}

        response = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}
        return json.dumps(response)

    async def dispatch(self, method: str, params: dict) -> dict:
        """Route a RoIS method to the appropriate Kachaka gRPC call."""
        if method == "rois.system.connect":
            return {"return_code": "OK"}

        if method == "rois.system.disconnect":
            return {"return_code": "OK"}

        if method == "rois.command.search":
            return {"return_code": "OK", "component_ref_list": list(COMPONENT_REFS)}

        if method == "rois.command.bind":
            return {"return_code": "OK"}

        if method == "rois.command.release":
            return {"return_code": "OK"}

        if method == "rois.command.execute":
            return await self.handle_execute(params)

        if method == "rois.query.query":
            return await self.handle_query(params)

        if method == "rois.event.subscribe":
            return await self.handle_subscribe(params)

        if method == "rois.event.unsubscribe":
            return await self.handle_unsubscribe(params)

        return {"return_code": "UNSUPPORTED"}

    # ─── Command Interface ──────────────────────────────────────

    async def handle_execute(self, params: dict) -> dict:
        """Execute a command on the Kachaka robot.

        Routes by command_type:
        - EXECUTE: resolve target location, call move_to_location
        - STOP: cancel the running command
        - SET_PARAMETER: store parameters for later EXECUTE
        - START/SUSPEND/RESUME: no-ops for Navigation
        """
        command_type = str(params.get("command_type", "EXECUTE"))

        if command_type == "EXECUTE":
            return await self._execute_move(params)

        if command_type == "STOP":
            result, _ = await self._client.cancel_command()
            return {"return_code": "OK" if result.success else "ERROR"}

        if command_type == "SET_PARAMETER":
            return self._set_parameter(params)

        if command_type in ("START", "SUSPEND", "RESUME"):
            return {"return_code": "OK"}

        return {"return_code": "UNSUPPORTED"}

    async def _execute_move(self, params: dict) -> dict:
        """Resolve the target location and call move_to_location."""
        target: str | None = None

        # Priority 1: parameters list (target_positions as JSON array string).
        raw_params = params.get("parameters", [])
        if isinstance(raw_params, list):
            for param in raw_params:
                if isinstance(param, dict) and param.get("name") == "target_positions":
                    try:
                        positions = json.loads(str(param.get("value", "")))
                        if isinstance(positions, list) and positions:
                            target = str(positions[0])
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Priority 2: stored target_positions from a prior SET_PARAMETER.
        if target is None and self._stored_target_positions:
            target = self._stored_target_positions[0]

        if not target:
            return {"return_code": "BAD_PARAMETER", "command_id": ""}

        # Validate the location exists.
        locations = await self._client.get_locations()
        known_names = {loc.name for loc in locations}
        known_ids = {loc.id for loc in locations}
        if target not in known_names and target not in known_ids:
            return {"return_code": "BAD_PARAMETER", "command_id": ""}

        # Fire-and-forget: wait_for_completion=False returns immediately.
        result = await self._client.move_to_location(
            target,
            wait_for_completion=False,
        )

        if not result.success:
            return {"return_code": "ERROR", "command_id": ""}

        cmd_id = f"kachaka-cmd-{uuid.uuid4().hex[:8]}"
        return {"return_code": "OK", "command_id": cmd_id}

    def _set_parameter(self, params: dict) -> dict:
        """Store parameters for later EXECUTE."""
        raw_params = params.get("parameters", [])
        if isinstance(raw_params, list):
            for param in raw_params:
                if not isinstance(param, dict):
                    continue
                name = param.get("name")
                value = str(param.get("value", ""))
                if name == "target_positions":
                    self._stored_target_positions = [
                        v.strip() for v in value.split(",") if v.strip()
                    ]
                elif name == "time_limit":
                    try:
                        self._stored_time_limit = int(value)
                    except ValueError:
                        pass
                elif name == "routing_policy":
                    self._stored_routing_policy = value
        return {"return_code": "OK"}

    # ─── Query Interface ──────────────────────────────────────────

    async def handle_query(self, params: dict) -> dict:
        """Query the Kachaka robot for information.

        Routes by query_type:
        - robot_position: get_robot_pose()
        - waypoints: get_locations()
        - component_status: get_command_state()
        """
        query_type = str(params.get("query_type", ""))

        if query_type == "robot_position":
            pose = await self._client.get_robot_pose()
            return {
                "return_code": "OK",
                "results": [{
                    "name": "robot_position",
                    "data_type_ref": "SystemInformationRobotPositionResult",
                    "value": json.dumps({
                        "x": pose.x,
                        "y": pose.y,
                        "theta": pose.theta,
                    }),
                }],
            }

        if query_type == "waypoints":
            locations = await self._client.get_locations()
            results = []
            for loc in locations:
                results.append({
                    "name": loc.name,
                    "data_type_ref": "waypoint",
                    "value": json.dumps({
                        "id": loc.id,
                        "name": loc.name,
                        "pose": {
                            "x": loc.pose.x,
                            "y": loc.pose.y,
                            "theta": loc.pose.theta,
                        },
                    }, ensure_ascii=False),
                })
            return {"return_code": "OK", "results": results}

        if query_type == "component_status":
            state, _ = await self._client.get_command_state()
            status = "BUSY" if state == pb2.CommandState.COMMAND_STATE_RUNNING else "READY"
            return {
                "return_code": "OK",
                "results": [{
                    "name": "component_status",
                    "data_type_ref": "NavigationStatusResult",
                    "value": status,
                }],
            }

        return {"return_code": "UNSUPPORTED", "results": []}

    # ─── Event Interface ─────────────────────────────────────────

    async def handle_subscribe(self, params: dict) -> dict:
        """Subscribe to command completion events.

        Starts a polling task that monitors get_command_state() and emits
        reached_target and completed events when a command finishes.
        """
        subscribe_id = f"kachaka-sub-{uuid.uuid4().hex[:8]}"
        task = asyncio.create_task(self._poll_loop(subscribe_id))
        self._pollers[subscribe_id] = task
        return {"return_code": "OK", "subscribe_id": subscribe_id}

    async def handle_unsubscribe(self, params: dict) -> dict:
        """Cancel an event subscription."""
        subscribe_id = str(params.get("subscribe_id", ""))
        task = self._pollers.pop(subscribe_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return {"return_code": "OK"}

    async def _poll_loop(self, subscribe_id: str) -> None:
        """Poll command state and emit events on completion.

        State machine:
        - If RUNNING: set was_running = True
        - If not RUNNING and was_running: command finished, emit events
        - If not RUNNING and not was_running but command_id changed: fast completion
        """
        was_running = False
        baseline_cmd_id: str | None = None

        try:
            while True:
                state, command = await self._client.get_command_state()
                cmd_id = self._extract_command_id(command)

                if baseline_cmd_id is None:
                    baseline_cmd_id = cmd_id
                    if state == pb2.CommandState.COMMAND_STATE_RUNNING:
                        was_running = True
                    await asyncio.sleep(self._poll_interval)
                    continue

                if state == pb2.CommandState.COMMAND_STATE_RUNNING:
                    was_running = True
                elif was_running:
                    was_running = False
                    await self._emit_events(subscribe_id, command)
                elif (
                    cmd_id
                    and cmd_id != baseline_cmd_id
                ):
                    await self._emit_events(subscribe_id, command)

                baseline_cmd_id = cmd_id
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.debug("Poller for %s cancelled", subscribe_id)
            raise

    async def _emit_events(self, subscribe_id: str, command: pb2.Command) -> None:
        """Emit reached_target and/or completed events to the avatar."""
        result, last_command = await self._client.get_last_command_result()
        target = self._extract_target(last_command)

        if result.success:
            # Emit reached_target.
            await self._send_notification(subscribe_id, "reached_target", [
                {"name": "target", "data_type_ref": "string", "value": target},
                {"name": "is_final_target", "data_type_ref": "bool", "value": "true"},
            ])
            # Emit completed with OK.
            await self._send_notification(subscribe_id, "completed", [], "OK")
        else:
            # Emit completed with ERROR.
            await self._send_notification(subscribe_id, "completed", [], "ERROR")

    async def _send_notification(
        self,
        subscribe_id: str,
        event_type: str,
        results: list,
        completed_status: str | None = None,
    ) -> None:
        """Send a JSON-RPC notification to the avatar via WebSocket."""
        params: dict[str, Any] = {
            "subscribe_id": subscribe_id,
            "event_type": event_type,
            "results": results,
        }
        if completed_status is not None:
            params["completed_status"] = completed_status

        notification = {
            "jsonrpc": JSONRPC_VERSION,
            "method": "rois.event.notification",
            "params": params,
        }
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(notification))
                logger.info("Sent notification: %s for %s", event_type, subscribe_id)
            except Exception:
                logger.exception("Failed to send notification")

    # ─── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_target(command: pb2.Command) -> str:
        """Extract the target location from a Kachaka Command."""
        if command.HasField("move_to_location_command"):
            return command.move_to_location_command.target_location_id
        return ""

    @staticmethod
    def _extract_command_id(command: pb2.Command) -> str:
        """Extract a stable identity from a Kachaka Command."""
        oneof = command.WhichOneof("command")
        if oneof is None:
            return ""
        return command.SerializeToString().hex()


async def run_websocket_loop(
    engine: KachakaSubHriEngine,
    ws_url: str,
    reconnect_delay: float,
) -> None:
    """Connect to the avatar WebSocket, reconnecting on disconnect.

    Retries every reconnect_delay seconds until the connection succeeds, then
    serves requests. On disconnect, waits reconnect_delay seconds and retries.
    Exits cleanly on cancellation (Ctrl+C).
    """
    while True:
        try:
            logger.info("Connecting to avatar at %s", ws_url)
            async with websockets.connect(ws_url) as ws:
                engine._ws = ws
                logger.info("Connected to avatar HRI Engine")
                async for raw in ws:
                    response = await engine.handle_message(raw)
                    if response is not None:
                        await ws.send(response)
                        logger.debug("Sent response: %s", response[:100])
            logger.info(
                "WebSocket disconnected, reconnecting in %.1fs", reconnect_delay
            )
        except asyncio.CancelledError:
            logger.info("Shutting down")
            return
        except Exception:
            logger.exception(
                "WebSocket connection failed, reconnecting in %.1fs",
                reconnect_delay,
            )
        finally:
            engine._ws = None

        try:
            await asyncio.sleep(reconnect_delay)
        except asyncio.CancelledError:
            return


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="CAPF Kachaka Sub HRI Engine"
    )
    parser.add_argument(
        "--target",
        default="192.168.100.203:26400",
        help="Kachaka gRPC target (default: 192.168.100.203:26400)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=31166,
        help="Avatar WebSocket server port (default: 31166)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Avatar WebSocket server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Command state poll interval in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=10.0,
        help="WebSocket reconnect delay in seconds (default: 10.0)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = KachakaSubHriEngine(target=args.target, poll_interval=args.poll_interval)
    await engine.connect()

    ws_url = f"ws://{args.host}:{args.port}"
    try:
        await run_websocket_loop(
            engine,
            ws_url,
            args.reconnect_delay,
        )
    finally:
        await engine.close()
        logger.info("Disconnected")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass