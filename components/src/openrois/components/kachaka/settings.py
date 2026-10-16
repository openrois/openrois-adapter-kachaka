"""The settings of the Kachaka components: one section each of an adapter's adapter.yaml.

An adapter declares them as sections of its AdapterConfig, and hands each component its
section::

    class KachakaConfig(AdapterConfig):
        engine_id: str = "kachaka"
        navigation: Ros2NavigationSettings = Ros2NavigationSettings()
        system_information: Ros2SystemInformationSettings = Ros2SystemInformationSettings()

The defaults match the topics and the action of the Kachaka ROS 2 bridge in its
``kachaka`` namespace. The address of the gRPC API is the adapter's ``robot_address``, not
a setting of a section.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GrpcNavigationSettings(BaseModel):
    """The section of GrpcNavigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    auto_homing: bool = Field(
        default=False, description="Whether the Kachaka returns to its charger when idle."
    )


class Ros2NavigationSettings(BaseModel):
    """The section of Ros2Navigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_name: str = Field(
        default="openrois_kachaka",
        min_length=1,
        description="Name of the rclpy node, which gets a _nav suffix.",
    )
    locations_topic: str = Field(
        default="/kachaka/layout/locations/list",
        min_length=1,
        description="The latched topic that lists the locations.",
    )
    command_action: str = Field(
        default="/kachaka/kachaka_command/execute",
        min_length=1,
        description="The action that runs Kachaka commands.",
    )
    goal_timeout: float = Field(
        default=10.0,
        gt=0,
        description="Seconds to wait for the Kachaka to accept or refuse a goal.",
    )


class Ros2SystemInformationSettings(BaseModel):
    """The section of Ros2SystemInformation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_name: str = Field(
        default="openrois_kachaka",
        min_length=1,
        description="Name of the rclpy node, which gets a _sysinfo suffix.",
    )
    pose_topic: str = Field(
        default="/kachaka/odometry/odometry",
        min_length=1,
        description="The odometry topic of the Kachaka.",
    )


class NavigationInformationSettings(BaseModel):
    """The section of GrpcNavigationInformation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    map_name: str = Field(
        default="", description="The name map_info gives the map, for the client's map image."
    )


class Ros2NavigationInformationSettings(BaseModel):
    """The section of Ros2NavigationInformation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_name: str = Field(
        default="openrois_kachaka",
        min_length=1,
        description="Name of the rclpy node, which gets a _navinfo suffix.",
    )
    locations_topic: str = Field(
        default="/kachaka/layout/locations/list",
        min_length=1,
        description="The latched topic that lists the locations.",
    )
    map_topic: str = Field(
        default="/kachaka/mapping/map", min_length=1, description="The latched map topic."
    )
    map_name: str = Field(
        default="", description="The name map_info gives the map, for the client's map image."
    )
