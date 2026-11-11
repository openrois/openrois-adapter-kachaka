"""The connection of the Kachaka components to one robot, over the Kachaka gRPC API.

The components of one robot share one gRPC channel. An adapter creates one
``KachakaConnection`` per robot, a ``Resource``, and gives it to each component, which
records it with ``Component.uses``. The engine opens it before the first of them connects
and closes it after the last one disconnects.

The connection outlives the robot's presence on the network. A watchdog asks the robot
``IsReady`` every ``retry`` seconds. While the robot is off, restarts or drops off the
network, the connection raises ERROR, which every component of the robot reports: clients
get ``rois.system.notify_error``, component_status says ERROR, and the engine refuses the
components' commands and queries. Once the robot answers again the error is cleared. So
the adapter and the robot start in any order, and a robot that comes back is driven again
without a restart. A robot that answers but is not ready to accept commands, for example
while it starts up, raises WARNING instead.

A robot that answered once and then stops answering is lost only once it has not
answered for ``lost_after`` seconds, so a short pause of the Wi-Fi neither raises ERROR
nor ends a running command. The channel tries to reach the robot again every ``retry``
seconds, and a channel that still fails while the robot accepts connections again is
replaced by a new one.

Each time the robot comes up, after a start, a restart or a loss, the connection reads
the robot's table of error codes, for ``describe``, and runs the hooks registered with
``on_connect``, such as a setting a component applies to the robot.

The robot runs one command at a time, for every client and for the Kachaka app. The
connection records which component started each command it starts, the command's owner,
and ``cancel`` cancels the running command only for its owner. So a component never
cancels the motion of another component, of another client or of the Kachaka app. A
command that starts while another one runs replaces it, as on the robot, and the
command it replaced ends with ``CommandPreempted``.

``grpc`` and the Kachaka API stubs are imported when the connection opens, so the
package imports without the ``robot`` extra. Without it, ``open`` fails and every
component of the robot reports ERROR.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from openrois.components.core import Resource
from openrois.interfaces.common import ComponentStatus

if TYPE_CHECKING:
    from kachaka_api.generated import kachaka_api_pb2 as pb2

__all__ = [
    "DEFAULT_PORT",
    "INSTALL_HINT",
    "CommandPreempted",
    "KachakaConnection",
    "OnConnect",
    "parse_address",
]

logger = logging.getLogger(__name__)

#: The port of the Kachaka gRPC API.
DEFAULT_PORT = 26400

INSTALL_HINT = (
    "The Kachaka components need the Kachaka API, which the robot extra installs: "
    'pip install "openrois-adapter-kachaka[robot]"'
)

#: A hook the connection runs each time the robot comes up.
type OnConnect = Callable[[], Awaitable[None]]

# How often the watchdog checks a robot that stopped answering but is not lost yet, at
# most, in seconds.
_GRACE_CHECK = 1.0

# The gRPC keepalive: a ping after this many milliseconds without data finds a robot that
# went away without closing the connection, as over Wi-Fi.
_KEEPALIVE_MS = 20_000

# How many checks in a row may find the robot accepting connections while the channel
# still fails before the channel is replaced.
_STUCK_CHECKS = 2

# How long a started command may be neither running nor finished before it counts as
# replaced by another command, in seconds. The robot can take a moment to report it.
_MISSING_GRACE = 1.0

# How long a command may go unfollowed while the robot does not answer, beyond
# ``lost_after``, in case the watchdog has not counted the robot as lost yet, in seconds.
_FOLLOW_SLACK = 1.0


class CommandPreempted(RuntimeError):  # noqa: N818, the name says what happened to it.
    """Another command replaced a command before it ended.

    Either another component started a command through the connection, which the robot
    ran in place of ours. Or ours ended without a result the robot reports: the robot
    keeps only its latest command result, another client or the Kachaka app started a
    command that replaced ours, and the robot's history has no record of ours.
    """


def parse_address(address: str) -> tuple[str, int]:
    """The host and the port of a Kachaka address, ``host`` or ``host:port``.

    The port defaults to 26400. An IPv6 address with a port is written in brackets,
    ``[::1]:26400``.

    Raises:
        ValueError: The address is empty, or the port is not a number from 1 to 65535.
    """
    text = address.strip()
    if not text:
        raise ValueError("The Kachaka address is empty")
    host, port, colon = text, "", False
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError(f"The Kachaka address {address!r} has no closing bracket")
        host, rest = text[1:end], text[end + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise ValueError(f"The Kachaka address {address!r} is not [host]:port")
            port, colon = rest[1:], True
    elif text.count(":") == 1:
        host, port = text.split(":")
        colon = True
    if not host:
        raise ValueError(f"The Kachaka address {address!r} has no host")
    if not colon:
        return host, DEFAULT_PORT
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(
            f"The port of the Kachaka address {address!r} is not a number from 1 to 65535"
        )
    return host, int(port)


class KachakaConnection(Resource):
    """One gRPC channel to one Kachaka, shared by the components of the robot.

    Args:
        address: Where the robot's gRPC API is: ``host`` or ``host:port``, for example
            ``192.168.1.100``. The port defaults to 26400.
        timeout: How long to wait for an answer of the robot, in seconds. Every call has
            this deadline unless it gives its own.
        retry: How often the watchdog checks the robot, and the channel tries to reach
            it again while it does not answer, in seconds.
        lost_after: How long a robot that answered before may stop answering before the
            connection counts it as lost and raises ERROR, in seconds.
        poll: How long one long-poll for a command result waits, in seconds, before the
            connection checks that the command still runs.

    Raises:
        ValueError: The address is malformed, or a duration is not positive.
    """

    def __init__(
        self,
        *,
        address: str,
        timeout: float = 5.0,
        retry: float = 3.0,
        lost_after: float = 6.0,
        poll: float = 10.0,
    ) -> None:
        self.host, self.port = parse_address(address)
        for name, value in (("timeout", timeout), ("retry", retry), ("poll", poll)):
            if value <= 0:
                raise ValueError(f"{name} must be positive, not {value}")
        if lost_after < 0:
            raise ValueError(f"lost_after must not be negative, not {lost_after}")
        self.timeout = timeout
        self.retry = retry
        self.lost_after = lost_after
        self.poll = poll
        # The reconnect backoff of the channel, in milliseconds.
        self._backoff_ms = max(50, int(retry * 1000))
        self._hooks: list[OnConnect] = []
        self._pending: list[OnConnect] = []
        self._failing_hooks: set[int] = set()
        self._titles: dict[int, str] = {}
        self._robot_errors: set[int] = set()
        # The owner of each command started here that may still run, by command id.
        self._owners: dict[str, object] = {}
        # Held while a StartCommand is on its way, which may replace a running command.
        self._starting = asyncio.Lock()
        self._grpc: Any = None
        self._pb2_grpc: Any = None
        self._channel: Any = None
        self._stub: Any = None
        self._open = False
        self._answered = False
        self._up = False
        self._raised: tuple[ComponentStatus, str] | None = None
        self._failing_since: float | None = None
        self._stuck = 0
        self._watch_failing = False
        self._wake: asyncio.Event | None = None
        self._watchdog: asyncio.Task[None] | None = None

    @property
    def address(self) -> str:
        """``host:port``, where the robot's gRPC API is."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    @property
    def is_open(self) -> bool:
        """Whether the engine opened the connection and has not closed it."""
        return self._open

    @property
    def is_up(self) -> bool:
        """Whether the robot answered the last check and was ready to accept commands."""
        return self._up

    def on_connect(self, hook: OnConnect) -> None:
        """Run ``hook`` each time the robot comes up, ready to accept commands.

        Call it in a component's ``__init__``, for a setting the robot must have, so
        that a robot that restarts gets it again. A hook that raises is logged and tried
        again at the next check of the watchdog.
        """
        self._hooks.append(hook)
        if self._up:
            self._pending.append(hook)
            self._wake_watchdog()

    def title(self, code: int) -> str | None:
        """The English title of a robot error code, or None for a code the robot did not
        list."""
        return self._titles.get(code)

    def describe(self, code: int) -> str:
        """A robot error code with its title, for a message: ``error 10001 (title)``."""
        title = self._titles.get(code)
        return f"error {code} ({title})" if title else f"error {code}"

    async def open(self) -> None:
        """Create the channel, check the robot once, and start the watchdog.

        A robot that does not answer raises ERROR, and the watchdog reaches it later.

        Raises:
            ImportError: The Kachaka API stubs are not installed.
        """
        try:
            import grpc
            from kachaka_api.generated import kachaka_api_pb2_grpc
        except ImportError as exc:
            self._set_status(ComponentStatus.ERROR, INSTALL_HINT)
            raise ImportError(INSTALL_HINT) from exc
        self._grpc = grpc
        self._pb2_grpc = kachaka_api_pb2_grpc
        self._open = True
        self._answered = False
        self._up = False
        self._failing_since = None
        self._stuck = 0
        self._wake = asyncio.Event()
        self._new_channel()
        try:
            await self._check()
        except Exception:
            logger.exception("The first check of the Kachaka at %s failed", self.address)
        self._watchdog = asyncio.create_task(self._watch(), name="kachaka-watchdog")

    async def close(self) -> None:
        """Stop the watchdog and close the channel."""
        self._open = False
        self._up = False
        if self._watchdog is not None:
            self._watchdog.cancel()
            try:
                await self._watchdog
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("The Kachaka watchdog failed")
            finally:
                self._watchdog = None
        channel, self._channel, self._stub = self._channel, None, None
        if channel is not None:
            await channel.close()
            logger.info("Closed the connection to the Kachaka at %s", self.address)

    async def call(self, rpc: str, request: object, *, timeout: float | None = None) -> Any:
        """Call one unary RPC of the Kachaka API, with a deadline, and return its response.

        Args:
            rpc: The name of the RPC, for example ``"GetLocations"``.
            request: Its request message.
            timeout: The deadline in seconds; ``timeout`` of the connection by default.

        Raises:
            RuntimeError: The connection is not open, or the robot refused the call.
            ConnectionError: The robot did not answer in time. The watchdog checks it at
                once.
        """
        try:
            return await self._rpc(rpc, request, self.timeout if timeout is None else timeout)
        except ConnectionError:
            self._wake_watchdog()
            raise

    async def cancel(self, owner: object) -> bool:
        """Cancel the robot's running command if ``owner`` started it with ``run_command``.

        A command that another component started, another client or the Kachaka app, is
        left running. The robot is not asked while no command of ``owner`` may run.

        Returns:
            Whether a cancel was sent.

        Raises:
            RuntimeError: The connection is not open, or the robot refused the call.
            ConnectionError: The robot did not answer in time.
        """
        owned = {command_id for command_id, by in self._owners.items() if by is owner}
        if not owned or await self._running_command() not in owned:
            return False
        await self._cancel_running()
        return True

    async def run_command(
        self,
        command: pb2.Command,
        *,
        title: str = "",
        cancel_all: bool = True,
        owner: object | None = None,
        started: Callable[[str], None] | None = None,
    ) -> pb2.Result:
        """Start a command on the robot and wait until it ends.

        The robot runs one command at a time and keeps only its latest result, read by
        long-polling from a cursor. A result that another command's result replaced
        before it was read is looked up in the robot's history. A task cancelled while the
        command starts or runs cancels it on the robot, if it is still ours, and
        re-raises.

        Args:
            command: The command.
            title: The title the Kachaka app shows for it.
            cancel_all: Whether starting it cancels the command that runs.
            owner: The component that starts the command, which alone cancels it with
                ``cancel``.
            started: Called with the command id once the robot started the command.

        Returns:
            The robot's result: the refusal of StartCommand, or how the command ended.

        Raises:
            CommandPreempted: Another command started here replaced the command, or it
                ended without a result, replaced by a command of another client.
            ConnectionError: The robot did not answer the start, or stopped answering
                while the command ran, for ``lost_after`` seconds. The robot may still
                move.
            RuntimeError: The connection is not open, or the robot refused a call.
        """
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        last: pb2.GetLastCommandResultResponse = await self.call(
            "GetLastCommandResult", pb2.GetRequest()
        )
        request = pb2.StartCommandRequest(command=command, cancel_all=cancel_all, title=title)
        async with self._starting:
            answer = asyncio.ensure_future(self.call("StartCommand", request))
            try:
                response: pb2.StartCommandResponse = await asyncio.shield(answer)
            except asyncio.CancelledError:
                await self._cancel_started(answer)
                raise
            if not response.result.success:
                return response.result
            command_id = response.command_id
            if cancel_all:
                # The robot ended whatever ran, so no older command runs any more.
                self._owners.clear()
            self._owners[command_id] = owner
        if started is not None:
            started(command_id)
        try:
            result = await self._follow(command_id, last.metadata.cursor)
        except CommandPreempted:
            self._owners.pop(command_id, None)
            raise
        except asyncio.CancelledError:
            await self._cancel_own(command_id)
            raise
        if not result.success:
            # A StartCommand on its way may be what ended the command: wait for its answer.
            async with self._starting:
                replaced = command_id not in self._owners
            if replaced:
                raise CommandPreempted(
                    f"The command {command_id} was cancelled: another command replaced it"
                )
        self._owners.pop(command_id, None)
        return result

    async def _follow(self, command_id: str, cursor: int) -> pb2.Result:
        """Long-poll the command results from ``cursor`` until the command ``command_id``
        ends."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        failing_since: float | None = None
        missing_since: float | None = None
        while True:
            try:
                deadline = self.poll if missing_since is None else _MISSING_GRACE
                request = pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor))
                last = await self._rpc("GetLastCommandResult", request, deadline, long_poll=True)
                if last is not None:
                    cursor = last.metadata.cursor
                    if last.command_id == command_id:
                        result: pb2.Result = last.result
                        return result
                # No result of ours: another command's result, or none in time.
                state = await self._rpc("GetCommandState", pb2.GetRequest(), self.timeout)
                if state.state == pb2.COMMAND_STATE_RUNNING and state.command_id == command_id:
                    missing_since = None
                    failing_since = None
                    continue
                found = await self._from_history(command_id)
            except ConnectionError as exc:
                self._wake_watchdog()
                now = time.monotonic()
                if failing_since is None:
                    failing_since = now
                if self._lost or now - failing_since >= self.lost_after + _FOLLOW_SLACK:
                    raise ConnectionError(
                        f"Lost the Kachaka at {self.address} while the command ran; the "
                        "robot may still be moving"
                    ) from exc
                await asyncio.sleep(min(self.retry, _GRACE_CHECK))
                continue
            failing_since = None
            if found is not None:
                return found
            now = time.monotonic()
            if missing_since is None:
                missing_since = now
            elif now - missing_since >= _MISSING_GRACE:
                raise CommandPreempted(
                    f"The command {command_id} ended without a result: another command replaced it"
                )

    async def _from_history(self, command_id: str) -> pb2.Result | None:
        """The result of a command that ended, from the robot's history, or None."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        response: pb2.GetHistoryListResponse = await self._rpc(
            "GetHistoryList", pb2.GetRequest(), self.timeout
        )
        for history in response.histories:
            if history.id == command_id:
                return pb2.Result(success=history.success, error_code=history.error_code)
        return None

    async def _cancel_started(self, answer: asyncio.Future[pb2.StartCommandResponse]) -> None:
        """Cancel the command of a StartCommand the robot had not answered yet, for a
        task that was cancelled: its owner could not cancel it without its id."""
        try:
            response = await answer
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("A cancelled StartCommand was not answered: %s", exc)
            return
        if response.result.success:
            await self._cancel_own(response.command_id)

    async def _cancel_own(self, command_id: str) -> None:
        """Cancel the command ``command_id`` on the robot if it still runs, for a task
        that was cancelled.

        A command that could not be cancelled keeps its owner, who can cancel it later.
        """
        try:
            if await self._running_command() == command_id:
                await self._cancel_running()
                logger.info("Cancelled the command %s on the Kachaka", command_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Could not cancel the command %s on the Kachaka at %s: %s",
                command_id,
                self.address,
                exc,
            )
        else:
            self._owners.pop(command_id, None)

    async def _running_command(self) -> str | None:
        """The id of the command the robot runs, or None while it runs none."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        state: pb2.GetCommandStateResponse = await self.call("GetCommandState", pb2.GetRequest())
        if state.state != pb2.COMMAND_STATE_RUNNING:
            return None
        command_id: str = state.command_id
        return command_id

    async def _cancel_running(self) -> None:
        """Cancel whatever the robot runs. Only for a command known to be the caller's."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        await self.call("CancelCommand", pb2.EmptyRequest())

    async def _rpc(
        self, rpc: str, request: object, timeout: float, *, long_poll: bool = False
    ) -> Any:
        """Call an RPC with a deadline, turning gRPC errors into Python ones.

        With ``long_poll``, a deadline that passes returns None.
        """
        stub, channel, grpc = self._stub, self._channel, self._grpc
        if stub is None:
            raise RuntimeError("The Kachaka connection is not open")
        try:
            return await getattr(stub, rpc)(request, timeout=timeout)
        except asyncio.CancelledError:
            # A channel replaced by a new one cancels its calls. Only a task that is
            # itself being cancelled sees a cancellation.
            task = asyncio.current_task()
            if channel is not self._channel and task is not None and task.cancelling() == 0:
                raise ConnectionError(
                    f"The Kachaka at {self.address} did not answer {rpc}: the channel was replaced"
                ) from None
            raise
        except grpc.aio.AioRpcError as exc:
            code = exc.code()
            if code == grpc.StatusCode.DEADLINE_EXCEEDED and long_poll:
                return None
            if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                raise ConnectionError(
                    f"The Kachaka at {self.address} did not answer {rpc}: {code.name}"
                ) from exc
            raise RuntimeError(
                f"The Kachaka at {self.address} refused {rpc}: {code.name} {exc.details()}"
            ) from exc

    # -- The watchdog ----------------------------------------------------------------------

    @property
    def _lost(self) -> bool:
        return self._raised is not None and self._raised[0] is ComponentStatus.ERROR

    def _wake_watchdog(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _watch(self) -> None:
        """Check the robot every ``retry`` seconds.

        Nothing ends the loop but the cancel of ``close``: a check that fails in an
        unexpected way is logged, once for each run of failures, and the next check runs
        as usual.
        """
        assert self._wake is not None
        while True:
            interval = self.retry
            if self._failing_since is not None and not self._lost:
                interval = min(self.retry, _GRACE_CHECK)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), interval)
            self._wake.clear()
            try:
                await self._check()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._watch_failing:
                    logger.exception("The Kachaka watchdog could not check the robot")
                self._watch_failing = True
            else:
                self._watch_failing = False

    async def _check(self) -> None:
        """One check: ask the robot whether it is ready, and update the status."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        try:
            response: pb2.IsReadyResponse = await self._rpc(
                "IsReady", pb2.EmptyRequest(), self.timeout
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._not_answering(exc)
            return
        self._answered = True
        self._failing_since = None
        self._stuck = 0
        if not response.ready:
            self._up = False
            self._set_status(
                ComponentStatus.WARNING,
                f"The Kachaka at {self.address} is not ready to accept commands",
            )
            return
        if not self._up:
            self._up = True
            logger.info("Connected to the Kachaka at %s", self.address)
            await self._read_titles()
            self._pending = list(self._hooks)
        self._set_status(None)
        await self._run_hooks()
        await self._log_robot_errors()

    async def _not_answering(self, exc: Exception) -> None:
        """A check found the robot not answering: raise ERROR, at once or after the
        grace."""
        now = time.monotonic()
        if self._failing_since is None:
            self._failing_since = now
        if self._answered and not self._lost and now - self._failing_since < self.lost_after:
            logger.debug("The Kachaka at %s did not answer, waiting: %s", self.address, exc)
            return
        if not self._lost:
            if self._answered:
                logger.warning(
                    "Lost the Kachaka at %s, trying again every %g s: %s",
                    self.address,
                    self.retry,
                    exc,
                )
            else:
                logger.warning(
                    "The Kachaka at %s does not answer, trying again every %g s: %s",
                    self.address,
                    self.retry,
                    exc,
                )
        self._up = False
        self._set_status(ComponentStatus.ERROR, f"The Kachaka at {self.address} does not answer")
        if self._watchdog is not None:
            # Not on the first check, so a robot that is absent delays the start only once.
            await self._unstick()

    async def _unstick(self) -> None:
        """Replace a channel that keeps failing while the robot accepts connections."""
        if not await self._reachable():
            self._stuck = 0
            return
        self._stuck += 1
        if self._stuck < _STUCK_CHECKS:
            return
        self._stuck = 0
        logger.info(
            "The Kachaka at %s accepts connections but the channel fails. Opening a new one",
            self.address,
        )
        old = self._channel
        self._new_channel()
        if old is not None:
            await old.close()

    async def _reachable(self) -> bool:
        """Whether the robot accepts a TCP connection on the API's port."""
        try:
            async with asyncio.timeout(self.timeout):
                _, writer = await asyncio.open_connection(self.host, self.port)
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    def _new_channel(self) -> None:
        backoff = self._backoff_ms
        options = [
            ("grpc.initial_reconnect_backoff_ms", backoff),
            ("grpc.min_reconnect_backoff_ms", backoff),
            ("grpc.max_reconnect_backoff_ms", backoff),
            ("grpc.dns_min_time_between_resolutions_ms", backoff),
            ("grpc.keepalive_time_ms", _KEEPALIVE_MS),
            ("grpc.keepalive_timeout_ms", max(1000, int(self.timeout * 1000))),
            # A channel of its own connections, so a new channel starts afresh rather than
            # share the connection of the channel it replaces.
            ("grpc.use_local_subchannel_pool", 1),
        ]
        self._channel = self._grpc.aio.insecure_channel(self.address, options=options)
        self._stub = self._pb2_grpc.KachakaApiStub(self._channel)

    def _set_status(self, status: ComponentStatus | None, message: str = "") -> None:
        """Raise ``status`` with ``message``, or clear it with None, when it changes."""
        raised = None if status is None else (status, message)
        if raised == self._raised:
            return
        self._raised = raised
        if raised is None:
            self.clear_status()
        else:
            self.raise_status(*raised)

    async def _read_titles(self) -> None:
        """Read the robot's table of error codes, for ``describe``."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        try:
            response: pb2.GetRobotErrorCodeJsonResponse = await self._rpc(
                "GetRobotErrorCodeJson", pb2.EmptyRequest(), self.timeout
            )
            titles: dict[int, str] = {}
            for item in json.loads(response.json):
                title = item.get("title_en") or item.get("title") or ""
                if title:
                    titles[int(item["code"])] = str(title)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Could not read the error codes of the Kachaka: %s", exc)
            return
        self._titles = titles

    async def _run_hooks(self) -> None:
        """Run the hooks that have not run since the robot came up."""
        pending, self._pending = self._pending, []
        for hook in pending:
            try:
                await hook()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if id(hook) not in self._failing_hooks:
                    logger.warning(
                        "A setting for the Kachaka at %s could not be applied, trying again: %s",
                        self.address,
                        exc,
                    )
                self._failing_hooks.add(id(hook))
                self._pending.append(hook)
            else:
                self._failing_hooks.discard(id(hook))

    async def _log_robot_errors(self) -> None:
        """Log the error codes the robot reports, once each time one appears."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        try:
            response: pb2.GetErrorResponse = await self._rpc(
                "GetError", pb2.GetRequest(), self.timeout
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Could not read the errors of the Kachaka: %s", exc)
            return
        codes = set(response.error_codes)
        for code in sorted(codes - self._robot_errors):
            logger.warning("The Kachaka at %s reports %s", self.address, self.describe(code))
        self._robot_errors = codes
