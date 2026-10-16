"""OpenRoIS adapter for the Kachaka robot over its gRPC API.

A thin process: it hosts the Kachaka components of openrois-components-kachaka in an
engine, Navigation, SystemInformation and NavigationInformation, and connects the engine
to the gateway on ``/adapter``. Each component keeps its own client of the gRPC API.

Usage:
    python kachaka_api_adapter.py [--config FILE] [--robot-address HOST:PORT]
                                  [--gateway-url URL] [--engine-id ID] [--log-level LEVEL]

Each setting comes from the defaults of KachakaConfig, then ``adapter.yaml`` (named by
``--config`` or ``OPENROIS_ADAPTER_CONFIG``), then the ``OPENROIS_ADAPTER_*`` environment
variables, then the flags. ``robot_address`` is the Kachaka's gRPC API, for example
``192.168.1.100:26400``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, Sequence

from openrois.components.kachaka import (
    GrpcNavigation,
    GrpcNavigationSettings,
    GrpcNavigationInformation,
    GrpcSystemInformation,
    NavigationInformationSettings,
)
from openrois.engine import Engine, WsClient
from openrois.engine.config import AdapterConfig, ConfigError, load_adapter_config

DESCRIPTION = "OpenRoIS adapter for the Kachaka robot, over its gRPC API."


class KachakaConfig(AdapterConfig):
    """The four common settings, and one section per component that has settings."""

    engine_id: str = "kachaka_01"
    navigation: GrpcNavigationSettings = GrpcNavigationSettings()
    navigation_information: NavigationInformationSettings = NavigationInformationSettings()


def load(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> KachakaConfig:
    """Read the settings from the file, the environment and the flags."""
    return load_adapter_config(KachakaConfig, argv, environ, description=DESCRIPTION)


def build_engine(config: KachakaConfig) -> Engine:
    """An engine that hosts the three components, under the names clients know."""
    if config.robot_address is None:
        raise ConfigError("robot_address is not set: the Kachaka's gRPC API, host:port.")
    kachaka = config.robot_address
    engine = Engine(config.engine_id)
    engine.add_component("Navigation", GrpcNavigation(kachaka, config.navigation))
    engine.add_component("SystemInformation", GrpcSystemInformation(kachaka))
    engine.add_component(
        "NavigationInformation",
        GrpcNavigationInformation(kachaka, config.navigation_information),
    )
    return engine


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> None:
    """Serve the Kachaka to the gateway until Ctrl+C."""
    try:
        config = load(argv, environ)
        engine = build_engine(config)
    except ConfigError as exc:
        print(f"kachaka_api_adapter: {exc}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=config.log_level.upper())
    WsClient(engine, config.gateway_url).run()


if __name__ == "__main__":
    main()
