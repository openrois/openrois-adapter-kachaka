"""OpenRoIS adapter for the Kachaka robot using the gRPC API.

Translates RoIS JSON-RPC to Kachaka gRPC calls. Uses the
openrois-adapter-sdk per-component handler pattern.

Implements the canonical RoIS components:
- SystemInformation: robot_position from get_robot_pose(), engine_status.
- Navigation: get_parameter returns target_positions (location names),
  component_status from get_command_state(), execute via
  move_to_location(), stop via cancel_command(),
  reached_target events via polling.
- MapObserver: waypoints (location list with coordinates), map_info,
  component_status. Custom component for spatial visualization data.

Usage:
    python kachaka_api_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from openrois.interfaces.bus import InvokeResponse
from openrois.interfaces.hri import ReturnCode, Result
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


class KachakaApiAdapter(RobotAdapter):
    """Adapter for the Kachaka robot via the kachaka-api Python library."""

    def __init__(self, config: dict) -> None:
        robot_config = config.get("robot", {})
        self._grpc_server = robot_config.get(
            "grpc_server", "192.168.1.100:26400",
        )
        self._auto_homing = robot_config.get("auto_homing", False)
        self._poll_interval = robot_config.get("poll_interval_sec", 0.5)

        # Map metadata from config. The operator app loads the map
        # image from StreamingAssets using map_name as the filename.
        map_config = config.get("map", {})
        self._map_name = map_config.get("map_name", "")
        self._map_resolution = map_config.get("resolution", 0.0)
        self._map_origin_x = map_config.get("origin_x", 0.0)
        self._map_origin_y = map_config.get("origin_y", 0.0)
        self._map_width = map_config.get("width", 0)
        self._map_height = map_config.get("height", 0)

        # Defer gRPC client creation to connect() so it binds to the
        # framework's event loop, not a default loop from __init__.
        self._client = None
        self._pb2 = None
        self._connected = False
        self._nav_busy = False
        self._nav_target = ""
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

        @query("engine_status")
        async def engine_status(self):
            # SystemInformation does not inherit RoIS_Common.
            # engine_status returns the engine's component status.
            return [
                Result(
                    name="status",
                    data_type_ref="string",
                    value="READY",
                ),
            ]

    # ─── Navigation ─────────────────────────────────────────────

    @component("Navigation", bind_required=True)
    class Navigation:

        @query("get_parameter")
        async def get_parameter(self):
            """Return target_positions (location names as string[]),
            time_limit, and routing_policy per the canonical Navigation spec.
            """
            locations = await self.parent._client.get_locations()
            self.parent._locations_cache = list(locations)
            target_positions = [loc.name for loc in locations]
            return [
                Result(
                    name="target_positions",
                    data_type_ref="string[]",
                    value=json.dumps(target_positions),
                ),
                Result(
                    name="time_limit",
                    data_type_ref="int",
                    value="0",
                ),
                Result(
                    name="routing_policy",
                    data_type_ref="string",
                    value="time",
                ),
            ]

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
            target = ""
            if parameters:
                # Parameters are dicts with name, data_type_ref, value.
                p = parameters[0]
                raw_value = p.get("value", "") if isinstance(p, dict) else getattr(p, "value", "")
                # The spec defines target_positions as string[] (a JSON
                # array). Parse the JSON and take the first element.
                # Some clients may send a plain string instead.
                try:
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, list) and parsed:
                        target = parsed[0]
                    else:
                        target = raw_value
                except (json.JSONDecodeError, TypeError):
                    target = raw_value
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
            self.parent._nav_target = target
            # Start polling for command completion. This runs
            # independently of the subscribe handler so it works
            # even if reached_target was subscribed only once.
            loop = asyncio.get_event_loop()
            loop.create_task(self._poll_reached())
            return InvokeResponse(return_code=ReturnCode.OK, command_id="cmd-nav")

        @invoke("STOP")
        async def stop(self, parameters):
            await self.parent._client.cancel_command()
            self.parent._nav_busy = False
            return InvokeResponse(return_code=ReturnCode.OK, command_id="")

        @subscribe("reached_target")
        async def on_reached(self):
            # The poll loop is started in navigate() after each
            # command is issued. This handler exists so the framework
            # registers the reached_target event subscription.
            pass

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
                    result, _ = (
                        await self.parent._client.get_last_command_result()
                    )
                    success = result.success
                    target_name = getattr(self.parent, "_nav_target", "")
                    await self.parent.emit_async(  # type: ignore[attr-defined]
                        "Navigation",
                        "reached_target",
                        results.reached_target(
                            target=target_name, is_final_target=True,
                        ),
                    )
                    logger.info(
                        "Fired reached_target event (success=%s)", success,
                    )
                    return

    # ─── MapObserver ───────────────────────────────────────────

    @component("MapObserver", bind_required=False)
    class MapObserver:
        """Custom component providing spatial data for visualization.

        Returns waypoints with coordinates (id, name, x, y, theta) and
        map metadata. This is robot-specific data that does not belong
        in the canonical Navigation component.
        """

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

        @query("map_info")
        async def get_map_info(self):
            # Map metadata from the adapter config. The operator app
            # loads the map image from StreamingAssets using map_name
            # as the filename (e.g. kachaka_map.webp).
            return [
                Result(
                    name="map_name",
                    data_type_ref="string",
                    value=self.parent._map_name,
                ),
                Result(
                    name="resolution",
                    data_type_ref="float",
                    value=str(self.parent._map_resolution),
                ),
                Result(
                    name="origin_x",
                    data_type_ref="float",
                    value=str(self.parent._map_origin_x),
                ),
                Result(
                    name="origin_y",
                    data_type_ref="float",
                    value=str(self.parent._map_origin_y),
                ),
                Result(
                    name="width",
                    data_type_ref="int",
                    value=str(self.parent._map_width),
                ),
                Result(
                    name="height",
                    data_type_ref="int",
                    value=str(self.parent._map_height),
                ),
            ]

        @query("component_status")
        async def status(self):
            return results.status("READY")


def main() -> None:
    """Entry point: load config, run framework.

    The framework calls adapter.connect() inside its own event loop
    so that the gRPC channel binds to the same loop as the WebSocket.
    """
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Kachaka OpenRoIS adapter")
    parser.add_argument(
        "--config",
        default="openrois-profile.yaml",
        help="Path to the profile YAML file (default: openrois-profile.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = KachakaApiAdapter(config)

    framework = AdapterFramework(adapter, config)
    framework.run()


if __name__ == "__main__":
    main()
