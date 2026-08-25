"""SystemInformation component for the Kachaka robot (gRPC API)."""

from __future__ import annotations

from openrois.interfaces.hri import Result
from openrois.sdk import component, query, results


@component("SystemInformation", bind_required=False)
class SystemInformation:
    """Status and location of the Kachaka robot via gRPC API."""

    @query("robot_position")
    async def robot_position(self):
        pose = await self.parent._client.get_robot_pose()
        return results.position(x=pose.x, y=pose.y, theta=pose.theta)

    @query("engine_status")
    async def engine_status(self):
        return [
            Result(
                name="status",
                data_type_ref="string",
                value="READY",
            ),
        ]