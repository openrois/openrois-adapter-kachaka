"""Mock BusAdapter for gateway testing without a real robot.

Implements the BusAdapter protocol with hardcoded responses. Used with
`python main.py --mock` to test the full JSON-RPC dispatch on day 1 before
the intern's KachakaAdapter exists or a real Kachaka robot is available.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timezone

from openrois.interfaces.bus import (
    CommandRequest,
    DiscoverRequest,
    DiscoverResponse,
    EventEnvelope,
    EventSink,
    InvokeResponse,
    QueryRequest,
    QueryResponse,
    SubscribeRequest,
    SubscribeResponse,
)
from openrois.interfaces.common import ComponentStatus
from openrois.interfaces.hri import (
    CommandId,
    CommandType,
    Parameter,
    Result,
    ReturnCode,
    SubscribeId,
)
from openrois.interfaces.service import CompletedStatus

_COMPONENT_REFS = ["kachaka/Navigation", "kachaka/SystemInformation"]

# Simulated locations (waypoints) from the Kachaka unicorn map.
_LOCATIONS = [
    {"id": "S01_home", "name": "シェルフのホーム", "pose": {"x": 3.773288, "y": -1.30068, "theta": 1.785376}},
    {"id": "home", "name": "充電ドック", "pose": {"x": 0.033114, "y": -0.062141, "theta": -0.020478307179586677}},
]

# Build a lookup from name or id to the location dict.
_LOCATION_BY_KEY: dict[str, dict] = {}
for loc in _LOCATIONS:
    _LOCATION_BY_KEY[loc["name"]] = loc
    _LOCATION_BY_KEY[loc["id"]] = loc

# Movement simulation: the robot moves at a fixed speed (meters per second).
# The poller updates the position at a fixed interval.
_MOVE_SPEED_MPS = 0.5
_MOVE_POLL_SEC = 0.1


class MockAdapter:
    """Minimal BusAdapter implementation with hardcoded responses."""

    def __init__(self) -> None:
        # Robot state: current position (x, y, theta) in map frame.
        self._robot_x = 0.0
        self._robot_y = 0.0
        self._robot_theta = 0.0

        # Command state.
        self._command_running = False
        self._current_command_id: CommandId = ""
        self._current_target: str = ""
        self._target_x = 0.0
        self._target_y = 0.0
        self._target_theta = 0.0
        self._move_task: asyncio.Task[None] | None = None

        self._subscriptions: dict[SubscribeId, EventSink] = {}

    async def discover(self, request: DiscoverRequest) -> DiscoverResponse:
        return DiscoverResponse(
            return_code=ReturnCode.OK,
            component_ref_list=list(_COMPONENT_REFS),
        )

    async def query(self, request: QueryRequest) -> QueryResponse:
        qt = request.query_type

        if qt == "robot_position":
            ts = datetime.now(timezone.utc).isoformat()
            x = f"{self._robot_x:.6f}"
            y = f"{self._robot_y:.6f}"
            theta = f"{self._robot_theta:.6f}"
            return QueryResponse(
                return_code=ReturnCode.OK,
                results=[
                    Result(name="timestamp", data_type_ref="DateTime", value=ts),
                    Result(name="robot_ref", data_type_ref="RoIS_IdentifierList", value='["kachaka"]'),
                    Result(name="position_data", data_type_ref="RoLoData[]", value=f'["{x}","{y}","{theta}"]'),
                ],
            )

        if qt == "waypoints":
            import json
            results = []
            for loc in _LOCATIONS:
                results.append(
                    Result(name="waypoint", data_type_ref="Waypoint", value=json.dumps(loc))
                )
            return QueryResponse(return_code=ReturnCode.OK, results=results)

        if qt == "component_status":
            status = ComponentStatus.BUSY if self._command_running else ComponentStatus.READY
            return QueryResponse(
                return_code=ReturnCode.OK,
                results=[Result(name="status", data_type_ref="ComponentStatus", value=status.value)],
            )

        return QueryResponse(return_code=ReturnCode.UNSUPPORTED)

    async def invoke(self, request: CommandRequest) -> InvokeResponse:
        ct = request.command_type

        if ct == CommandType.EXECUTE:
            # Extract target_positions from parameters.
            target = ""
            for p in request.parameters:
                if p.name == "target_positions":
                    positions = json.loads(p.value)
                    if positions:
                        target = positions[0]
                    break

            if not target:
                return InvokeResponse(return_code=ReturnCode.BAD_PARAMETER)

            # Check if the target is a known location name or id.
            if target not in _LOCATION_BY_KEY:
                return InvokeResponse(return_code=ReturnCode.BAD_PARAMETER)

            loc = _LOCATION_BY_KEY[target]
            self._command_running = True
            self._current_command_id = f"mock-cmd-{uuid.uuid4().hex[:8]}"
            self._current_target = target
            self._target_x = loc["pose"]["x"]
            self._target_y = loc["pose"]["y"]
            self._target_theta = loc["pose"]["theta"]

            # Start the movement simulation task.
            if self._move_task is not None:
                self._move_task.cancel()
            self._move_task = asyncio.create_task(self._move_to_target())

            return InvokeResponse(
                return_code=ReturnCode.OK,
                command_id=self._current_command_id,
            )

        if ct == CommandType.STOP:
            # Cancel the movement task.
            if self._move_task is not None:
                self._move_task.cancel()
                self._move_task = None
            self._command_running = False
            self._current_command_id = ""
            self._current_target = ""
            return InvokeResponse(return_code=ReturnCode.OK)

        if ct == CommandType.SET_PARAMETER:
            # Store parameters (no-op for the mock).
            return InvokeResponse(return_code=ReturnCode.OK)

        # START, SUSPEND, RESUME: no-op for Navigation.
        return InvokeResponse(return_code=ReturnCode.OK)

    async def subscribe(
        self,
        request: SubscribeRequest,
        sink: EventSink,
    ) -> SubscribeResponse:
        sub_id = f"mock-sub-{uuid.uuid4().hex[:8]}"
        self._subscriptions[sub_id] = sink
        return SubscribeResponse(return_code=ReturnCode.OK, subscribe_id=sub_id)

    async def unsubscribe(self, subscribe_id: SubscribeId) -> ReturnCode:
        self._subscriptions.pop(subscribe_id, None)
        return ReturnCode.OK

    async def _move_to_target(self) -> None:
        """Simulate movement from current position to target waypoint.

        Updates self._robot_x, self._robot_y, self._robot_theta at a fixed
        interval. Theta is computed as the heading from the current position
        to the target. When the robot reaches the target, emits reached_target
        and completed events to all active subscribers.
        """
        try:
            while True:
                dx = self._target_x - self._robot_x
                dy = self._target_y - self._robot_y
                dist = math.hypot(dx, dy)

                if dist < 0.01:
                    # Reached the target. Snap to exact position.
                    self._robot_x = self._target_x
                    self._robot_y = self._target_y
                    self._robot_theta = self._target_theta
                    break

                # Compute heading toward target.
                self._robot_theta = math.atan2(dy, dx)

                # Move one step toward the target.
                step = _MOVE_SPEED_MPS * _MOVE_POLL_SEC
                if dist <= step:
                    self._robot_x = self._target_x
                    self._robot_y = self._target_y
                    self._robot_theta = self._target_theta
                    break

                self._robot_x += (dx / dist) * step
                self._robot_y += (dy / dist) * step

                await asyncio.sleep(_MOVE_POLL_SEC)

            # Arrived. Emit events to all subscribers.
            target = self._current_target
            self._command_running = False
            self._current_command_id = ""
            self._current_target = ""
            self._move_task = None

            for sub_id, sink in list(self._subscriptions.items()):
                reached_event = EventEnvelope(
                    event_id=f"mock-evt-{uuid.uuid4().hex[:8]}",
                    event_type="reached_target",
                    subscribe_id=sub_id,
                    component_ref="kachaka/Navigation",
                    payload=[
                        Result(name="target", data_type_ref="string", value=target),
                        Result(name="is_final_target", data_type_ref="bool", value="true"),
                    ],
                )
                try:
                    await sink(reached_event)
                except Exception:
                    pass

                completed_event = EventEnvelope(
                    event_id=f"mock-evt-{uuid.uuid4().hex[:8]}",
                    event_type="completed",
                    subscribe_id=sub_id,
                    component_ref="kachaka/Navigation",
                    completed_status=CompletedStatus.OK,
                )
                try:
                    await sink(completed_event)
                except Exception:
                    pass

        except asyncio.CancelledError:
            self._move_task = None