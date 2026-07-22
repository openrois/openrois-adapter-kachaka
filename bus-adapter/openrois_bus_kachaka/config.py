"""Configuration for the Kachaka BusAdapter.

Defines :class:`KachakaAdapterConfig`, the dataclass that parameterises
:class:`openrois_bus_kachaka.adapter.KachakaAdapter`.

Attributes are intentionally simple primitives so the config can be loaded
from environment variables, JSON, or constructed inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class KachakaAdapterConfig:
    """Configuration for :class:`KachakaAdapter`.

    Attributes:
        target: gRPC target string for the Kachaka robot
            (``host:port``, e.g. ``"192.168.1.100:26400"``).
        poll_interval_sec: Interval in seconds for polling
            ``get_command_state()`` in the event poller (afternoon task).
        component_refs: Static list of component references advertised by
            ``discover()``. Defaults to the Navigation and SystemInformation
            components.
        auto_homing: Whether to enable Kachaka auto-homing on connect.
    """

    target: str = "192.168.100.203:26400"
    poll_interval_sec: float = 0.5
    component_refs: list[str] = field(
        default_factory=lambda: [
            "kachaka/Navigation",
            "kachaka/SystemInformation",
        ]
    )
    auto_homing: bool = False