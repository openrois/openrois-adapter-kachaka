"""RoIS JSON-RPC handler for the thin gateway.

Maps JSON-RPC 2.0 method names to BusAdapter calls. The handler depends only
on the BusAdapter protocol from openrois-interfaces. It does not import
kachaka_api or websockets. The adapter is injected via constructor.

This is a thin placeholder. When the monorepo's openrois-engine package lands
(M1), this file is replaced by the formal engine. The adapter does not change.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from openrois.interfaces.bus import (
    CommandRequest,
    DiscoverRequest,
    EventEnvelope,
    EventSink,
    InvokeResponse,
    QueryRequest,
    QueryResponse,
    SubscribeRequest,
    SubscribeResponse,
)
from openrois.interfaces.hri import (
    CommandId,
    CommandType,
    Parameter,
    ReturnCode,
    SubscribeId,
)

# Type alias for a function that creates an EventSink bound to a specific
# WebSocket connection. The server provides this so the handler can create
# per-connection event sinks without knowing about websockets.
type SinkFactory = Callable[[SubscribeId], EventSink]


class RoISHandler:
    """Dispatch JSON-RPC method names to BusAdapter calls."""

    def __init__(self, adapter: object) -> None:
        # The adapter is typed as object here to avoid importing the BusAdapter
        # Protocol at runtime in this file. The handler calls the five async
        # methods that the protocol defines. Type checking is done by mypy
        # against the protocol in the adapter package.
        self._adapter = adapter

    async def dispatch(
        self,
        method: str,
        params: dict[str, object],
        sink_factory: SinkFactory | None = None,
    ) -> dict[str, object]:
        """Route a JSON-RPC method to the appropriate BusAdapter call.

        Returns a dict to be sent as the JSON-RPC result. If the method is not
        supported, returns a dict with return_code=UNSUPPORTED.
        """
        handlers: dict[str, Callable[..., Awaitable[dict[str, object]]]] = {
            "rois.system.connect": self._handle_connect,
            "rois.system.disconnect": self._handle_disconnect,
            "rois.command.search": self._handle_search,
            "rois.command.bind": self._handle_bind,
            "rois.command.release": self._handle_release,
            "rois.command.set_parameter": self._handle_set_parameter,
            "rois.command.execute": self._handle_execute,
            "rois.query.query": self._handle_query,
            "rois.event.subscribe": self._handle_subscribe,
            "rois.event.unsubscribe": self._handle_unsubscribe,
        }

        handler = handlers.get(method)
        if handler is None:
            return {"return_code": ReturnCode.UNSUPPORTED.value}

        if method == "rois.event.subscribe":
            if sink_factory is None:
                return {"return_code": ReturnCode.ERROR.value}
            return await handler(params, sink_factory)

        return await handler(params)

    # ------------------------------------------------------------------
    # System interface (no-op in the thin gateway)
    # ------------------------------------------------------------------

    async def _handle_connect(self, params: dict[str, object]) -> dict[str, object]:
        return {"return_code": ReturnCode.OK.value}

    async def _handle_disconnect(self, params: dict[str, object]) -> dict[str, object]:
        return {"return_code": ReturnCode.OK.value}

    # ------------------------------------------------------------------
    # Command interface
    # ------------------------------------------------------------------

    async def _handle_search(self, params: dict[str, object]) -> dict[str, object]:
        condition = str(params.get("condition", ""))
        request = DiscoverRequest(condition=condition)
        response = await self._adapter.discover(request)  # type: ignore[attr-defined]
        return {
            "return_code": response.return_code.value,
            "component_ref_list": response.component_ref_list,
        }

    async def _handle_bind(self, params: dict[str, object]) -> dict[str, object]:
        # No-op in the thin gateway. No bind/release lifecycle.
        return {"return_code": ReturnCode.OK.value}

    async def _handle_release(self, params: dict[str, object]) -> dict[str, object]:
        return {"return_code": ReturnCode.OK.value}

    async def _handle_set_parameter(
        self, params: dict[str, object]
    ) -> dict[str, object]:
        component_ref = str(params.get("component_ref", ""))
        raw_params = params.get("parameters", [])
        parameters = self._parse_parameters(raw_params)

        request = CommandRequest(
            component_ref=component_ref,
            command_type=CommandType.SET_PARAMETER,
            command_id="",
            parameters=parameters,
        )
        response = await self._adapter.invoke(request)  # type: ignore[attr-defined]
        return {
            "return_code": response.return_code.value,
            "command_id": response.command_id,
        }

    async def _handle_execute(self, params: dict[str, object]) -> dict[str, object]:
        component_ref = str(params.get("component_ref", ""))
        raw_params = params.get("parameters", [])
        parameters = self._parse_parameters(raw_params)

        request = CommandRequest(
            component_ref=component_ref,
            command_type=CommandType.EXECUTE,
            command_id="",
            parameters=parameters,
        )
        response = await self._adapter.invoke(request)  # type: ignore[attr-defined]
        return {
            "return_code": response.return_code.value,
            "command_id": response.command_id,
        }

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    async def _handle_query(self, params: dict[str, object]) -> dict[str, object]:
        component_ref = str(params.get("component_ref", ""))
        query_type = str(params.get("query_type", ""))
        condition = str(params.get("condition", ""))

        request = QueryRequest(
            component_ref=component_ref,
            query_type=query_type,
            condition=condition,
        )
        response: QueryResponse = await self._adapter.query(request)  # type: ignore[attr-defined]
        return {
            "return_code": response.return_code.value,
            "results": [r.model_dump() for r in response.results],
        }

    # ------------------------------------------------------------------
    # Event interface
    # ------------------------------------------------------------------

    async def _handle_subscribe(
        self,
        params: dict[str, object],
        sink_factory: SinkFactory,
    ) -> dict[str, object]:
        component_ref = str(params.get("component_ref", ""))
        event_type = str(params.get("event_type", ""))
        condition = str(params.get("condition", ""))

        request = SubscribeRequest(
            component_ref=component_ref,
            event_type=event_type,
            condition=condition,
        )

        # The subscribe_id is not known until the adapter responds, but the
        # sink needs to be bound to the connection. We create a sink that
        # captures the connection and is passed to the adapter. The adapter
        # calls it when events fire.
        # We use a placeholder subscribe_id for the sink factory. The real
        # subscribe_id comes back from the adapter.
        sink = sink_factory("")
        response: SubscribeResponse = await self._adapter.subscribe(request, sink)  # type: ignore[attr-defined]
        return {
            "return_code": response.return_code.value,
            "subscribe_id": response.subscribe_id,
        }

    async def _handle_unsubscribe(self, params: dict[str, object]) -> dict[str, object]:
        subscribe_id = str(params.get("subscribe_id", ""))
        result: ReturnCode = await self._adapter.unsubscribe(subscribe_id)  # type: ignore[attr-defined]
        return {"return_code": result.value}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_parameters(raw: object) -> list[Parameter]:
        """Parse a list of raw parameter dicts into Parameter models."""
        if not isinstance(raw, list):
            return []
        params = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            params.append(
                Parameter(
                    name=str(item.get("name", "")),
                    data_type_ref=str(item.get("data_type_ref", "")),
                    value=str(item.get("value", "")),
                )
            )
        return params