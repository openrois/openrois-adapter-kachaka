"""NavigationInformation for the Kachaka, over ROS 2.

The destinations come from the latched location list topic and ``map_info`` from the
latched map topic, both kept from their latest message. The component owns its rclpy
node, created in ``connect()`` and handed to the adapter's RclpyExecutor, which spins it.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from openrois.components.core import Component, component, query
from openrois.components.core.ros2 import RclpyExecutor

from openrois.components.kachaka.profiles import NAVIGATION_INFORMATION_PROFILE
from openrois.components.kachaka.settings import Ros2NavigationInformationSettings

logger = logging.getLogger(__name__)


@component(NAVIGATION_INFORMATION_PROFILE)
class Ros2NavigationInformation(Component):
    """NavigationInformation backed by the Kachaka ROS 2 interfaces.

    Args:
        ros: The adapter's RclpyExecutor, shared with its other ROS 2 components.
        settings: The component's section of adapter.yaml.
    """

    def __init__(
        self, ros: RclpyExecutor, settings: Ros2NavigationInformationSettings | None = None
    ) -> None:
        settings = settings or Ros2NavigationInformationSettings()
        self._ros = self.uses(ros)
        self._node_name = settings.node_name
        self._locations_topic = settings.locations_topic
        self._map_topic = settings.map_topic
        self._map_name = settings.map_name
        self._node: Any = None
        self._lock = threading.Lock()
        self._locations: list[dict[str, object]] = []
        self._map_info: dict[str, object] = {}

    async def connect(self) -> None:
        from kachaka_interfaces.msg import LocationList
        from nav_msgs.msg import OccupancyGrid
        from rclpy.callback_groups import ReentrantCallbackGroup
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        node = Node(f"{self._node_name}_navinfo")
        try:
            callbacks = ReentrantCallbackGroup()
            latched = QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                depth=1,
            )
            node.create_subscription(
                LocationList, self._locations_topic, self._on_locations, latched,
                callback_group=callbacks,
            )
            node.create_subscription(
                OccupancyGrid, self._map_topic, self._on_map, latched, callback_group=callbacks
            )
        except Exception:
            # A node that is half set up is not spun.
            node.destroy_node()
            raise
        self._ros.add_node(node)
        self._node = node
        logger.info(
            "NavigationInformation listens on %s and %s", self._locations_topic, self._map_topic
        )

    async def disconnect(self) -> None:
        if self._node is None:
            return
        # The node leaves the executor before it is destroyed.
        self._ros.remove_node(self._node)
        self._node.destroy_node()
        self._node = None

    @query("destinations")
    async def destinations(self) -> dict[str, object]:
        with self._lock:
            return {"destinations": [location["name"] for location in self._locations]}

    @query("destination_positions")
    async def destination_positions(self) -> dict[str, object]:
        with self._lock:
            positions = list(self._locations)
        return {"destination_positions": json.dumps(positions, ensure_ascii=False)}

    @query("map_info")
    async def map_info(self) -> dict[str, object]:
        with self._lock:
            info = dict(self._map_info)
        return {"map_info": json.dumps(info, ensure_ascii=False)}

    def _on_locations(self, message: Any) -> None:
        """Keep the latest locations. Runs on the thread that spins the node."""
        locations: list[dict[str, object]] = [
            {
                "id": location.id,
                "name": location.name,
                "x": location.pose.x,
                "y": location.pose.y,
                "theta": location.pose.theta,
            }
            for location in message.locations
        ]
        with self._lock:
            self._locations = locations
        logger.info("Received %d locations", len(locations))

    def _on_map(self, message: Any) -> None:
        """Keep the geometry of the latest map. Runs on the thread that spins the node."""
        info = message.info
        geometry: dict[str, object] = {
            "map_name": self._map_name,
            "resolution": info.resolution,
            "origin_x": info.origin.position.x,
            "origin_y": info.origin.position.y,
            "origin_theta": info.origin.orientation.z,
            "width": info.width,
            "height": info.height,
        }
        with self._lock:
            self._map_info = geometry
        logger.info("Received a %dx%d map", info.width, info.height)
