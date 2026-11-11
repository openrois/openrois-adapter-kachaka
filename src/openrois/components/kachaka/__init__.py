"""OpenRoIS components for the Preferred Robotics Kachaka.

Canonical RoIS components that drive a Kachaka through the Kachaka gRPC API. Each one is a
``Component`` of ``openrois-components-core`` that declares the profile of its type, so an
``Engine`` serves it as that type. The components of one robot share one
``KachakaConnection``.

The components import ``grpc`` and the Kachaka API stubs only when they connect, so this
package imports without the ``robot`` extra.
"""

from openrois.components.kachaka.connection import CommandPreempted, KachakaConnection
from openrois.components.kachaka.move import KachakaMove
from openrois.components.kachaka.navigation import KachakaNavigation
from openrois.components.kachaka.system_information import KachakaSystemInformation

__all__ = [
    "CommandPreempted",
    "KachakaConnection",
    "KachakaMove",
    "KachakaNavigation",
    "KachakaSystemInformation",
]
