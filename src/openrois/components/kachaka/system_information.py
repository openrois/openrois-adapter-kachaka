"""SystemInformation for the Kachaka.

``robot_position`` reads the robot's pose on its map from the robot on each query: x and
y in meters, theta in radians. ``engine_status`` reports the status of the engine that
hosts the component. While the robot does not answer, the connection raises ERROR on this
component as on the others, and the engine refuses its queries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openrois.components.core import Component, component, query
from openrois.interfaces.components import SYSTEM_INFORMATION_PROFILE

from openrois.components.kachaka.connection import KachakaConnection

if TYPE_CHECKING:
    from kachaka_api.generated import kachaka_api_pb2 as pb2

__all__ = ["KachakaSystemInformation"]


@component(SYSTEM_INFORMATION_PROFILE)
class KachakaSystemInformation(Component):
    """SystemInformation of one Kachaka.

    Args:
        connection: The connection to the robot, shared with its other components.
    """

    def __init__(self, connection: KachakaConnection) -> None:
        self._connection = self.uses(connection)
        self._started = datetime.now(UTC)

    async def connect(self) -> None:
        self._started = datetime.now(UTC)

    @query("robot_position")
    async def robot_position(self) -> dict[str, object]:
        """``x,y,theta`` of the robot on its map, in meters and radians.

        Raises:
            ConnectionError: The robot did not answer in time.
        """
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        response: pb2.GetRobotPoseResponse = await self._connection.call(
            "GetRobotPose", pb2.GetRequest()
        )
        pose = response.pose
        return {
            "position_data": [f"{pose.x:.4f},{pose.y:.4f},{pose.theta:.4f}"],
            "robot_ref": [self.ref.split("/", 1)[0]],
            "timestamp": datetime.now(UTC),
        }

    @query("engine_status")
    async def engine_status(self) -> dict[str, object]:
        """The status of the engine, and when the component connected."""
        return {"operable_time": self._started, "status": self.engine_state()}
