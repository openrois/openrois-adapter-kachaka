"""Move for the Kachaka: straight lines and turns in place.

``start`` moves the robot by ``line``, two integers: a distance in mm and an orientation
in degrees. The robot first turns in place by the orientation, then drives straight by
the distance. A positive orientation turns counter-clockwise, the sense in which theta
of ``robot_position`` grows, and a negative distance drives backwards. A part that is 0
is skipped, so ``[0, 90]`` only turns and ``[500, 0]`` only drives. A start without a
line, or with the line ``[0, 0]``, ends with ERROR.

The turn is a RotateInPlaceCommand of the robot and the straight line a
MoveForwardCommand. A turn of more than 90 degrees is sent as equal turns of at most 90
degrees each, one after the other. So the robot turns in the sense that was asked, also
beyond half a circle, where the other way round is shorter, and the robot's pose tells
how far a cancelled turn went.

``time``, in seconds, sets the speed of the straight line to the distance divided by the
time, kept between 0.05 and 0.3 m/s. 0, the default, drives at the robot's own speed.
The robot has no speed setting for a turn, and it speeds up and slows down on its own,
so the motion lasts about ``time`` at best, and longer when it turns first.

``set_parameter`` refuses a line that is not two integers, a distance beyond 10000 mm,
an orientation beyond 3600 degrees and a negative time. This component drives no curves:
``set_parameter`` refuses a ``curve`` that is not empty.

The robot runs one command at a time, for every client and for the Kachaka app. ``stop``
cancels only the motion of this component, and so does a start the engine aborts, when
it stops or a client releases the component. A start while another command runs replaces
that command, and a motion that another command replaced ends with ERROR.

The Kachaka has no pause. ``suspend`` cancels the running turn or line and holds the
start, and ``resume`` moves on by what remains of the motion: the robot's pose before the
part and after the cancel tells how far it turned or drove. What remains of a part is
never more than the part, and a remainder of at most 2 degrees or 20 mm counts as done.
The speed of the line stays the one ``time`` set. A ``stop`` while suspended aborts the
start.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from openrois.components.core import CommandFailed, Component, command, component
from openrois.interfaces.components import MOVE_PROFILE
from openrois.interfaces.service import CompletedStatus

from openrois.components.kachaka.connection import CommandPreempted, KachakaConnection

if TYPE_CHECKING:
    from kachaka_api.generated import kachaka_api_pb2 as pb2

__all__ = [
    "LINE_TOLERANCE",
    "MAX_DISTANCE",
    "MAX_ORIENTATION",
    "MAX_SPEED",
    "MAX_TURN",
    "MIN_SPEED",
    "TURN_TOLERANCE",
    "KachakaMove",
    "Pose",
    "advanced",
    "line_speed",
    "remainder",
    "turned",
]

logger = logging.getLogger(__name__)

#: The longest straight line ``line`` may ask for, in mm, forwards or backwards.
MAX_DISTANCE = 10_000

#: The largest orientation ``line`` may ask for, in degrees, in either sense: ten turns.
MAX_ORIENTATION = 3600

#: The slowest and the fastest speed ``time`` sets for a straight line, in m/s.
MIN_SPEED = 0.05
MAX_SPEED = 0.3

#: The largest turn sent to the robot as one command, in degrees.
MAX_TURN = 90.0

#: What may remain of a turn, in degrees, and of a straight line, in meters, for the part
#: to count as done after a suspension.
TURN_TOLERANCE = 2.0
LINE_TOLERANCE = 0.02

# A turn the pose shows as this close to a full circle, in radians, went backwards a
# little rather than almost all the way round.
_BACK_MARGIN = math.pi / 2

#: A pose of the robot on its map: x and y in meters, theta in radians.
type Pose = tuple[float, float, float]


def line_speed(distance: float, seconds: int) -> float:
    """The speed of a straight line of ``distance`` meters that ``time`` asks for, in m/s.

    0.0, which the robot takes as its own speed, when ``seconds`` is 0. Otherwise the
    distance divided by the time, kept between ``MIN_SPEED`` and ``MAX_SPEED``.
    """
    if seconds <= 0 or distance == 0:
        return 0.0
    return min(MAX_SPEED, max(MIN_SPEED, abs(distance) / seconds))


def turned(before: float, after: float, sense: float) -> float:
    """How far the robot turned from the heading ``before`` to ``after``, in radians, on
    a turn in the sense of ``sense``: positive is counter-clockwise.

    The headings are angles in radians, wrapped or not. The result has the sign of
    ``sense`` and is up to three quarters of a circle, or has the other sign, up to a
    quarter of a circle, for a robot that turned back a little.
    """
    sign = math.copysign(1.0, sense)
    way = (after - before) * sign % math.tau
    return sign * (way - math.tau if way > math.tau - _BACK_MARGIN else way)


def advanced(before: Pose, after: Pose) -> float:
    """How far the robot drove from ``before`` to ``after`` along its heading at
    ``before``, in meters: negative for a robot that drove backwards."""
    x, y, theta = before
    return (after[0] - x) * math.cos(theta) + (after[1] - y) * math.sin(theta)


def remainder(asked: float, done: float, tolerance: float) -> float:
    """What remains of a part of ``asked`` once the robot did ``done`` of it.

    Both are signed alike: degrees of a turn, or meters of a straight line. 0.0 when at
    most ``tolerance`` remains or the robot went past the end. Never more than ``asked``,
    also for a robot that went the other way.
    """
    left = asked - done
    if abs(left) <= tolerance or (left > 0) != (asked > 0):
        return 0.0
    return asked if abs(left) > abs(asked) else left


def _check_line(line: Sequence[int]) -> None:
    """Refuse a line that is not a distance and an orientation within their limits."""
    if len(line) != 2:
        raise ValueError(
            f"line is two integers, a distance in mm and an orientation in degrees, not {line}"
        )
    distance, orientation = line
    if abs(distance) > MAX_DISTANCE:
        raise ValueError(f"The distance of line is at most {MAX_DISTANCE} mm, not {distance}")
    if abs(orientation) > MAX_ORIENTATION:
        raise ValueError(
            f"The orientation of line is at most {MAX_ORIENTATION} degrees, not {orientation}"
        )


def _rotate(degrees: float) -> pb2.Command:
    """A turn in place, counter-clockwise for a positive angle."""
    from kachaka_api.generated import kachaka_api_pb2 as pb2

    return pb2.Command(
        rotate_in_place_command=pb2.RotateInPlaceCommand(angle_radian=math.radians(degrees))
    )


def _forward(meters: float, speed: float) -> pb2.Command:
    """A straight line, backwards for a negative distance. The speed 0.0 leaves the speed
    to the robot."""
    from kachaka_api.generated import kachaka_api_pb2 as pb2

    return pb2.Command(
        move_forward_command=pb2.MoveForwardCommand(distance_meter=meters, speed=speed)
    )


@component(MOVE_PROFILE)
class KachakaMove(Component):
    """Move of one Kachaka, in straight lines and turns in place.

    Args:
        connection: The connection to the robot, shared with its other components.
    """

    def __init__(self, connection: KachakaConnection) -> None:
        self._connection = self.uses(connection)
        # Set while the component is not suspended.
        self._resumed = asyncio.Event()
        self._resumed.set()
        # How many suspends and stops ran, so a start knows why a part ended.
        self._suspends = 0
        self._stops = 0
        # The robot's command of the running start.
        self._command_id: str | None = None
        self._cancels: set[asyncio.Task[None]] = set()

    # -- Commands --------------------------------------------------------------------------

    @command("set_parameter")
    async def check(self, values: Mapping[str, Any]) -> None:
        """Refuse a line that is not a distance and an orientation within their limits,
        a curve and a negative time.

        Raises:
            ValueError: A value is refused.
        """
        if "time" in values and values["time"] < 0:
            raise ValueError(f"time is in seconds, 0 or more, not {values['time']}")
        if values.get("curve"):
            raise ValueError(
                "The Kachaka Move component drives straight lines and turns in place, no "
                f"curves: curve stays empty, not {values['curve']}"
            )
        if "line" in values:
            _check_line(values["line"])

    @command("start")
    async def start(self) -> None:
        """Turn in place by the orientation of line, then drive straight by its distance.

        Raises:
            CommandFailed: ERROR when line is not set or is [0, 0], the robot refused or
                failed a part, another command replaced it or the robot stopped
                answering; ABORT when a stop ended it.
        """
        line: list[int] = list(self.parameters.get("line") or [])
        if not any(line):
            raise CommandFailed(CompletedStatus.ERROR, "Set line first")
        distance, orientation = line
        seconds: int = self.parameters.get("time", 0)
        self._resumed.set()
        stops = self._stops
        await self._turn(float(orientation), stops)
        await self._straight(distance / 1000, line_speed(distance / 1000, seconds), stops)
        logger.info("The Kachaka turned %d degrees and drove %d mm", orientation, distance)

    @command("stop")
    async def stop(self) -> None:
        """Cancel the motion of this component on the robot, if the robot still runs it.

        A command another component started, another client or the Kachaka app, is left
        running.
        """
        self._stops += 1
        self._resumed.set()
        try:
            await self._connection.cancel(self)
        except (ConnectionError, RuntimeError) as exc:
            raise CommandFailed(
                CompletedStatus.ERROR, f"Could not cancel the motion of the Kachaka: {exc}"
            ) from exc

    @command("suspend")
    async def suspend(self) -> None:
        """Cancel the turn or line of the running start on the robot, and hold the start."""
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
                CompletedStatus.ERROR, f"Could not halt the motion of the Kachaka: {exc}"
            ) from exc

    @command("resume")
    async def resume(self) -> None:
        """Let the start move again, by what remains of the motion."""
        self._resumed.set()

    # -- Moving ----------------------------------------------------------------------------

    async def _turn(self, degrees: float, stops: int) -> None:
        """Turn in place by ``degrees``, in equal turns of at most ``MAX_TURN``."""
        remaining = degrees
        while remaining:
            step = remaining / math.ceil(abs(remaining) / MAX_TURN)
            halted = await self._run("turn", f"turn {step:g} deg", _rotate(step), stops)
            if halted is None:
                remaining -= step
                continue
            before, after = halted
            done = math.degrees(turned(before[2], after[2], step))
            remaining -= step - remainder(step, done, TURN_TOLERANCE)

    async def _straight(self, meters: float, speed: float, stops: int) -> None:
        """Drive straight by ``meters``, backwards for a negative distance."""
        remaining = meters
        while remaining:
            title = f"line {remaining * 1000:.0f} mm"
            halted = await self._run("line", title, _forward(remaining, speed), stops)
            if halted is None:
                return
            remaining = remainder(remaining, advanced(*halted), LINE_TOLERANCE)

    async def _run(
        self, part: str, title: str, move: pb2.Command, stops: int
    ) -> tuple[Pose, Pose] | None:
        """Run one command of the motion on the robot, once the component is not
        suspended.

        Returns:
            None once the robot completed the command. For a command a suspend
            cancelled, the robot's pose before the command and after the cancel.
        """
        while True:
            await self._wait_resumed(stops)
            suspends = self._suspends
            before = await self._pose()
            if not self._resumed.is_set() or self._stops != stops:
                continue  # A suspend or a stop came meanwhile.
            try:
                result = await self._connection.run_command(
                    move, title=f"OpenRoIS: {title}", owner=self, started=self._started
                )
            except (ConnectionError, CommandPreempted, RuntimeError) as exc:
                raise CommandFailed(CompletedStatus.ERROR, f"The {part} failed: {exc}") from exc
            finally:
                self._command_id = None
            if result.success:
                return None
            if self._stops != stops:
                raise CommandFailed(CompletedStatus.ABORT, f"Stopped the {part}")
            if self._suspends != suspends:
                return before, await self._pose()
            raise CommandFailed(
                CompletedStatus.ERROR,
                f"The {part} failed: {self._connection.describe(result.error_code)}",
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
            logger.warning("Could not halt the motion of the Kachaka: %s", exc)

    async def _wait_resumed(self, stops: int) -> None:
        """Wait while the component is suspended."""
        await self._resumed.wait()
        if self._stops != stops:
            raise CommandFailed(CompletedStatus.ABORT, "Stopped")

    # -- The robot -------------------------------------------------------------------------

    async def _pose(self) -> Pose:
        """The robot's pose on its map.

        Raises:
            CommandFailed: ERROR when the robot did not answer.
        """
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        try:
            response: pb2.GetRobotPoseResponse = await self._connection.call(
                "GetRobotPose", pb2.GetRequest()
            )
        except (ConnectionError, RuntimeError) as exc:
            raise CommandFailed(
                CompletedStatus.ERROR, f"Could not read the pose of the Kachaka: {exc}"
            ) from exc
        pose = response.pose
        return pose.x, pose.y, pose.theta
