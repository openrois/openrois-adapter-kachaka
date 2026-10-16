"""OpenRoIS components for the Preferred Robotics Kachaka.

Navigation and SystemInformation, each over the Kachaka gRPC API or over ROS 2, and
NavigationInformation, a Coarobo component type for the destinations and the
map. Both
classes of a type declare the same profile constant, so a client sees the same component
whichever backend the adapter imports. Each component takes its section of the adapter's
adapter.yaml as one of the settings models.
"""

from openrois.components.kachaka.navigation_grpc import GrpcNavigation
from openrois.components.kachaka.navigation_information_grpc import GrpcNavigationInformation
from openrois.components.kachaka.navigation_information_ros2 import Ros2NavigationInformation
from openrois.components.kachaka.navigation_ros2 import Ros2Navigation
from openrois.components.kachaka.profiles import NAVIGATION_INFORMATION_PROFILE
from openrois.components.kachaka.settings import (
    GrpcNavigationSettings,
    NavigationInformationSettings,
    Ros2NavigationInformationSettings,
    Ros2NavigationSettings,
    Ros2SystemInformationSettings,
)
from openrois.components.kachaka.system_information_grpc import GrpcSystemInformation
from openrois.components.kachaka.system_information_ros2 import Ros2SystemInformation

__all__ = [
    "NAVIGATION_INFORMATION_PROFILE",
    "GrpcNavigation",
    "GrpcNavigationInformation",
    "GrpcNavigationSettings",
    "GrpcSystemInformation",
    "NavigationInformationSettings",
    "Ros2Navigation",
    "Ros2NavigationInformation",
    "Ros2NavigationInformationSettings",
    "Ros2NavigationSettings",
    "Ros2SystemInformation",
    "Ros2SystemInformationSettings",
]
