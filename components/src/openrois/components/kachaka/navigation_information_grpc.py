"""NavigationInformation for the Kachaka, over its gRPC API.

The destinations are the Kachaka's locations, read on each query, and ``map_info``
combines the configured map name with the geometry of the Kachaka's current map. The
component owns its gRPC client, created in ``connect()``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openrois.components.core import Component, component, query

from openrois.components.kachaka.profiles import NAVIGATION_INFORMATION_PROFILE
from openrois.components.kachaka.settings import NavigationInformationSettings

logger = logging.getLogger(__name__)


@component(NAVIGATION_INFORMATION_PROFILE)
class GrpcNavigationInformation(Component):
    """NavigationInformation backed by the Kachaka gRPC API.

    Args:
        grpc_server: The address of the Kachaka's gRPC API, for example
            ``192.168.1.100:26400``.
        settings: The component's section of adapter.yaml.
    """

    def __init__(
        self, grpc_server: str, settings: NavigationInformationSettings | None = None
    ) -> None:
        self._grpc_server = grpc_server
        self._map_name = (settings or NavigationInformationSettings()).map_name
        self._client: Any = None

    async def connect(self) -> None:
        from kachaka_api.aio import KachakaApiClient

        self._client = KachakaApiClient(target=self._grpc_server)
        await self._client.update_resolver()
        logger.info("NavigationInformation connected to the Kachaka at %s", self._grpc_server)

    async def disconnect(self) -> None:
        self._client = None

    @query("destinations")
    async def destinations(self) -> dict[str, object]:
        return {"destinations": [location.name for location in await self._client.get_locations()]}

    @query("destination_positions")
    async def destination_positions(self) -> dict[str, object]:
        positions = [
            {
                "id": location.id,
                "name": location.name,
                "x": location.pose.x,
                "y": location.pose.y,
                "theta": location.pose.theta,
            }
            for location in await self._client.get_locations()
        ]
        return {"destination_positions": json.dumps(positions, ensure_ascii=False)}

    @query("map_info")
    async def map_info(self) -> dict[str, object]:
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
        return {"map_info": json.dumps(info, ensure_ascii=False)}
