"""WebSocket server for the thin gateway.

Accepts WebSocket connections, parses JSON-RPC 2.0 messages, dispatches
requests to the RoISHandler, sends responses back to the client, and pushes
event notifications from the adapter to the client.

Uses the `websockets` library. Does not import kachaka_api or the adapter.
The handler (and thus the adapter) is injected via constructor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable

import websockets
from websockets.asyncio.server import ServerConnection

from openrois.interfaces.bus import EventEnvelope, EventSink
from openrois.interfaces.hri import SubscribeId

from gateway.handler import RoISHandler, SinkFactory

logger = logging.getLogger(__name__)


class WSServer:
    """Thin WebSocket server that speaks JSON-RPC 2.0 to RoIS clients."""

    def __init__(
        self,
        handler: RoISHandler,
        host: str = "0.0.0.0",
        port: int = 8765,
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port

    async def serve(self) -> None:
        """Start the WebSocket server and run until interrupted."""
        logger.info("WebSocket server listening on ws://%s:%d", self._host, self._port)
        async with websockets.serve(self._handle_connection, self._host, self._port):
            await asyncio.Future()  # run forever

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """Handle a single WebSocket connection lifecycle."""
        logger.info("Client connected: %s", websocket.remote_address)
        # Track subscriptions for this connection so we can clean up on disconnect.
        subscribe_ids: set[SubscribeId] = set()

        def make_sink_factory(
            ws: ServerConnection,
            subs: set[SubscribeId],
        ) -> SinkFactory:
            """Create a sink factory that pushes events to this connection."""

            def create_sink(_placeholder_id: SubscribeId) -> EventSink:
                async def sink(envelope: EventEnvelope) -> None:
                    notification = _envelope_to_notification(envelope)
                    try:
                        await ws.send(json.dumps(notification))
                    except websockets.ConnectionClosed:
                        logger.debug("Connection closed while sending event")

                return sink

            return create_sink

        sink_factory = make_sink_factory(websocket, subscribe_ids)

        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps(_jsonrpc_error(None, -32700, "Parse error")))
                    continue

                # Only handle requests (have "method" and "id"). Notifications
                # from client to server are not expected in this protocol.
                if "method" not in message:
                    logger.warning("Received non-request message: %s", message)
                    continue

                msg_id = message.get("id")
                method = message["method"]
                params = message.get("params", {})

                # Track subscribe/unsubscribe for cleanup.
                if method == "rois.event.subscribe":
                    result = await self._handler.dispatch(method, params, sink_factory)
                    sub_id = result.get("subscribe_id", "")
                    if sub_id:
                        subscribe_ids.add(sub_id)
                elif method == "rois.event.unsubscribe":
                    sub_id = str(params.get("subscribe_id", ""))
                    subscribe_ids.discard(sub_id)
                    result = await self._handler.dispatch(method, params)
                else:
                    result = await self._handler.dispatch(method, params)

                # Send the JSON-RPC response.
                response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
                await websocket.send(json.dumps(response))

        except websockets.ConnectionClosed:
            logger.info("Client disconnected: %s", websocket.remote_address)
        finally:
            # Clean up subscriptions for this connection.
            for sub_id in subscribe_ids:
                try:
                    await self._handler.dispatch(
                        "rois.event.unsubscribe",
                        {"subscribe_id": sub_id},
                    )
                except Exception:
                    logger.debug("Error cleaning up subscription %s", sub_id)


def _envelope_to_notification(envelope: EventEnvelope) -> dict[str, object]:
    """Convert an EventEnvelope to a JSON-RPC notification dict.

    The notification method depends on the event_type:
    - "completed" -> rois.command.completed
    - "notify_error" -> rois.system.notify_error
    - everything else -> rois.event.notify
    """
    if envelope.event_type == "completed":
        return {
            "jsonrpc": "2.0",
            "method": "rois.command.completed",
            "params": {
                "command_id": "",
                "status": envelope.completed_status.value if envelope.completed_status else "OK",
                "subscribe_id": envelope.subscribe_id,
            },
        }

    if envelope.event_type == "notify_error":
        return {
            "jsonrpc": "2.0",
            "method": "rois.system.notify_error",
            "params": {
                "error_id": envelope.event_id,
                "error_type": envelope.error_type.value if envelope.error_type else "",
            },
        }

    # Default: rois.event.notify for component events (reached_target, etc.).
    return {
        "jsonrpc": "2.0",
        "method": "rois.event.notify",
        "params": {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "subscribe_id": envelope.subscribe_id,
            "expire": envelope.expire,
            "results": [r.model_dump() for r in envelope.payload],
        },
    }


def _jsonrpc_error(msg_id: object, code: int, message: str) -> dict[str, object]:
    """Create a JSON-RPC 2.0 error response."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }