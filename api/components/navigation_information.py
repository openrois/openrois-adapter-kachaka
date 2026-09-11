"""Navigation Information component for the Kachaka robot (gRPC API).

Non-canonical component (user-defined per RoIS spec section 12) that
provides navigation-related spatial data for the operator app:

- destinations: string[] of destination names (discovery).
- destination_positions: [{id, name, x, y, theta}] (coordinates for rendering).

The component owns its own gRPC client, created in connect() and torn
down in disconnect(). No dependency on the adapter for shared state.
"""

from __future__ import annotations

import json
import logging

from openrois.interfaces.hri import Result
from openrois_components_core import component, query, results

logger = logging.getLogger(__name__)


@component("NavigationInformation")
class NavigationInformation:
    """Spatial data for navigation: available destinations and positions."""

    def __init__(self, config: dict) -> None:
        self._grpc_server = config.get(
            "grpc_server", "192.168.1.100:26400",
        )
        self._map_name = config.get("map_name", "")
        self._client = None
        self._connected = False

    async def connect(self) -> None:
        """Create the gRPC client and connect to the Kachaka robot."""
        from kachaka_api.aio import KachakaApiClient

        self._client = KachakaApiClient(target=self._grpc_server)
        await self._client.update_resolver()
        self._connected = True
        logger.info(
            "NavigationInformation connected to Kachaka at %s",
            self._grpc_server,
        )

    async def disconnect(self) -> None:
        """Tear down the gRPC client."""
        self._connected = False
        self._client = None

    @query("destinations")
    async def destinations(self):
        """Return available destination names as string[]."""
        if not self._connected or self._client is None:
            return [
                Result(
                    name="destinations",
                    data_type_ref="string[]",
                    value=json.dumps([], ensure_ascii=False),
                ),
            ]
        locs = await self._client.get_locations()
        names = [loc.name for loc in locs]
        return [
            Result(
                name="destinations",
                data_type_ref="string[]",
                value=json.dumps(names, ensure_ascii=False),
            ),
        ]

    @query("destination_positions")
    async def destination_positions(self):
        """Return destination positions with coordinates as JSON string[]."""
        if not self._connected or self._client is None:
            return [
                Result(
                    name="destination_positions",
                    data_type_ref="string",
                    value=json.dumps([], ensure_ascii=False),
                ),
            ]
        locs = await self._client.get_locations()
        details = [
            {
                "id": loc.id,
                "name": loc.name,
                "x": loc.pose.x,
                "y": loc.pose.y,
                "theta": loc.pose.theta,
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

    @query("map_info")
    async def map_info(self):
        """Return map geometry metadata for rendering.

        Combines the static map_name (from profile config) with the
        live map geometry (resolution, origin, dimensions) from the
        robot's current map. The operator app loads the PNG image
        from StreamingAssets using map_name, then uses the geometry
        for world-to-pixel coordinate conversion.
        """
        if not self._connected or self._client is None:
            return [
                Result(
                    name="map_info",
                    data_type_ref="string",
                    value=json.dumps({}, ensure_ascii=False),
                ),
            ]
        png_map = await self._client.get_png_map()
        info = {
            "map_name": self._map_name,
            "resolution": png_map.resolution,
            "origin_x": png_map.origin.x,
            "origin_y": png_map.origin.y,
            "origin_theta": png_map.origin.theta,
            "width": png_map.width,
            "height": png_map.height,
        }
        return [
            Result(
                name="map_info",
                data_type_ref="string",
                value=json.dumps(info, ensure_ascii=False),
            ),
        ]

    @query("component_status")
    async def status(self):
        if not self._connected:
            return results.status("ERROR")
        return results.status("READY")