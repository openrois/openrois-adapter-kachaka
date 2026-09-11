"""OpenRoIS adapter for the Kachaka robot using ROS 2.

The Navigation and SystemInformation components are imported from
openrois_components.kachaka. NavigationInformation is a local
non-canonical component. Each component owns its own rclpy Node,
created in connect() and torn down in disconnect(). The adapter
registers components with the Engine and connects to the gateway
via WsClient. The WsClient spins all component nodes in a
background thread.

Usage:
    python kachaka_ros2_adapter.py --config openrois-profile.yaml
"""

from __future__ import annotations

import argparse
import logging

from openrois_core import Engine, WsClient, component_config, read_profile
from openrois_components_core import meta_from_decorators

from openrois_components.kachaka import Ros2Navigation, Ros2SystemInformation
from .components import NavigationInformation

logger = logging.getLogger(__name__)


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
    # Use parse_known_args so --ros-args (consumed by rclpy.init)
    # does not cause argparse to error when launched via ros2 run.
    args, _ = parser.parse_known_args()

    profile = read_profile(args.config)

    engine = Engine(
        engine_id=profile["engine"]["id"],
        platform=profile["engine"].get("platform", ""),
    )
    engine.register_component(
        "Navigation",
        Ros2Navigation(component_config(profile, "Navigation")),
        meta_from_decorators(Ros2Navigation),
    )
    engine.register_component(
        "SystemInformation",
        Ros2SystemInformation(component_config(profile, "SystemInformation")),
        meta_from_decorators(Ros2SystemInformation),
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
