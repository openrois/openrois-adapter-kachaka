"""Async polling task for Kachaka command completion events.

Kachaka has no server-push for command completion — the gRPC API only exposes
``GetCommandState`` (returns ``RUNNING`` / ``PENDING`` / ``UNSPECIFIED``) and
``GetLastCommandResult`` (returns the final ``Result`` of the most recent
command). To deliver RoIS events (``reached_target``, ``completed``) we poll
these endpoints at a configurable interval and emit events when the command
transitions from ``RUNNING`` to idle.

The poller is an ``asyncio.Task`` owned by :class:`KachakaAdapter`. Each
subscription gets its own poller. ``unsubscribe()`` cancels the task.

Event emission rules
--------------------

1. When ``get_command_state()`` transitions from ``RUNNING`` to
   ``PENDING``/``UNSPECIFIED`` (command just finished), the poller calls
   ``get_last_command_result()`` to check ``Result.success``.

2. If ``success`` is ``True`` → emit ``reached_target`` then ``completed``
   with ``CompletedStatus.OK``.

3. If ``success`` is ``False`` → emit ``completed`` with
   ``CompletedStatus.ERROR`` (no ``reached_target``).

4. If the poller is cancelled (via ``unsubscribe()``) while the command is
   still running, no events are emitted — the subscription is gone.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from kachaka_api.generated import kachaka_api_pb2 as pb2
from openrois.interfaces.bus import EventEnvelope, EventSink
from openrois.interfaces.hri import Result, ReturnCode, SubscribeId
from openrois.interfaces.service import CompletedStatus

if TYPE_CHECKING:
    from kachaka_api.aio import KachakaApiClient

logger = logging.getLogger(__name__)


class CommandStatePoller:
    """Polls Kachaka command state and emits RoIS events on completion.

    The poller is started by :meth:`start` and stopped by :meth:`stop`.
    It runs an ``asyncio.Task`` that loops:

    1. If no command is running, wait for one to start (poll ``get_command_state``).
    2. Once a command is ``RUNNING``, poll until it transitions to idle.
    3. On transition, call ``get_last_command_result()`` and emit events.

    The poller tracks the last-seen command_id so it only fires events for
    *new* command completions, not stale results from previous commands.
    """

    def __init__(
        self,
        client: KachakaApiClient,
        sink: EventSink,
        subscribe_id: SubscribeId,
        component_ref: str = "kachaka/Navigation",
        poll_interval_sec: float = 0.5,
    ) -> None:
        """Initialise the poller (does not start polling).

        Args:
            client: The Kachaka async gRPC client.
            sink: The RoIS event sink to call when events fire.
            subscribe_id: The subscription ID this poller belongs to.
            component_ref: Component ref to include in event envelopes.
            poll_interval_sec: Seconds between polls.
        """
        self._client = client
        self._sink = sink
        self._subscribe_id = subscribe_id
        self._component_ref = component_ref
        self._poll_interval = poll_interval_sec
        self._task: asyncio.Task[None] | None = None
        # Track the command_id we last emitted events for, so we don't
        # re-emit for a stale result on the next subscription.
        self._last_emitted_command_id: str = ""
        # Track whether we've seen a RUNNING state in this cycle, so we
        # only emit events on the RUNNING → idle transition.
        self._was_running = False
        # Baseline command_id captured on first poll. If the command_id
        # changes from the baseline while we never saw RUNNING, it means
        # a command completed before our first poll caught it.
        self._baseline_command_id: str | None = None

    def start(self) -> None:
        """Start the polling task."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main polling loop — runs until cancelled.

        State machine:

        * First poll: capture the baseline ``command_id``. If a command is
          already ``RUNNING``, set ``_was_running = True``.
        * Subsequent polls:
          - If ``RUNNING``: set ``_was_running = True``, remember the
            ``command_id``.
          - If not ``RUNNING`` and ``_was_running``: the command we were
            tracking just finished → emit events, reset ``_was_running``.
          - If not ``RUNNING`` and not ``_was_running`` but the
            ``command_id`` changed from the last one we saw (or from
            baseline): a command started and completed between polls →
            emit events for the fast-completed command.
        """
        try:
            while True:
                state, command = await self._client.get_command_state()
                cmd_id = self._extract_command_id(command)

                # First poll: establish baseline.
                if self._baseline_command_id is None:
                    self._baseline_command_id = cmd_id
                    if state == pb2.CommandState.COMMAND_STATE_RUNNING:
                        self._was_running = True
                    logger.debug(
                        "Poller %s baseline: state=%s, cmd_id=%s...",
                        self._subscribe_id,
                        state,
                        cmd_id[:16],
                    )
                    await asyncio.sleep(self._poll_interval)
                    continue

                if state == pb2.CommandState.COMMAND_STATE_RUNNING:
                    self._was_running = True
                elif self._was_running:
                    # Transition from RUNNING → idle: command finished.
                    logger.debug(
                        "Poller %s: RUNNING→idle, emitting events",
                        self._subscribe_id,
                    )
                    self._was_running = False
                    await self._emit_events(command)
                elif (
                    cmd_id
                    and cmd_id != self._baseline_command_id
                    and cmd_id != self._last_emitted_command_id
                ):
                    # A command completed between polls (fast completion).
                    logger.debug(
                        "Poller %s: cmd_id changed (fast completion), emitting events",
                        self._subscribe_id,
                    )
                    await self._emit_events(command)

                # Update baseline to current so we only react to *new* changes.
                self._baseline_command_id = cmd_id

                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.debug("Poller for %s cancelled", self._subscribe_id)
            raise

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit_events(self, command: pb2.Command) -> None:
        """Emit ``reached_target`` and/or ``completed`` events.

        Called when the command transitions from ``RUNNING`` to idle. Fetches
        the last command result to determine success/failure and the actual
        command that was executed (for target extraction).

        Args:
            command: The ``Command`` protobuf from ``get_command_state()``.
                May be empty if no command was running — the actual command
                is fetched from ``get_last_command_result()``.
        """
        result, last_command = await self._client.get_last_command_result()

        # Use the command from get_last_command_result() for target extraction,
        # since the command from get_command_state() is empty after completion.
        target = self._extract_target(last_command)

        if result.success:
            # Mark this command as emitted so we don't re-fire.
            self._last_emitted_command_id = self._extract_command_id(last_command)

            # Emit reached_target event.
            reached = EventEnvelope(
                event_id=f"kachaka-evt-{uuid.uuid4().hex[:8]}",
                event_type="reached_target",
                subscribe_id=self._subscribe_id,
                component_ref=self._component_ref,
                payload=[
                    Result(
                        name="target",
                        data_type_ref="string",
                        value=target,
                    ),
                    Result(
                        name="is_final_target",
                        data_type_ref="bool",
                        value="true",
                    ),
                ],
            )
            try:
                await self._sink(reached)
            except Exception:
                logger.exception(
                    "Error delivering reached_target event to sink %s",
                    self._subscribe_id,
                )

            # Emit completed event with OK status.
            completed = EventEnvelope(
                event_id=f"kachaka-evt-{uuid.uuid4().hex[:8]}",
                event_type="completed",
                subscribe_id=self._subscribe_id,
                component_ref=self._component_ref,
                completed_status=CompletedStatus.OK,
            )
            try:
                await self._sink(completed)
            except Exception:
                logger.exception(
                    "Error delivering completed event to sink %s",
                    self._subscribe_id,
                )
        else:
            # Command failed — emit completed with ERROR status.
            self._last_emitted_command_id = self._extract_command_id(last_command)
            logger.warning(
                "Command failed (error_code=%d) for subscription %s",
                result.error_code,
                self._subscribe_id,
            )
            completed = EventEnvelope(
                event_id=f"kachaka-evt-{uuid.uuid4().hex[:8]}",
                event_type="completed",
                subscribe_id=self._subscribe_id,
                component_ref=self._component_ref,
                completed_status=CompletedStatus.ERROR,
            )
            try:
                await self._sink(completed)
            except Exception:
                logger.exception(
                    "Error delivering completed event to sink %s",
                    self._subscribe_id,
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_target(command: pb2.Command) -> str:
        """Extract the target location name/id from a Kachaka ``Command``.

        The ``Command`` protobuf has a ``oneof`` with 14 command variants.
        For ``move_to_location_command``, we return the ``target_location_id``.
        For other commands (or empty commands), we return an empty string.
        """
        if command.HasField("move_to_location_command"):
            return command.move_to_location_command.target_location_id
        return ""

    @staticmethod
    def _extract_command_id(command: pb2.Command) -> str:
        """Extract a stable identity string from a Kachaka ``Command``.

        The ``Command`` protobuf doesn't have a top-level ``command_id`` field
        (that's on the response messages). We use the serialized bytes as a
        fingerprint — if the command changes, the bytes change. For an empty
        command (no command running), this returns an empty string.
        """
        oneof = command.WhichOneof("command")
        if oneof is None:
            return ""
        return command.SerializeToString().hex()