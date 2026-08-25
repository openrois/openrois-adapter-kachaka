"""OpenRoIS adapter for the Kachaka robot using the gRPC API.

Translates RoIS JSON-RPC to Kachaka gRPC calls. Component handlers
are in the components/ package, one file per component.

Usage:
    python kachaka_api_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import logging

from openrois.sdk import AdapterFramework, RobotAdapter, load_config

from components import MapObserver, Navigation, SystemInformation

logger = logging.getLogger(__name__)


class KachakaApiAdapter(RobotAdapter):
    """Adapter for the Kachaka robot via the kachaka-api Python library."""

    SystemInformation = SystemInformation
    Navigation = Navigation
    MapObserver = MapObserver

    def __init__(self, config: dict) -> None:
        env = config.get("environment", {})
        self._grpc_server = env.get("grpc_server", "192.168.1.100:26400")
        self._auto_homing = env.get("auto_homing", False)
        self._poll_interval = env.get("poll_interval_sec", 0.5)

        map_config = config.get("components", {}).get("MapObserver", {})
        self._map_name = map_config.get("map_name", "")
        self._map_resolution = float(map_config.get("map_resolution", 0.0))
        self._map_origin_x = float(map_config.get("map_origin_x", 0.0))
        self._map_origin_y = float(map_config.get("map_origin_y", 0.0))
        self._map_width = int(map_config.get("map_width", 0))
        self._map_height = int(map_config.get("map_height", 0))

        self._client = None
        self._pb2 = None
        self._connected = False
        self._nav_busy = False
        self._nav_target = ""
        self._locations_cache: list | None = None

        super().__init__(config)

    async def connect(self) -> None:
        """Connect to Kachaka and initialize the resolver."""
        from kachaka_api.aio import KachakaApiClient
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        self._client = KachakaApiClient(target=self._grpc_server)
        self._pb2 = pb2
        await self._client.update_resolver()
        await self._client.set_auto_homing_enabled(self._auto_homing)
        self._connected = True
        logger.info("Connected to Kachaka at %s", self._grpc_server)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Kachaka OpenRoIS adapter")
    parser.add_argument(
        "--config",
        default="openrois-profile.yaml",
        help="Path to the profile YAML file",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = KachakaApiAdapter(config)
    framework = AdapterFramework(adapter, config)
    framework.run()


if __name__ == "__main__":
    main()
