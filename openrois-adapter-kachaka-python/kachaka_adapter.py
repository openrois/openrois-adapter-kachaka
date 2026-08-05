"""OpenRoIS adapter for the Kachaka robot using the gRPC API.

Translates RoIS JSON-RPC to Kachaka gRPC calls. Uses the
openrois-adapter-sdk per-component handler pattern.

Currently implements:
- SystemInformation: robot position from get_robot_pose()
- Navigation: WayPoints from get_locations(), navigation via
  move_to_location(), status from get_command_state(),
  reached_target events via polling.

Usage:
    python kachaka_adapter.py --config profile.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from openrois.interfaces.bus import InvokeResponse
from openrois.interfaces.hri import ReturnCode
from openrois.sdk import (
    AdapterFramework,
    RobotAdapter,
    component,
    invoke,
    load_config,
    query,
    results,
    subscribe,
)

logger = logging.getLogger(__name__)


class KachakaAdapter(RobotAdapter):
    """Adapter for the Kachaka robot via gRPC."""

    def __init__(self, config: dict) -> None:
        robot_config = config.get("robot", {})
        self._grpc_server = robot_config.get(
            "grpc_server", "192.168.1.100:26400",
        )
        self._auto_homing = robot_config.get("auto_homing", False)
        self._poll_interval = robot_config.get("poll_interval_sec", 0.5)

        # Defer gRPC client creation to connect() so it binds to the
        # framework's event loop, not a default loop from __init__.
        self._client = None
        self._pb2 = None
        self._connected = False
        self._nav_busy = False
        self._locations_cache: list | None = None

        super().__init__(config)

    async def connect(self) -> None:
        """Connect to Kachaka and initialize the resolver.

        Creates the gRPC client here (not in __init__) so it binds
        to the framework's event loop.
        """
        from kachaka_api.aio import KachakaApiClient
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        self._client = KachakaApiClient(target=self._grpc_server)
        self._pb2 = pb2
        await self._client.update_resolver()
        await self._client.set_auto_homing_enabled(self._auto_homing)
        self._connected = True
        logger.info("Connected to Kachaka at %s", self._grpc_server)

    # ─── SystemInformation ─────────────────────────────────────

    @component("SystemInformation", bind_required=False)
    class SystemInformation:

        @query("robot_position")
        async def robot_position(self):
            pose = await self.parent._client.get_robot_pose()
            return results.position(x=pose.x, y=pose.y, theta=pose.theta)

        @query("component_status")
        async def status(self):
            return results.status("READY")

    # ─── Navigation ─────────────────────────────────────────────

    @component("Navigation", bind_required=True)
    class Navigation:

        @query("waypoints")
        async def get_waypoints(self):
            locations = await self.parent._client.get_locations()
            self.parent._locations_cache = list(locations)
            return results.waypoints([
                {
                    "id": loc.id,
                    "name": loc.name,
                    "x": loc.pose.x,
                    "y": loc.pose.y,
                    "theta": loc.pose.theta,
                }
                for loc in locations
            ])

        @query("component_status")
        async def status(self):
            state, _ = await self.parent._client.get_command_state()
            if state == self.parent._pb2.CommandState.COMMAND_STATE_RUNNING:
                return results.status("BUSY")
            return results.status("READY")

        @invoke("EXECUTE")
        async def navigate(self, parameters):
            if self.parent._nav_busy:
                return InvokeResponse(
                    return_code=ReturnCode.ERROR, command_id="",
                )
            target = parameters[0].value if parameters else ""
            # Resolve location name to ID.
            if not self.parent._locations_cache:
                locations = await self.parent._client.get_locations()
                self.parent._locations_cache = list(locations)
            loc = next(
                (l for l in self.parent._locations_cache if l.name == target),
                None,
            )
            if loc is None:
                return InvokeResponse(
                    return_code=ReturnCode.BAD_PARAMETER, command_id="",
                )
            await self.parent._client.move_to_location(
                loc.id, wait_for_completion=False,
            )
            self.parent._nav_busy = True
            return InvokeResponse(return_code=ReturnCode.OK, command_id="cmd-nav")

        @invoke("STOP")
        async def stop(self, parameters):
            await self.parent._client.cancel_command()
            self.parent._nav_busy = False
            return InvokeResponse(return_code=ReturnCode.OK, command_id="")

        @subscribe("reached_target")
        async def on_reached(self):
            # Start polling for command completion.
            loop = asyncio.get_event_loop()
            loop.create_task(self._poll_reached())

        async def _poll_reached(self):
            """Poll get_command_state() until the command finishes."""
            was_running = False
            while True:
                await asyncio.sleep(self.parent._poll_interval)
                state, _ = await self.parent._client.get_command_state()
                is_running = (
                    state == self.parent._pb2.CommandState.COMMAND_STATE_RUNNING
                )
                if is_running:
                    was_running = True
                elif was_running:
                    # Command just finished.
                    self.parent._nav_busy = False
                    last_result = (
                        await self.parent._client.get_last_command_result()
                    )
                    success = last_result.success
                    self.parent.emit(  # type: ignore[attr-defined]
                        "Navigation",
                        "reached_target",
                        results.reached_target(target="target", success=success),
                    )
                    logger.info(
                        "Fired reached_target event (success=%s)", success,
                    )
                    return


def main() -> None:
    """Entry point: load config, run framework.

    The framework calls adapter.connect() inside its own event loop
    so that the gRPC channel binds to the same loop as the WebSocket.
    """
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Kachaka OpenRoIS adapter")
    parser.add_argument(
        "--config",
        default="profile.yaml",
        help="Path to the profile YAML file (default: profile.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = KachakaAdapter(config)

    framework = AdapterFramework(adapter, config)
    framework.run()


if __name__ == "__main__":
    main()
