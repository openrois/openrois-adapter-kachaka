"""MapObserver component for the Kachaka robot (ROS 2).

Custom component providing spatial data for visualization. Returns
waypoints from the ROS 2 locations topic and map metadata from config.
"""

from __future__ import annotations

from openrois.interfaces.hri import Result
from openrois.sdk import component, query, results


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