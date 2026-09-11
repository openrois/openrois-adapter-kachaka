"""Navigation Information component for the Kachaka robot (ROS 2).

Non-canonical component (user-defined per RoIS spec section 12) that
provides navigation-related spatial data for the operator app:

- destinations: string[] of destination names (discovery).
- destination_positions: [{id, name, x, y, theta}] (coordinates for rendering).

The component owns its own rclpy Node, created in connect() and torn
down in disconnect(). No dependency on the adapter for shared state.
"""

from __future__ import annotations

import json
import logging
import threading

from openrois.interfaces.hri import Result
from openrois_components_core import component, query, results

logger = logging.getLogger(__name__)


@component("NavigationInformation")
class NavigationInformation:
    """Spatial data for navigation: available destinations and positions."""

    def __init__(self, config: dict) -> None:
        self._node_name = config.get("ros2_node_name", "openrois_kachaka")
        self._locations_topic = config.get(
            "locations_topic", "/kachaka/layout/locations/list",
        )
        self._map_topic = config.get(
            "map_topic", "/kachaka/mapping/map",
        )
        self._map_name = config.get("map_name", "")
        self._node = None
        self._connected = False

    async def connect(self) -> None:
        """Create the rclpy node and subscribe to locations and map."""
        import rclpy
        from rclpy.callback_groups import ReentrantCallbackGroup
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from kachaka_interfaces.msg import LocationList
        from nav_msgs.msg import OccupancyGrid

        if not rclpy.ok():
            rclpy.init()

        self._node = Node(f"{self._node_name}_navinfo")
        self._lock = threading.Lock()
        self._latest_locations: list[dict] | None = None
        self._latest_map_info: dict | None = None

        cb_group = ReentrantCallbackGroup()
        latched_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )
        self._node.create_subscription(
            LocationList,
            self._locations_topic,
            self._on_locations,
            latched_qos,
            callback_group=cb_group,
        )
        self._node.create_subscription(
            OccupancyGrid,
            self._map_topic,
            self._on_map,
            latched_qos,
            callback_group=cb_group,
        )
        self._connected = True
        logger.info(
            "NavigationInformation subscribed to %s and %s",
            self._locations_topic,
            self._map_topic,
        )

    async def disconnect(self) -> None:
        """Tear down the rclpy node."""
        self._connected = False
        if self._node is not None:
            self._node.destroy_node()
            self._node = None

    def _on_locations(self, msg) -> None:
        """Cache the latest location list."""
        locations = []
        for loc in msg.locations:
            locations.append({
                "id": loc.id,
                "name": loc.name,
                "x": loc.pose.x,
                "y": loc.pose.y,
                "theta": loc.pose.theta,
            })
        with self._lock:
            self._latest_locations = locations
        logger.info("Received %d locations", len(locations))

    def _get_locations(self) -> list[dict] | None:
        with self._lock:
            return self._latest_locations

    def _on_map(self, msg) -> None:
        """Cache the latest map metadata from the OccupancyGrid."""
        info = {
            "map_name": self._map_name,
            "resolution": msg.info.resolution,
            "origin_x": msg.info.origin.position.x,
            "origin_y": msg.info.origin.position.y,
            "origin_theta": msg.info.origin.orientation.z,
            "width": msg.info.width,
            "height": msg.info.height,
        }
        with self._lock:
            self._latest_map_info = info
        logger.info(
            "Received map: %dx%d @ %.3f m/px",
            msg.info.width,
            msg.info.height,
            msg.info.resolution,
        )

    def _get_map_info(self) -> dict | None:
        with self._lock:
            return self._latest_map_info

    @query("destinations")
    async def destinations(self):
        """Return available destination names as string[]."""
        locs = self._get_locations() if self._connected else None
        if locs is None:
            names: list[str] = []
        else:
            names = [loc["name"] for loc in locs]
        return [
            Result(
                name="destinations",
                data_type_ref="string[]",
                value=json.dumps(names, ensure_ascii=False),
            ),
        ]

    @query("destination_positions")
    async def destination_positions(self):
        """Return destination positions with coordinates as JSON string."""
        locs = self._get_locations() if self._connected else None
        if locs is None:
            details: list[dict] = []
        else:
            details = [
                {
                    "id": loc["id"],
                    "name": loc["name"],
                    "x": loc["x"],
                    "y": loc["y"],
                    "theta": loc["theta"],
                }
                for loc in locs
            ]
        return [
            Result(
                name="destination_positions",
                data_type_ref="string",
                value=json.dumps(details, ensure_ascii=False),
            ),
        ]

    @query("component_status")
    async def status(self):
        if not self._connected:
            return results.status("ERROR")
        return results.status("READY")

    @query("map_info")
    async def map_info(self):
        """Return map geometry metadata for rendering.

        Combines the static map_name (from profile config) with the
        live map geometry (resolution, origin, dimensions) from the
        latest OccupancyGrid message. The operator app loads the PNG
        image from StreamingAssets using map_name, then uses the
        geometry for world-to-pixel coordinate conversion.
        """
        info = self._get_map_info() if self._connected else None
        if info is None:
            info = {}
        return [
            Result(
                name="map_info",
                data_type_ref="string",
                value=json.dumps(info, ensure_ascii=False),
            ),
        ]