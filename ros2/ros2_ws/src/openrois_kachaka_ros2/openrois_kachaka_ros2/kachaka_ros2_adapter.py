"""OpenRoIS adapter for the Kachaka robot using ROS 2.

Translates RoIS JSON-RPC to ROS 2 topic reads and action client calls.
Uses the openrois-adapter-sdk per-component handler pattern. The rclpy
node is spun in a background thread by the AdapterFramework.

Implements the canonical RoIS components:
- SystemInformation: robot_position from odometry topic, engine_status.
- Navigation: get_parameter returns target_positions (location names),
  component_status from action state, execute via ExecKachakaCommand
  action, stop cancels the action, reached_target events via action
  result callback (no polling).
- MapObserver: waypoints (location list with coordinates), map_info,
  component_status. Custom component for spatial visualization data.

Usage:
    python kachaka_ros2_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import json
import logging

import rclpy

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

from .kachaka_ros2_client import KachakaRos2Client

logger = logging.getLogger(__name__)


class KachakaRos2Adapter(RobotAdapter):
    """Adapter for the Kachaka robot via ROS 2."""

    def __init__(self, config: dict) -> None:
        # Initialize rclpy if not already initialized.
        if not rclpy.ok():
            rclpy.init()

        # Create the rclpy node. The framework detects self.node and
        # spins it in a background thread via MultiThreadedExecutor.
        self.node = KachakaRos2Client(config)

        # Map metadata from config. The operator app loads the map
        # image from StreamingAssets using map_name as the filename.
        map_config = config.get("map", {})
        self._map_name = map_config.get("map_name", "")
        self._map_resolution = map_config.get("resolution", 0.0)
        self._map_origin_x = map_config.get("origin_x", 0.0)
        self._map_origin_y = map_config.get("origin_y", 0.0)
        self._map_width = map_config.get("width", 0)
        self._map_height = map_config.get("height", 0)

        self._nav_busy = False
        self._nav_target = ""

        super().__init__(config)

    async def connect(self) -> None:
        """Wait for the action server to become available.

        The rclpy node is already created and spinning. This method
        waits for the ExecKachakaCommand action server to be ready.
        """
        if not self.node._action_client.wait_for_server(timeout_sec=5.0):
            logger.warning(
                "Action server not available after 5s, "
                "commands will fail until the bridge is ready",
            )
        logger.info("ROS 2 adapter connected")

    async def disconnect(self) -> None:
        """Clean up the rclpy node."""
        self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    # ─── SystemInformation ─────────────────────────────────────

    @component("SystemInformation", bind_required=False)
    class SystemInformation:

        @query("robot_position")
        async def robot_position(self):
            pose = self.parent.node.get_pose()
            if pose is None:
                return results.position(x=0.0, y=0.0, theta=0.0)
            return results.position(x=pose[0], y=pose[1], theta=pose[2])

        @query("engine_status")
        async def engine_status(self):
            # SystemInformation does not inherit RoIS_Common.
            # engine_status returns the engine's component status and
            # operable time. The adapter always reports READY.
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
            locations = self.parent.node.get_locations()
            if locations is None:
                target_positions: list[str] = []
            else:
                target_positions = [loc["name"] for loc in locations]
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
            if self.parent.node.is_command_running():
                return results.status("BUSY")
            return results.status("READY")

        @invoke("execute")
        async def navigate(self, parameters):
            if self.parent.node.is_command_running():
                return InvokeResponse(
                    return_code=ReturnCode.ERROR, command_id="",
                )
            target = parameters[0].value if parameters else ""
            # Resolve location name to ID.
            locations = self.parent.node.get_locations()
            if locations is None:
                return InvokeResponse(
                    return_code=ReturnCode.BAD_PARAMETER, command_id="",
                )
            loc = next(
                (l for l in locations if l["name"] == target),
                None,
            )
            if loc is None:
                return InvokeResponse(
                    return_code=ReturnCode.BAD_PARAMETER, command_id="",
                )
            accepted = self.parent.node.send_move_to_location(loc["id"])
            if not accepted:
                return InvokeResponse(
                    return_code=ReturnCode.ERROR, command_id="",
                )
            self.parent._nav_busy = True
            self.parent._nav_target = target
            return InvokeResponse(
                return_code=ReturnCode.OK, command_id="cmd-nav",
            )

        @invoke("stop")
        async def stop(self, parameters):
            self.parent.node.cancel_command()
            self.parent._nav_busy = False
            return InvokeResponse(return_code=ReturnCode.OK, command_id="")

        @subscribe("reached_target")
        async def on_reached(self):
            """Register a result callback on the node.

            When the action completes, the node calls the callback
            from the rclpy thread. The callback calls self.parent.emit()
            which is thread-safe (uses asyncio.run_coroutine_threadsafe).
            """
            def on_action_result(success: bool) -> None:
                self.parent._nav_busy = False
                target_name = getattr(self.parent, "_nav_target", "")
                self.parent.emit(
                    "Navigation",
                    "reached_target",
                    results.reached_target(
                        target=target_name, is_final_target=True,
                    ),
                )
                logger.info(
                    "Fired reached_target event (success=%s)", success,
                )

            self.parent.node.set_result_callback(on_action_result)

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
            locations = self.parent.node.get_locations()
            if locations is None:
                return results.waypoints([])
            return results.waypoints(locations)

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
    """Entry point: load config, run framework."""
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Kachaka OpenRoIS adapter (ROS 2)",
    )
    parser.add_argument(
        "--config",
        default="openrois-profile.yaml",
        help="Path to the profile YAML file (default: openrois-profile.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = KachakaRos2Adapter(config)

    framework = AdapterFramework(adapter, config)
    framework.run()


if __name__ == "__main__":
    main()
