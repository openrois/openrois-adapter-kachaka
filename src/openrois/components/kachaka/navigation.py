"""Navigation for the Kachaka.

``start`` drives the robot to each of ``target_positions`` in turn and emits
``reached_target`` at each one, with ``is_final_target`` true at the last. A target is
one of:

- a location of the robot, by its name or its id, as the Kachaka app shows them. Names
  are matched first, then ids. The charger is the location ``home``.
- a pose on the robot's map, ``x,y,theta``: x and y in meters, theta in radians.

``set_parameter`` refuses a target the robot does not know, a negative ``time_limit`` and
a ``routing_policy`` other than ``time`` or ``distance``. The Kachaka plans its own
routes, so ``routing_policy`` has no effect. ``time_limit``, in seconds, ends a start
that has not reached its last target in time with TIMEOUT and cancels the drive; 0, the
default, sets no limit. A suspension does not count towards it.

The robot runs one command at a time, for every client and for the Kachaka app. ``stop``
cancels only the drive of this component, and so does a start the engine aborts, when it
stops or a client releases the component. A command another component started, another
client or the Kachaka app, is left running. A start while another command runs replaces
that command, and a drive that another command replaced ends with ERROR.

The Kachaka has no pause. ``suspend`` cancels the drive and holds the start, and
``resume`` starts the drive to the same target again. A ``stop`` while suspended aborts
the start.

``auto_homing``, if given, is set on the robot each time it comes up, after a start of
the adapter or a restart of the robot: whether the robot returns to its charger on its
own after a while without commands.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from openrois.components.core import CommandFailed, Component, command, component, event
from openrois.interfaces.components import NAVIGATION_PROFILE
from openrois.interfaces.service import CompletedStatus

from openrois.components.kachaka.connection import CommandPreempted, KachakaConnection

if TYPE_CHECKING:
    from kachaka_api.generated import kachaka_api_pb2 as pb2

__all__ = ["ROUTING_POLICIES", "KachakaNavigation", "parse_pose"]

logger = logging.getLogger(__name__)

#: The values of routing_policy the profile names. The Kachaka plans its own routes.
ROUTING_POLICIES = frozenset({"time", "distance"})


def parse_pose(text: str) -> tuple[float, float, float] | None:
    """The pose ``x,y,theta`` a target names, or None for a target that is not a pose.

    x and y are in meters and theta in radians, on the robot's map. Spaces around the
    numbers are allowed.
    """
    parts = text.split(",")
    if len(parts) != 3:
        return None
    try:
        x, y, theta = (float(part) for part in parts)
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in (x, y, theta)):
        return None
    return x, y, theta


def _command(text: str, locations: Sequence[pb2.Location]) -> pb2.Command | None:
    """The drive to a target: a location by its name, then by its id, then a pose."""
    from kachaka_api.generated import kachaka_api_pb2 as pb2

    name = text.strip()
    found = next((location for location in locations if location.name == name), None)
    if found is None:
        found = next((location for location in locations if location.id == name), None)
    if found is not None:
        return pb2.Command(
            move_to_location_command=pb2.MoveToLocationCommand(target_location_id=found.id)
        )
    pose = parse_pose(text)
    if pose is None:
        return None
    x, y, theta = pose
    return pb2.Command(move_to_pose_command=pb2.MoveToPoseCommand(x=x, y=y, yaw=theta))


@component(NAVIGATION_PROFILE)
class KachakaNavigation(Component):
    """Navigation of one Kachaka.

    Args:
        connection: The connection to the robot, shared with its other components.
        auto_homing: Whether the robot returns to its charger on its own, set each time
            the robot comes up. None leaves the robot's setting as it is.
    """

    def __init__(self, connection: KachakaConnection, *, auto_homing: bool | None = None) -> None:
        self._connection = self.uses(connection)
        self._auto_homing = auto_homing
        if auto_homing is not None:
            connection.on_connect(self._apply_auto_homing)
        # Set while the component is not suspended.
        self._resumed = asyncio.Event()
        self._resumed.set()
        # How many suspends and stops ran, so a start knows why its drive ended.
        self._suspends = 0
        self._stops = 0
        # The robot's command of the running start.
        self._command_id: str | None = None
        # The time_limit of the running start.
        self._limit: asyncio.Timeout | None = None
        self._cancels: set[asyncio.Task[None]] = set()

    # -- Commands --------------------------------------------------------------------------

    @command("set_parameter")
    async def check(self, values: Mapping[str, Any]) -> None:
        """Refuse targets the robot does not know, a negative time_limit and an unknown
        routing_policy.

        Raises:
            ValueError: A value is refused.
        """
        if "time_limit" in values and values["time_limit"] < 0:
            raise ValueError(f"time_limit is in seconds, 0 or more, not {values['time_limit']}")
        if "routing_policy" in values and values["routing_policy"] not in ROUTING_POLICIES:
            raise ValueError(
                f"routing_policy is 'time' or 'distance', not {values['routing_policy']!r}"
            )
        if "target_positions" in values:
            await self._check_targets(values["target_positions"])

    @command("start")
    async def start(self) -> None:
        """Drive to each target in turn, until the last.

        Raises:
            CommandFailed: ERROR when there are no targets, a target is unknown, a drive
                failed, another command replaced it or the robot stopped answering;
                TIMEOUT when time_limit passed; ABORT when a stop ended it.
        """
        targets: list[str] = list(self.parameters.get("target_positions") or [])
        if not targets:
            raise CommandFailed(CompletedStatus.ERROR, "Set target_positions first")
        self._resumed.set()
        stops = self._stops
        time_limit: int = self.parameters.get("time_limit", 0)
        if time_limit <= 0:
            await self._drive(targets, stops)
            return
        try:
            async with asyncio.timeout(time_limit) as limit:
                self._limit = limit
                await self._drive(targets, stops)
        except TimeoutError:
            if not limit.expired():
                raise
            raise CommandFailed(
                CompletedStatus.TIMEOUT,
                f"The Kachaka did not reach its last target within {time_limit} s",
            ) from None
        finally:
            self._limit = None

    @command("stop")
    async def stop(self) -> None:
        """Cancel the drive of this component on the robot, if the robot still runs it.

        A command another component started, another client or the Kachaka app, is left
        running.
        """
        self._stops += 1
        self._resumed.set()
        try:
            await self._connection.cancel(self)
        except (ConnectionError, RuntimeError) as exc:
            raise CommandFailed(
                CompletedStatus.ERROR, f"Could not cancel the drive of the Kachaka: {exc}"
            ) from exc

    @command("suspend")
    async def suspend(self) -> None:
        """Cancel the drive of the running start on the robot, and hold the start."""
        self._resumed.clear()
        self._suspends += 1
        if self._command_id is None:
            return
        try:
            await self._connection.cancel(self)
        except (ConnectionError, RuntimeError) as exc:
            # The engine lifts the hold of a suspend that fails.
            self._resumed.set()
            raise CommandFailed(
                CompletedStatus.ERROR, f"Could not halt the drive of the Kachaka: {exc}"
            ) from exc

    @command("resume")
    async def resume(self) -> None:
        """Let the start drive again, to the target it was driving to."""
        self._resumed.set()

    @event("reached_target")
    async def reached_target(self) -> None:
        """Nothing to start: ``start`` emits the event at each target."""

    # -- Driving ---------------------------------------------------------------------------

    async def _drive(self, targets: Sequence[str], stops: int) -> None:
        """Resolve every target first, then drive to each one."""
        locations = await self._locations_or_fail()
        commands: list[pb2.Command] = []
        for text in targets:
            drive = _command(text, locations)
            if drive is None:
                raise CommandFailed(
                    CompletedStatus.ERROR,
                    f"The Kachaka knows no location {text!r}, and it is not a pose x,y,theta",
                )
            commands.append(drive)
        last = len(targets) - 1
        for index, (text, drive) in enumerate(zip(targets, commands, strict=True)):
            await self._drive_to(text, drive, stops)
            logger.info("The Kachaka reached %s", text)
            self.emit("reached_target", target=text, is_final_target=index == last)

    async def _drive_to(self, text: str, drive: pb2.Command, stops: int) -> None:
        """Drive to one target, again after each suspension, until the robot is there."""
        while True:
            await self._wait_resumed(stops)
            suspends = self._suspends
            try:
                result = await self._connection.run_command(
                    drive, title=f"OpenRoIS: {text}", owner=self, started=self._started
                )
            except (ConnectionError, CommandPreempted, RuntimeError) as exc:
                raise CommandFailed(
                    CompletedStatus.ERROR, f"The drive to {text} failed: {exc}"
                ) from exc
            finally:
                self._command_id = None
            if result.success:
                return
            if self._stops != stops:
                raise CommandFailed(CompletedStatus.ABORT, f"Stopped the drive to {text}")
            if self._suspends != suspends:
                continue  # The suspend cancelled the drive; it starts again on resume.
            raise CommandFailed(
                CompletedStatus.ERROR,
                f"The drive to {text} failed: {self._connection.describe(result.error_code)}",
            )

    def _started(self, command_id: str) -> None:
        """Record the robot's command, and cancel it if a suspend came while it started."""
        self._command_id = command_id
        if not self._resumed.is_set():
            task = asyncio.get_running_loop().create_task(self._cancel_own())
            self._cancels.add(task)
            task.add_done_callback(self._cancels.discard)

    async def _cancel_own(self) -> None:
        try:
            await self._connection.cancel(self)
        except (ConnectionError, RuntimeError) as exc:
            logger.warning("Could not halt the drive of the Kachaka: %s", exc)

    async def _wait_resumed(self, stops: int) -> None:
        """Wait while the component is suspended. The time_limit pauses meanwhile."""
        if not self._resumed.is_set():
            loop = asyncio.get_running_loop()
            limit, remaining = self._limit, None
            if limit is not None and not limit.expired() and (when := limit.when()) is not None:
                remaining = when - loop.time()
                limit.reschedule(None)
            try:
                await self._resumed.wait()
            finally:
                if limit is not None and remaining is not None and not limit.expired():
                    limit.reschedule(loop.time() + remaining)
        if self._stops != stops:
            raise CommandFailed(CompletedStatus.ABORT, "Stopped")

    # -- The robot -------------------------------------------------------------------------

    async def _locations(self) -> list[pb2.Location]:
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        response: pb2.GetLocationsResponse = await self._connection.call(
            "GetLocations", pb2.GetRequest()
        )
        return list(response.locations)

    async def _locations_or_fail(self) -> list[pb2.Location]:
        try:
            return await self._locations()
        except (ConnectionError, RuntimeError) as exc:
            raise CommandFailed(
                CompletedStatus.ERROR, f"Could not read the locations of the Kachaka: {exc}"
            ) from exc

    async def _check_targets(self, targets: Sequence[str]) -> None:
        """Refuse an empty list, an empty target, and a target the robot does not know.

        A robot that does not answer leaves the targets unchecked, and start checks
        them again.
        """
        if not targets:
            raise ValueError("target_positions is empty")
        if any(not text.strip() for text in targets):
            raise ValueError("target_positions has an empty target")
        if all(parse_pose(text) is not None for text in targets):
            return
        try:
            locations = await self._locations()
        except (ConnectionError, RuntimeError) as exc:
            logger.warning("Could not check target_positions on the Kachaka: %s", exc)
            return
        unknown = [text for text in targets if _command(text, locations) is None]
        if unknown:
            known = ", ".join(sorted(location.name or location.id for location in locations))
            raise ValueError(
                f"The Kachaka knows no location {', '.join(map(repr, unknown))}, and "
                f"{'it is' if len(unknown) == 1 else 'they are'} not a pose x,y,theta. "
                f"Its locations: {known}"
            )

    async def _apply_auto_homing(self) -> None:
        """Set auto homing on the robot, once each time it comes up."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        enable = bool(self._auto_homing)
        response: pb2.SetAutoHomingEnabledResponse = await self._connection.call(
            "SetAutoHomingEnabled", pb2.SetAutoHomingEnabledRequest(enable=enable)
        )
        if not response.result.success:
            raise RuntimeError(
                "The Kachaka refused to set auto homing: "
                + self._connection.describe(response.result.error_code)
            )
        logger.info("Set auto homing of the Kachaka to %s", "on" if enable else "off")
