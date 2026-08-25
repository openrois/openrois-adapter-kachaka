"""Navigation component for the Kachaka robot (ROS 2).

Translates RoIS Navigation commands to Kachaka ROS 2 action calls:
- execute: send_move_to_location() with the target location ID.
- stop: cancel_command().
- component_status: check is_command_running().
- reached_target event: result callback on the action client.
"""

from __future__ import annotations

import json
import logging

from openrois.interfaces.bus import InvokeResponse
from openrois.interfaces.hri import ReturnCode, Result
from openrois.sdk import component, invoke, query, results, subscribe

logger = logging.getLogger(__name__)


@component("Navigation", bind_required=True)
class Navigation:
    """Canonical RoIS Navigation component backed by Kachaka ROS 2 actions."""

    @query("get_parameter")
    async def get_parameter(self):
        """Return target_positions (location names), time_limit, routing_policy."""
        locations = self.parent.node.get_locations()
        if locations is None:
            target_positions: list[str] = []
        else:
            target_positions = [loc["name"] for loc in locations]
        return [
            Result(
                name="target_positions",
                data_type_ref="string[]",
                value=json.dumps(target_positions),
            ),
            Result(
                name="time_limit",
                data_type_ref="int",
                value="0",
            ),
            Result(
                name="routing_policy",
                data_type_ref="string",
                value="time",
            ),
        ]

    @query("component_status")
    async def status(self):
        if self.parent.node.is_command_running():
            return results.status("BUSY")
        return results.status("READY")

    @invoke("execute")
    async def navigate(self, parameters):
        if self.parent.node.is_command_running():
            return InvokeResponse(
                return_code=ReturnCode.ERROR, command_id="",
            )
        target = parameters[0].value if parameters else ""
        locations = self.parent.node.get_locations()
        if locations is None:
            return InvokeResponse(
                return_code=ReturnCode.BAD_PARAMETER, command_id="",
            )
        loc = next(
            (l for l in locations if l["name"] == target),
            None,
        )
        if loc is None:
            return InvokeResponse(
                return_code=ReturnCode.BAD_PARAMETER, command_id="",
            )
        accepted = self.parent.node.send_move_to_location(loc["id"])
        if not accepted:
            return InvokeResponse(
                return_code=ReturnCode.ERROR, command_id="",
            )
        self.parent._nav_busy = True
        self.parent._nav_target = target
        return InvokeResponse(
            return_code=ReturnCode.OK, command_id="cmd-nav",
        )

    @invoke("stop")
    async def stop(self, parameters):
        self.parent.node.cancel_command()
        self.parent._nav_busy = False
        return InvokeResponse(return_code=ReturnCode.OK, command_id="")

    @subscribe("reached_target")
    async def on_reached(self):
        """Register a result callback on the node."""
        def on_action_result(success: bool) -> None:
            self.parent._nav_busy = False
            target_name = getattr(self.parent, "_nav_target", "")
            self.parent.emit(
                "Navigation",
                "reached_target",
                results.reached_target(
                    target=target_name, is_final_target=True,
                ),
            )
            logger.info(
                "Fired reached_target event (success=%s)", success,
            )

        self.parent.node.set_result_callback(on_action_result)