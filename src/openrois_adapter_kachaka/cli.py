"""The ``openrois-adapter-kachaka`` command: one Kachaka behind an OpenRoIS gateway.

The adapter builds an ``Engine`` with the components of one robot, shares one
connection to the robot's gRPC API between them, and serves the engine to a gateway with
``WsClient``, reconnecting whenever the connection drops. Two adapters with different
engine ids put two robots behind one gateway::

    # The Kachaka on the network, at its IP address
    openrois-adapter-kachaka --config adapter.yaml --robot-address 192.168.1.100

    # The fake Kachaka on this machine, started with
    # python -m openrois_adapter_kachaka.fake_robot
    openrois-adapter-kachaka --config adapter.fake.yaml

The components are ``<engine id>/system_information`` and ``<engine id>/navigation``.
``<engine id>/move`` moves the robot in straight lines and turns in place.
The adapter reaches the robot at ``robot_address``, ``host`` or ``host:port``, on port
26400 by default. While the robot does not answer, the components report ERROR, and the
adapter drives the robot again once it is back.

The settings come from the engine's adapter loader: the defaults of ``KachakaConfig``,
then ``adapter.yaml`` (named by ``--config`` or ``OPENROIS_ADAPTER_CONFIG``), then the
``OPENROIS_ADAPTER_*`` environment variables, then the flags. The environment and the
flags set only the gateway URL, the engine id, the robot's address and the log level.
Everything else comes from the file.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, Sequence

from openrois.engine import Engine, WsClient
from openrois.engine.config import AdapterConfig, ConfigError, load_adapter_config
from pydantic import BaseModel, ConfigDict, Field

from openrois.components.kachaka import (
    KachakaConnection,
    KachakaMove,
    KachakaNavigation,
    KachakaSystemInformation,
)
from openrois.components.kachaka.connection import parse_address

__all__ = [
    "DESCRIPTION",
    "ConnectionSettings",
    "KachakaConfig",
    "NavigationSettings",
    "build_engine",
    "load_config",
    "main",
]

logger = logging.getLogger("openrois_adapter_kachaka")

DESCRIPTION = "Serve one Preferred Robotics Kachaka to an OpenRoIS gateway."


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectionSettings(_Section):
    """How the adapter reaches the robot's gRPC API."""

    timeout: float = Field(default=5.0, gt=0, description="Seconds to wait for an answer.")
    retry: float = Field(default=3.0, gt=0, description="Seconds between two checks of the robot.")
    lost_after: float = Field(
        default=6.0,
        ge=0,
        description=(
            "Seconds the robot may stop answering before the adapter reports it lost. A "
            "running drive continues through a shorter pause."
        ),
    )
    poll: float = Field(
        default=10.0,
        gt=0,
        description="Seconds one wait for the result of a drive lasts before it is renewed.",
    )


class NavigationSettings(_Section):
    """The Navigation component."""

    auto_homing: bool | None = Field(
        default=None,
        description=(
            "Whether the robot returns to its charger on its own after a while without "
            "commands, set each time the robot comes up. Unset leaves the robot's setting "
            "as it is."
        ),
    )


class KachakaConfig(AdapterConfig):
    """The settings of one Kachaka adapter.

    ``robot_address`` is where the robot's gRPC API is: the robot's IP address or host
    name, with ``:port`` when the port is not 26400. It has no default: set it in the
    file, with ``OPENROIS_ADAPTER_ROBOT_ADDRESS`` or with ``--robot-address``.
    """

    engine_id: str = "kachaka"
    connection: ConnectionSettings = ConnectionSettings()
    navigation: NavigationSettings = NavigationSettings()


def build_engine(config: KachakaConfig) -> Engine:
    """The engine of one robot, with its components on one shared connection.

    Raises:
        ValueError: ``robot_address`` is not set or is malformed.
    """
    if not config.robot_address:
        raise ValueError("robot_address is not set")
    settings = config.connection
    connection = KachakaConnection(
        address=config.robot_address,
        timeout=settings.timeout,
        retry=settings.retry,
        lost_after=settings.lost_after,
        poll=settings.poll,
    )
    engine = Engine(config.engine_id)
    engine.add_component("system_information", KachakaSystemInformation(connection))
    engine.add_component(
        "navigation", KachakaNavigation(connection, auto_homing=config.navigation.auto_homing)
    )
    engine.add_component("move", KachakaMove(connection))
    return engine


def load_config(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> KachakaConfig:
    """The settings, from the defaults, adapter.yaml, the environment and the flags.

    Raises:
        ConfigError: The file cannot be read, a setting is invalid, or ``robot_address``
            is not set or is malformed.
        SystemExit: For an unknown flag or ``--help``.
    """
    config = load_adapter_config(
        KachakaConfig,
        argv,
        os.environ if environ is None else environ,
        description=DESCRIPTION,
    )
    if not config.robot_address or not config.robot_address.strip():
        raise ConfigError(
            "Invalid configuration:\n"
            "  robot_address: set the Kachaka's IP address or host name, for example "
            "192.168.1.100"
        )
    try:
        parse_address(config.robot_address)
    except ValueError as exc:
        raise ConfigError(f"Invalid configuration:\n  robot_address: {exc}") from exc
    return config


def main(argv: Sequence[str] | None = None) -> None:
    """Load the settings and serve the gateway until Ctrl+C."""
    try:
        config = load_config(argv)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(
        level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info(
        "Serving the Kachaka at %s as %s to %s",
        config.robot_address,
        config.engine_id,
        config.gateway_url,
    )
    WsClient(build_engine(config), config.gateway_url).run()
