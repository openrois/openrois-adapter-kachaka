"""OpenRoIS adapter for the Kachaka robot using ROS 2.

Component handlers are in the components/ package, one file per
component. The rclpy node is spun in a background thread by the
AdapterFramework.

Usage:
    python kachaka_ros2_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import logging

import rclpy

from openrois.sdk import AdapterFramework, RobotAdapter, load_config

from .components import MapObserver, Navigation, SystemInformation
from .kachaka_ros2_client import KachakaRos2Client

logger = logging.getLogger(__name__)


class KachakaRos2Adapter(RobotAdapter):
    """Adapter for the Kachaka robot via ROS 2."""

    SystemInformation = SystemInformation
    Navigation = Navigation
    MapObserver = MapObserver

    def __init__(self, config: dict) -> None:
        if not rclpy.ok():
            rclpy.init()

        self.node = KachakaRos2Client(config)

        map_config = config.get("components", {}).get("MapObserver", {})
        self._map_name = map_config.get("map_name", "")
        self._map_resolution = float(map_config.get("map_resolution", 0.0))
        self._map_origin_x = float(map_config.get("map_origin_x", 0.0))
        self._map_origin_y = float(map_config.get("map_origin_y", 0.0))
        self._map_width = int(map_config.get("map_width", 0))
        self._map_height = int(map_config.get("map_height", 0))

        self._nav_busy = False
        self._nav_target = ""

        super().__init__(config)

    async def connect(self) -> None:
        if not self.node._action_client.wait_for_server(timeout_sec=5.0):
            logger.warning(
                "Action server not available after 5s, "
                "commands will fail until the bridge is ready",
            )
        logger.info("ROS 2 adapter connected")

    async def disconnect(self) -> None:
        self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Kachaka OpenRoIS adapter (ROS 2)",
    )
    parser.add_argument(
        "--config",
        default="openrois-profile.yaml",
        help="Path to the profile YAML file",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = KachakaRos2Adapter(config)
    framework = AdapterFramework(adapter, config)
    framework.run()


if __name__ == "__main__":
    main()
