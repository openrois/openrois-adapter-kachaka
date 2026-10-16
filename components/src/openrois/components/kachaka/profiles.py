"""The profile of NavigationInformation, a component type Coarobo publishes.

No basic RoIS component type gives a client the places a robot can go to and the map it
moves on, so the Kachaka components declare this profile. It includes RoIS_Common for
component_status, which the engine answers.
"""

from __future__ import annotations

from openrois.interfaces.components import ROIS_COMMON_PROFILE, ROIS_COMMON_URN
from openrois.interfaces.profiles import (
    HRIComponentProfile,
    ParameterProfile,
    QueryMessageProfile,
    RoISIdentifierType,
)


def _result(name: str, code: str, description: str) -> ParameterProfile:
    return ParameterProfile(
        name=name, data_type_ref=RoISIdentifierType(code=code), description=description
    )


#: Where the robot can go, and the map it moves on.
NAVIGATION_INFORMATION_PROFILE = HRIComponentProfile(
    identifier=RoISIdentifierType(authority="Coarobo", code="NavigationInformation"),
    name="navigation_information",
    sub_component_profiles=[ROIS_COMMON_URN],
    query_profiles=[
        *ROIS_COMMON_PROFILE.query_profiles,
        QueryMessageProfile(
            name="destinations",
            results=[_result("destinations", "string[]", "the names of the destinations")],
        ),
        QueryMessageProfile(
            name="destination_positions",
            results=[
                _result(
                    "destination_positions",
                    "string",
                    "a JSON array of {id, name, x, y, theta}, in meters and radians on the map",
                )
            ],
        ),
        QueryMessageProfile(
            name="map_info",
            results=[
                _result(
                    "map_info",
                    "string",
                    "a JSON object {map_name, resolution, origin_x, origin_y, origin_theta, "
                    "width, height}, or {} before the map is known",
                )
            ],
        ),
    ],
)
