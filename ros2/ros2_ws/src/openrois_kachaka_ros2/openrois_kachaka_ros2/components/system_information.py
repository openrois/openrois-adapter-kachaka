"""SystemInformation component for the Kachaka robot (ROS 2)."""

from __future__ import annotations

from openrois.interfaces.hri import Result
from openrois.sdk import component, query, results


@component("SystemInformation", bind_required=False)
class SystemInformation:
    """Status and location of the Kachaka robot via ROS 2."""

    @query("robot_position")
    async def robot_position(self):
        pose = self.parent.node.get_pose()
        if pose is None:
            return results.position(x=0.0, y=0.0, theta=0.0)
        return results.position(x=pose[0], y=pose[1], theta=pose[2])

    @query("engine_status")
    async def engine_status(self):
        return [
            Result(
                name="status",
                data_type_ref="string",
                value="READY",
            ),
        ]