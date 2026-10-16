"""OpenRoIS adapter for the Kachaka robot over ROS 2.

A thin process: it hosts the Kachaka ROS 2 components of openrois-components-kachaka in
an engine, Navigation, SystemInformation and NavigationInformation, and connects the
engine to the gateway on ``/adapter``. The components share one RclpyExecutor, which
initializes rclpy and spins their nodes in a background thread. They talk to the topics
and the action of the kachaka_grpc_ros2_bridge.

Usage:
    ros2 run openrois_kachaka_ros2 kachaka_adapter [--config FILE] [--gateway-url URL]
                                                   [--engine-id ID] [--log-level LEVEL]

Each setting comes from the defaults of KachakaConfig, then ``adapter.yaml`` (named by
``--config`` or ``OPENROIS_ADAPTER_CONFIG``), then the ``OPENROIS_ADAPTER_*`` environment
variables, then the flags. The arguments after ``--ros-args`` are left to ROS 2.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, Sequence

from openrois.components.core.ros2 import RclpyExecutor
from openrois.components.kachaka import (
    Ros2Navigation,
    Ros2NavigationInformation,
    Ros2NavigationInformationSettings,
    Ros2NavigationSettings,
    Ros2SystemInformation,
    Ros2SystemInformationSettings,
)
from openrois.engine import Engine, WsClient
from openrois.engine.config import AdapterConfig, ConfigError, load_adapter_config

DESCRIPTION = "OpenRoIS adapter for the Kachaka robot, over ROS 2."


class KachakaConfig(AdapterConfig):
    """The four common settings, and one section per component."""

    engine_id: str = "kachaka"
    navigation: Ros2NavigationSettings = Ros2NavigationSettings()
    system_information: Ros2SystemInformationSettings = Ros2SystemInformationSettings()
    navigation_information: Ros2NavigationInformationSettings = (
        Ros2NavigationInformationSettings()
    )


def without_ros_args(argv: Sequence[str]) -> list[str]:
    """The arguments that are the adapter's: those outside ``--ros-args ... --``."""
    kept: list[str] = []
    in_ros_args = False
    for argument in argv:
        if argument == "--ros-args":
            in_ros_args = True
        elif in_ros_args and argument == "--":
            in_ros_args = False
        elif not in_ros_args:
            kept.append(argument)
    return kept


def load(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> KachakaConfig:
    """Read the settings from the file, the environment and the flags."""
    flags = without_ros_args(sys.argv[1:] if argv is None else argv)
    return load_adapter_config(KachakaConfig, flags, environ, description=DESCRIPTION)


def build_engine(config: KachakaConfig) -> Engine:
    """An engine that hosts the three components, under the names clients know."""
    ros = RclpyExecutor()
    engine = Engine(config.engine_id)
    engine.add_component("Navigation", Ros2Navigation(ros, config.navigation))
    engine.add_component(
        "SystemInformation", Ros2SystemInformation(ros, config.system_information)
    )
    engine.add_component(
        "NavigationInformation", Ros2NavigationInformation(ros, config.navigation_information)
    )
    return engine


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> None:
    """Serve the Kachaka to the gateway until Ctrl+C."""
    try:
        config = load(argv, environ)
    except ConfigError as exc:
        print(f"kachaka_adapter: {exc}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=config.log_level.upper())
    WsClient(build_engine(config), config.gateway_url).run()


if __name__ == "__main__":
    main()
