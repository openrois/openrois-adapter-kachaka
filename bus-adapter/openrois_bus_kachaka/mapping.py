"""Conversion functions between Kachaka protobuf types and RoIS models.

This module is the single place that knows how to translate Kachaka gRPC
protobuf responses into OpenRoIS Pydantic result models. It imports **only**
from ``openrois.interfaces`` and ``kachaka_api.generated`` — never from the
adapter or gateway, keeping the separation of concerns clean.

Three public functions are defined:

* :func:`pose_to_robot_position_result` — ``pb2.Pose`` →
  :class:`SystemInformationRobotPositionResult`
* :func:`locations_to_waypoints_result` — ``list[pb2.Location]`` →
  :class:`ResultList` (custom waypoints query)
* :func:`command_state_to_component_status` — ``pb2.CommandState`` →
  :class:`ComponentStatus`
"""

from __future__ import annotations

from datetime import datetime, timezone

from kachaka_api.generated import kachaka_api_pb2 as pb2
from openrois.interfaces.common import ComponentStatus
from openrois.interfaces.components.system_information import (
    SystemInformationRobotPositionResult,
)
from openrois.interfaces.hri import Result, ResultList


def pose_to_robot_position_result(
    pose: pb2.Pose,
    *,
    robot_ref: str = "kachaka",
) -> SystemInformationRobotPositionResult:
    """Convert a Kachaka ``Pose`` to a RoIS ``SystemInformationRobotPositionResult``.

    The Kachaka ``Pose`` has ``x``, ``y``, ``theta`` (all ``double``).
    The RoIS model expects ``timestamp`` (ISO 8601), ``robot_ref`` (list of IDs),
    and ``position_data`` (list of strings).

    Args:
        pose: Kachaka protobuf ``Pose`` message.
        robot_ref: Robot identifier to include in the result.

    Returns:
        A :class:`SystemInformationRobotPositionResult` with ``position_data``
        set to ``[str(x), str(y), str(theta)]``.
    """
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    return SystemInformationRobotPositionResult(
        timestamp=timestamp,
        robot_ref=[robot_ref],
        position_data=[str(pose.x), str(pose.y), str(pose.theta)],
    )


def locations_to_waypoints_result(
    locations: list[pb2.Location] | pb2.Location,
) -> ResultList:
    """Convert Kachaka locations to a RoIS ``ResultList`` for the waypoints query.

    This is a demo extension (not in the OMG spec). Each location is serialised
    into a :class:`Result` whose ``name`` is the location name, ``data_type_ref``
    is ``"waypoint"``, and ``value`` is a pipe-delimited string:

    ``"id|name|x|y|theta"``

    This keeps the generic ``Result(value=str)`` contract while carrying the
    full waypoint information needed by the map UI.

    Args:
        locations: A list of Kachaka ``Location`` messages, or the
            ``RepeatedCompositeContainer`` returned by ``get_locations()``.

    Returns:
        A :class:`ResultList` with one :class:`Result` per location.
    """
    results: ResultList = []
    for loc in locations:
        value = "|".join(
            [
                loc.id,
                loc.name,
                str(loc.pose.x),
                str(loc.pose.y),
                str(loc.pose.theta),
            ]
        )
        results.append(
            Result(
                name=loc.name,
                data_type_ref="waypoint",
                value=value,
            )
        )
    return results


def command_state_to_component_status(
    state: pb2.CommandState,
) -> ComponentStatus:
    """Map a Kachaka ``CommandState`` to a RoIS ``ComponentStatus``.

    Kachaka ``CommandState`` has three values:

    =============  =================  ====================
    Kachaka state  Int value         RoIS status
    =============  =================  ====================
    UNSPECIFIED    0                 READY
    PENDING        1                 READY
    RUNNING        2                 BUSY
    =============  =================  ====================

    Command success/failure is determined separately via
    ``get_last_command_result()`` (used by the poller in the afternoon task).
    For the synchronous ``query("component_status")`` we only report whether
    the robot is currently executing a command (BUSY) or idle (READY).

    Args:
        state: Kachaka ``CommandState`` protobuf enum value.

    Returns:
        :class:`ComponentStatus.BUSY` if the robot is running a command,
        :class:`ComponentStatus.READY` otherwise.
    """
    if state == pb2.CommandState.COMMAND_STATE_RUNNING:
        return ComponentStatus.BUSY
    # PENDING (idle, waiting) and UNSPECIFIED both map to READY.
    return ComponentStatus.READY