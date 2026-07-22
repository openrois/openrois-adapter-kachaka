"""Kachaka BusAdapter — bridges the RoIS BusAdapter contract to Kachaka gRPC.

This module implements :class:`KachakaAdapter`, the concrete ``BusAdapter``
that translates RoIS operations (``discover``, ``query``, ``invoke``,
``subscribe``, ``unsubscribe``) into Kachaka gRPC API calls.

The adapter owns a :class:`kachaka_api.aio.KachakaApiClient` instance. It is
constructed with a :class:`KachakaAdapterConfig` and must be ``await``-ed
(connected) before use, or used as an async context manager.

Morning scope (synchronous operations):

* :meth:`discover` — returns the static component list.
* :meth:`query` — routes ``robot_position``, ``waypoints``, ``component_status``.
* :meth:`invoke` — routes ``EXECUTE``, ``STOP``, ``SET_PARAMETER``,
  ``START``/``SUSPEND``/``RESUME`` (no-ops for Navigation).

Afternoon scope (events):

* :meth:`subscribe` / :meth:`unsubscribe` — poller-based event delivery
  (implemented in a later iteration).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from kachaka_api.aio import KachakaApiClient
from kachaka_api.generated import kachaka_api_pb2 as pb2
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
    CommandType,
    Parameter,
    ReturnCode,
    Result,
    SubscribeId,
)

from openrois_bus_kachaka.config import KachakaAdapterConfig
from openrois_bus_kachaka.mapping import (
    command_state_to_component_status,
    locations_to_waypoints_result,
    pose_to_robot_position_result,
)
from openrois_bus_kachaka.poller import CommandStatePoller

if TYPE_CHECKING:
    pass


class KachakaAdapter:
    """BusAdapter implementation for the Kachaka robot via gRPC.

    Implements the :class:`openrois.interfaces.bus.BusAdapter` protocol by
    translating each RoIS operation to one or more Kachaka gRPC calls.

    Usage::

        config = KachakaAdapterConfig(target="192.168.1.100:26400")
        adapter = KachakaAdapter(config)
        await adapter.connect()
        response = await adapter.discover(DiscoverRequest())
        await adapter.close()
    """

    def __init__(self, config: KachakaAdapterConfig) -> None:
        """Initialise the adapter with the given configuration.

        Does **not** connect to the robot. Call :meth:`connect` (or use the
        adapter as an async context manager) before invoking any BusAdapter
        method.

        Args:
            config: Adapter configuration (gRPC target, poll interval, etc.).
        """
        self._config = config
        self._client = KachakaApiClient(target=config.target)
        self._connected = False
        # Stored parameters from SET_PARAMETER, used by later EXECUTE.
        self._stored_target_positions: list[str] = []
        self._stored_time_limit: int = 0
        self._stored_routing_policy: str = "time"
        # Active event subscriptions: subscribe_id → CommandStatePoller.
        self._pollers: dict[SubscribeId, CommandStatePoller] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the Kachaka robot and initialise the resolver.

        Calls ``update_resolver()`` so location name→id resolution works, and
        optionally disables auto-homing per the config.
        """
        await self._client.update_resolver()
        await self._client.set_auto_homing_enabled(self._config.auto_homing)
        self._connected = True

    async def close(self) -> None:
        """Close the gRPC channel to the Kachaka robot.

        Stops all active event pollers first to avoid task leaks.
        """
        # Stop all active pollers.
        for sub_id in list(self._pollers):
            await self.unsubscribe(sub_id)

        # The gRPC aio channel is accessible via the stub's internal channel.
        # grpc.aio.Channel.close() is a coroutine.
        channel = getattr(self._client.stub, "_channel", None)
        if channel is not None:
            await channel.close()
        self._connected = False

    async def __aenter__(self) -> KachakaAdapter:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # BusAdapter protocol — discover
    # ------------------------------------------------------------------

    async def discover(self, request: DiscoverRequest) -> DiscoverResponse:
        """Return the static component list for this adapter.

        Kachaka has a fixed set of components (Navigation + SystemInformation).
        The ``condition`` field is ignored — all components are always returned.

        Args:
            request: Discover request (condition is ignored).

        Returns:
            :class:`DiscoverResponse` with ``return_code=OK`` and the
            configured ``component_ref_list``.
        """
        return DiscoverResponse(
            return_code=ReturnCode.OK,
            component_ref_list=list(self._config.component_refs),
        )

    # ------------------------------------------------------------------
    # BusAdapter protocol — query
    # ------------------------------------------------------------------

    async def query(self, request: QueryRequest) -> QueryResponse:
        """Execute a synchronous query on a Kachaka component.

        Routes by ``query_type``:

        ``"robot_position"`` → ``get_robot_pose()`` →
            :class:`SystemInformationRobotPositionResult` serialised to
            :class:`ResultList`.

        ``"waypoints"`` → ``get_locations()`` → custom :class:`ResultList`
            with one :class:`Result` per location (``"id|name|x|y|theta"``).

        ``"component_status"`` → ``get_command_state()`` →
            :class:`NavigationStatusResult` serialised to :class:`ResultList`.

        Args:
            request: Query request with ``query_type`` and optional ``condition``.

        Returns:
            :class:`QueryResponse` with ``return_code=OK`` and ``results``,
            or ``return_code=UNSUPPORTED`` for unknown query types.
        """
        qt = request.query_type
        if qt == "robot_position":
            pose = await self._client.get_robot_pose()
            result_model = pose_to_robot_position_result(pose)
            results: list[Result] = [
                Result(
                    name="robot_position",
                    data_type_ref="SystemInformationRobotPositionResult",
                    value=result_model.model_dump_json(),
                ),
            ]
            return QueryResponse(return_code=ReturnCode.OK, results=results)

        if qt == "waypoints":
            locations = await self._client.get_locations()
            wp_results = locations_to_waypoints_result(list(locations))
            return QueryResponse(return_code=ReturnCode.OK, results=wp_results)

        if qt == "component_status":
            state, _command = await self._client.get_command_state()
            status = command_state_to_component_status(state)
            results = [
                Result(
                    name="component_status",
                    data_type_ref="NavigationStatusResult",
                    value=status.value,
                ),
            ]
            return QueryResponse(return_code=ReturnCode.OK, results=results)

        # Unknown query type.
        return QueryResponse(
            return_code=ReturnCode.UNSUPPORTED,
            results=[],
        )

    # ------------------------------------------------------------------
    # BusAdapter protocol — invoke
    # ------------------------------------------------------------------

    async def invoke(self, request: CommandRequest) -> InvokeResponse:
        """Invoke a command on a Kachaka component.

        Routes by ``command_type``:

        ``EXECUTE`` — resolve ``arguments[0].value`` (or stored
            ``target_positions[0]``) as a location name/id, call
            ``move_to_location(..., wait_for_completion=False)``. Returns
            immediately. Unknown locations return ``BAD_PARAMETER``.

        ``STOP`` — call ``cancel_command()`` to stop the running navigation.

        ``SET_PARAMETER`` — store ``target_positions``, ``time_limit``,
            ``routing_policy`` from the request parameters for later EXECUTE.

        ``START`` / ``SUSPEND`` / ``RESUME`` — no-ops for Navigation (no
            start/stop lifecycle). Return ``OK``.

        Args:
            request: Command request with ``command_type`` and arguments.

        Returns:
            :class:`InvokeResponse` with ``return_code`` and optional
            ``command_id``.
        """
        ct = request.command_type

        if ct == CommandType.EXECUTE:
            return await self._invoke_execute(request)

        if ct == CommandType.STOP:
            return await self._invoke_stop(request)

        if ct == CommandType.SET_PARAMETER:
            return self._invoke_set_parameter(request)

        if ct in (CommandType.START, CommandType.SUSPEND, CommandType.RESUME):
            # Navigation has no start/stop lifecycle — no-op.
            return InvokeResponse(
                return_code=ReturnCode.OK,
                command_id=request.command_id,
            )

        # Unknown command type.
        return InvokeResponse(
            return_code=ReturnCode.UNSUPPORTED,
            command_id=request.command_id,
        )

    # ------------------------------------------------------------------
    # BusAdapter protocol — subscribe / unsubscribe (afternoon task stubs)
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        request: SubscribeRequest,
        sink: EventSink,
    ) -> SubscribeResponse:
        """Subscribe to async events from a Kachaka component.

        Starts a :class:`CommandStatePoller` that polls
        ``get_command_state()`` at the configured interval. When a command
        transitions from ``RUNNING`` to idle, the poller emits
        ``reached_target`` and ``completed`` events via ``sink``.

        Args:
            request: Subscribe request with ``event_type`` and ``component_ref``.
            sink: Async callback invoked for each matching event.

        Returns:
            :class:`SubscribeResponse` with ``return_code=OK`` and a
            generated ``subscribe_id``.
        """
        subscribe_id = f"kachaka-sub-{uuid.uuid4().hex[:8]}"
        poller = CommandStatePoller(
            client=self._client,
            sink=sink,
            subscribe_id=subscribe_id,
            component_ref=request.component_ref,
            poll_interval_sec=self._config.poll_interval_sec,
        )
        self._pollers[subscribe_id] = poller
        poller.start()
        return SubscribeResponse(
            return_code=ReturnCode.OK,
            subscribe_id=subscribe_id,
        )

    async def unsubscribe(self, subscribe_id: SubscribeId) -> ReturnCode:
        """Cancel an event subscription.

        Stops the :class:`CommandStatePoller` for the given ``subscribe_id``
        and removes it from the registry. Duplicate calls are silently
        ignored (no error).

        Args:
            subscribe_id: The subscription ID to cancel.

        Returns:
            :class:`ReturnCode.OK`.
        """
        poller = self._pollers.pop(subscribe_id, None)
        if poller is not None:
            await poller.stop()
        return ReturnCode.OK

    # ------------------------------------------------------------------
    # Private helpers — invoke sub-handlers
    # ------------------------------------------------------------------

    async def _invoke_execute(self, request: CommandRequest) -> InvokeResponse:
        """Handle EXECUTE: resolve location, call move_to_location (fire-and-forget).

        The target location can arrive via three channels (checked in order):

        1. ``request.arguments[0].value`` — direct call with an Argument.
        2. ``request.parameters`` — gateway sends ``target_positions`` as a
           Parameter whose ``value`` is a JSON array string (e.g.
           ``'["充電ドック"]'``). This matches the mock adapter's convention.
        3. ``self._stored_target_positions`` — from a prior SET_PARAMETER.
        """
        target: str | None = None

        # Priority 1: arguments list (e.g. from a direct execute call).
        if request.arguments:
            target = request.arguments[0].value

        # Priority 2: parameters list (gateway dispatch path).
        if target is None:
            for param in request.parameters:
                if param.name == "target_positions":
                    try:
                        positions = json.loads(param.value)
                        if isinstance(positions, list) and positions:
                            target = str(positions[0])
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Priority 3: stored target_positions from a prior SET_PARAMETER.
        if target is None and self._stored_target_positions:
            target = self._stored_target_positions[0]

        if not target:
            return InvokeResponse(
                return_code=ReturnCode.BAD_PARAMETER,
                command_id=request.command_id,
            )

        # Validate the location exists by checking against get_locations().
        # The resolver silently passes through unknown names, so we verify
        # explicitly to return BAD_PARAMETER for unknown locations.
        locations = await self._client.get_locations()
        known_names = {loc.name for loc in locations}
        known_ids = {loc.id for loc in locations}
        if target not in known_names and target not in known_ids:
            return InvokeResponse(
                return_code=ReturnCode.BAD_PARAMETER,
                command_id=request.command_id,
            )

        # Fire-and-forget: wait_for_completion=False returns immediately.
        result = await self._client.move_to_location(
            target,
            wait_for_completion=False,
        )

        if not result.success:
            return InvokeResponse(
                return_code=ReturnCode.ERROR,
                command_id=request.command_id,
            )

        # Use the request's command_id if provided, otherwise generate one.
        # Kachaka doesn't expose the command_id when wait_for_completion=False.
        cmd_id = request.command_id or f"kachaka-cmd-{uuid.uuid4().hex[:8]}"
        return InvokeResponse(
            return_code=ReturnCode.OK,
            command_id=cmd_id,
        )

    async def _invoke_stop(self, request: CommandRequest) -> InvokeResponse:
        """Handle STOP: cancel the running command."""
        result, _command = await self._client.cancel_command()
        return InvokeResponse(
            return_code=ReturnCode.OK if result.success else ReturnCode.ERROR,
            command_id=request.command_id,
        )

    def _invoke_set_parameter(self, request: CommandRequest) -> InvokeResponse:
        """Handle SET_PARAMETER: store parameters for later EXECUTE.

        Extracts ``target_positions``, ``time_limit``, and ``routing_policy``
        from the request's ``parameters`` list. These are used by a subsequent
        EXECUTE call that has no explicit target in its arguments.
        """
        for param in request.parameters:
            if param.name == "target_positions":
                # Stored as a comma-separated list or single value.
                self._stored_target_positions = [
                    v.strip() for v in param.value.split(",") if v.strip()
                ]
            elif param.name == "time_limit":
                try:
                    self._stored_time_limit = int(param.value)
                except ValueError:
                    pass
            elif param.name == "routing_policy":
                self._stored_routing_policy = param.value

        return InvokeResponse(
            return_code=ReturnCode.OK,
            command_id=request.command_id,
        )