"""OpenRoIS BusAdapter implementation for the Kachaka robot (gRPC).

This package bridges the OMG RoIS 2.0 ``BusAdapter`` contract to the Kachaka
gRPC API. It exposes Navigation and SystemInformation components.

Public API::

    from openrois_bus_kachaka import KachakaAdapter, KachakaAdapterConfig

    config = KachakaAdapterConfig(target="192.168.1.100:26400")
    adapter = KachakaAdapter(config)
    await adapter.discover(request)
    await adapter.query(request)
    await adapter.invoke(request)
"""

from __future__ import annotations

from openrois_bus_kachaka.adapter import KachakaAdapter
from openrois_bus_kachaka.config import KachakaAdapterConfig

__all__ = [
    "KachakaAdapter",
    "KachakaAdapterConfig",
]

__version__ = "0.1.0a1"