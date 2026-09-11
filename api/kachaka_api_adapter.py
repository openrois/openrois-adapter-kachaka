"""OpenRoIS adapter for the Kachaka robot using the gRPC API.

Translates RoIS JSON-RPC to Kachaka gRPC calls. The Navigation and
SystemInformation components are imported from openrois_components.kachaka.
NavigationInformation is a local non-canonical component. Each component
owns its own gRPC client, created in connect() and torn down in
disconnect(). The adapter registers components with the Engine and
connects to the gateway via WsClient.

Usage:
    python kachaka_api_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from openrois_core import Engine, WsClient, component_config, read_profile
from openrois_components_core import meta_from_decorators

from openrois_components.kachaka import GrpcNavigation, GrpcSystemInformation
from components import NavigationInformation

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Kachaka OpenRoIS adapter")
    parser.add_argument(
        "--config",
        default="openrois-profile.yaml",
        help="Path to the profile YAML file",
    )
    args = parser.parse_args()

    profile = read_profile(args.config)

    engine = Engine(
        engine_id=profile["engine"]["id"],
        platform=profile["engine"].get("platform", ""),
    )
    engine.register_component(
        "Navigation",
        GrpcNavigation(component_config(profile, "Navigation")),
        meta_from_decorators(GrpcNavigation),
    )
    engine.register_component(
        "SystemInformation",
        GrpcSystemInformation(component_config(profile, "SystemInformation")),
        meta_from_decorators(GrpcSystemInformation),
    )
    engine.register_component(
        "NavigationInformation",
        NavigationInformation(component_config(profile, "NavigationInformation")),
        meta_from_decorators(NavigationInformation),
    )

    ws_client = WsClient(engine, profile["engine"]["gateway_url"])
    ws_client.run()


if __name__ == "__main__":
    main()
