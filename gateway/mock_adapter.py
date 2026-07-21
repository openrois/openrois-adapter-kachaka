"""Mock BusAdapter for gateway testing without a real robot.

Implements the BusAdapter protocol with hardcoded responses. Used with
`python main.py --mock` to test the full JSON-RPC dispatch on day 1 before
the intern's KachakaAdapter exists or a real Kachaka robot is available.
"""

from __future__ import annotations

import asyncio
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

_LOCATION_NAMES = {loc["name"] for loc in _LOCATIONS}
_LOCATION_IDS = {loc["id"] for loc in _LOCATIONS}

# Simulated command state. The mock simulates a 3-second navigation.
_COMMAND_DURATION_SEC = 3.0


class MockAdapter:
    """Minimal BusAdapter implementation with hardcoded responses."""

    def __init__(self) -> None:
        self._command_running = False
        self._current_command_id: CommandId = ""
        self._current_target: str = ""
        self._subscriptions: dict[SubscribeId, asyncio.Task[None]] = {}

    async def discover(self, request: DiscoverRequest) -> DiscoverResponse:
        return DiscoverResponse(
            return_code=ReturnCode.OK,
            component_ref_list=list(_COMPONENT_REFS),
        )

    async def query(self, request: QueryRequest) -> QueryResponse:
        qt = request.query_type

        if qt == "robot_position":
            ts = datetime.now(timezone.utc).isoformat()
            return QueryResponse(
                return_code=ReturnCode.OK,
                results=[
                    Result(name="timestamp", data_type_ref="DateTime", value=ts),
                    Result(name="robot_ref", data_type_ref="RoIS_IdentifierList", value='["kachaka"]'),
                    Result(name="position_data", data_type_ref="RoLoData[]", value='["1.23","4.56","0.78"]'),
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
                    # The value is a JSON-encoded list of strings.
                    import json
                    positions = json.loads(p.value)
                    if positions:
                        target = positions[0]
                    break

            if not target:
                return InvokeResponse(return_code=ReturnCode.BAD_PARAMETER)

            # Check if the target is a known location name or id.
            if target not in _LOCATION_NAMES and target not in _LOCATION_IDS:
                return InvokeResponse(return_code=ReturnCode.BAD_PARAMETER)

            self._command_running = True
            self._current_command_id = f"mock-cmd-{uuid.uuid4().hex[:8]}"
            self._current_target = target
            return InvokeResponse(
                return_code=ReturnCode.OK,
                command_id=self._current_command_id,
            )

        if ct == CommandType.STOP:
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
        task = asyncio.create_task(self._poll_command_state(sub_id, sink))
        self._subscriptions[sub_id] = task
        return SubscribeResponse(return_code=ReturnCode.OK, subscribe_id=sub_id)

    async def unsubscribe(self, subscribe_id: SubscribeId) -> ReturnCode:
        task = self._subscriptions.pop(subscribe_id, None)
        if task is not None:
            task.cancel()
        return ReturnCode.OK

    async def _poll_command_state(self, sub_id: SubscribeId, sink: EventSink) -> None:
        """Simulate polling: wait for a command to start, then emit events on completion."""
        try:
            # Wait until a command is running.
            while not self._command_running:
                await asyncio.sleep(0.5)

            # Wait for the simulated command duration.
            await asyncio.sleep(_COMMAND_DURATION_SEC)

            if not self._command_running:
                # Command was cancelled (STOP) while we were waiting.
                return

            # Emit reached_target event.
            target = self._current_target
            self._command_running = False
            self._current_command_id = ""
            self._current_target = ""

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
            await sink(reached_event)

            # Emit completed event.
            completed_event = EventEnvelope(
                event_id=f"mock-evt-{uuid.uuid4().hex[:8]}",
                event_type="completed",
                subscribe_id=sub_id,
                component_ref="kachaka/Navigation",
                completed_status=CompletedStatus.OK,
            )
            await sink(completed_event)

        except asyncio.CancelledError:
            pass