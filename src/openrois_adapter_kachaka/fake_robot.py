"""A fake Kachaka that serves the Kachaka gRPC API, for tests and for trying the adapter.

``FakeKachaka`` answers the RPCs the adapter uses the way the robot does: data that
carries a cursor is read by long-polling, a command runs in the robot's one command slot
and ends with a result, and only the latest result is kept. The robot drives in a
straight line at 0.5 m/s, moving in 0.1 s ticks; ``speed_scale`` makes it faster. A
MoveForwardCommand drives along the robot's heading, backwards for a negative distance,
at its own speed when it gives one, and a RotateInPlaceCommand turns the robot by its
angle at 1 rad/s. It has three locations, ``home`` (the charger), ``kitchen`` and
``meeting_room``, and a small map whose origin is turned.

Its object detector publishes what it sees every ``detection_period`` seconds, an empty
room at first. Until the first detection, a request for the detection waits, even with
cursor 0.

A test controls it: ``stop()`` and ``start()`` take the robot off the network and back on
the same port, ``set_ready(False)`` makes it report not ready, ``fail_next()`` makes the
next command fail, ``preempt()`` starts a command of another client, ``hold_starts()``
keeps the answers of StartCommand back until ``release_starts()``, and ``received`` logs
every request. ``result_wait`` makes the robot end a wait for the command result with
CANCELLED after that many seconds, as a real robot does, and ``refuse_results()`` makes it
refuse the request. A command that a new one replaces ends without a result, as on a real
robot, unless ``replaced_result`` is set. ``see()`` sets the persons and the other objects
the detector sees, ``set_detecting(False)`` stops the detector, ``refuse_detection()``
makes the robot refuse the request for the detection, and ``clock_offset`` puts the
robot's clock ahead.

Run it on its own, for the adapter with ``adapter.fake.yaml``::

    python -m openrois_adapter_kachaka.fake_robot --port 26400

It imports ``grpc`` and the Kachaka API stubs, so it needs the ``robot`` extra.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import itertools
import json
import logging
import math
import socket
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Self, override

import grpc
from kachaka_api.generated import kachaka_api_pb2 as pb2
from kachaka_api.generated import kachaka_api_pb2_grpc as pb2_grpc

__all__ = [
    "BUSY",
    "CANCELLED",
    "ERROR_CODES",
    "INVALID_COMMAND",
    "LOCATIONS",
    "MAP_ID",
    "NOT_READY",
    "UNKNOWN_LOCATION",
    "UNREACHABLE",
    "FakeKachaka",
    "Received",
    "main",
]

logger = logging.getLogger(__name__)

DEFAULT_PORT = 26400

# Speed in metres per second, and the period in seconds of each step of a drive.
SPEED = 0.5
TICK = 0.1

# Speed of a turn in place, in radians per second.
TURN_SPEED = 1.0
# The period in seconds at which the object detector publishes what it sees.
DETECTION_PERIOD = 0.2

# The error codes of the fake, with the fields of the robot's GetRobotErrorCodeJson. A
# real robot has its own table; the codes here only have to be told apart.
CANCELLED = 10001
BUSY = 10002
NOT_READY = 10003
UNKNOWN_LOCATION = 10004
INVALID_COMMAND = 10005
UNREACHABLE = 10006

ERROR_CODES: dict[int, str] = {
    CANCELLED: "The command was cancelled",
    BUSY: "Another command is running",
    NOT_READY: "The robot is not ready",
    UNKNOWN_LOCATION: "The location does not exist",
    INVALID_COMMAND: "The command is not valid",
    UNREACHABLE: "The robot could not reach the destination",
}

# The locations: id, name, (x, y, theta) on the map, and type.
LOCATIONS: tuple[tuple[str, str, tuple[float, float, float], pb2.LocationType], ...] = (
    ("home", "Charger", (0.0, 0.0, 0.0), pb2.LOCATION_TYPE_CHARGER),
    ("kitchen", "Kitchen", (2.0, 1.0, math.pi / 2), pb2.LOCATION_TYPE_UNSPECIFIED),
    ("meeting_room", "Meeting room", (-1.0, 2.0, math.pi), pb2.LOCATION_TYPE_UNSPECIFIED),
)
DEFAULT_LOCATION_ID = "kitchen"

# A 16 x 12 grayscale PNG: a room with a wall in the middle, at 0.25 m per pixel. Its
# origin, the pose of the lower-left pixel, is turned, as on a robot that mapped a
# room at an angle.
MAP_ID = "fake_map"
MAP_NAME = "Fake floor"
MAP_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAAAAABOjGJdAAAAG0lEQVR42mMwQAMMBh9QAB4BA1oIfCBBAA0A"
    "AJV2iUGNp2PgAAAAAElFTkSuQmCC"
)
MAP_WIDTH = 16
MAP_HEIGHT = 12
MAP_RESOLUTION = 0.25
MAP_ORIGIN = (-1.5, -0.5, 0.05)


@dataclass(frozen=True)
class Received:
    """A request the fake received: the RPC's name and its request message."""

    method: str
    request: Any


@dataclass
class _Running:
    """The command in the robot's command slot."""

    command_id: str
    command: pb2.Command
    task: asyncio.Task[None] | None = None


class _Topic:
    """Data published under a cursor, as the cursor long-poll reads it.

    A request with cursor 0 gets the latest data at once. A request with an older cursor
    gets the latest data at once too, so data published in between is skipped. A request
    with the latest cursor waits until new data is published.
    """

    def __init__(self, cursors: Iterator[int]) -> None:
        self._cursors = cursors
        self.cursor = next(cursors)
        self._changed = asyncio.Event()

    def publish(self) -> None:
        self.cursor = next(self._cursors)
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def wait(self, cursor: int) -> None:
        while cursor != 0 and cursor == self.cursor:
            await self._changed.wait()


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port: int = sock.getsockname()[1]
        return port


def _pose(x: float, y: float, theta: float) -> pb2.Pose:
    return pb2.Pose(x=x, y=y, theta=theta)


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


class FakeKachaka(pb2_grpc.KachakaApiServicer):
    """A fake Kachaka robot on ``host:port``, serving the Kachaka gRPC API.

    The port is chosen when the fake is made, so ``address`` is known before the server
    starts, and ``start()`` after ``stop()`` serves on the same port again. The robot
    keeps its pose and its running command while it is stopped, as a robot that is only
    off the network does.

    Args:
        host: The address to serve on.
        port: The port; 0 picks a free one.
        speed_scale: How many times faster than 0.5 m/s the robot drives.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0, speed_scale: float = 1.0):
        self.host = host
        self.port = port or _free_port(host)
        self.speed_scale = speed_scale
        self.received: list[Received] = []
        self.ready = True
        self.auto_homing = True
        self.errors: list[int] = []
        self.x, self.y, self.theta = 0.0, 0.0, 0.0
        self.history: list[pb2.History] = []
        self.objects: list[pb2.ObjectDetection] = []
        self.detection_period = DETECTION_PERIOD
        self.detecting = True
        self.detection_refused = False
        # How long a request for the command result may wait before the robot ends the
        # wait with CANCELLED, in seconds, as a real robot does after some seconds. None
        # lets it wait.
        self.result_wait: float | None = None
        self.result_refused = False
        self.replaced_result = False
        # How far the robot's clock is ahead of this machine's, in seconds.
        self.clock_offset = 0.0

        self._server: Any = None
        self._running: _Running | None = None
        self._last: pb2.GetLastCommandResultResponse | None = None
        self._fail_next: int | None = None
        self._refuse_next: int | None = None
        self._answer_starts = asyncio.Event()
        self._answer_starts.set()
        self._detector: asyncio.Task[None] | None = None
        self._detection: pb2.GetObjectDetectionResponse | None = None

        cursors = itertools.count(1_000_000)
        self._pose_topic = _Topic(cursors)
        self._state_topic = _Topic(cursors)
        self._result_topic = _Topic(cursors)
        self._history_topic = _Topic(cursors)
        self._error_topic = _Topic(cursors)
        self._settings_topic = _Topic(cursors)
        self._static_topic = _Topic(cursors)
        self._detection_topic = _Topic(cursors)

    # The controls of a test.

    @property
    def address(self) -> str:
        """``host:port``, the address the adapter connects to."""
        return f"{self.host}:{self.port}"

    @property
    def serving(self) -> bool:
        return self._server is not None

    @property
    def pose(self) -> tuple[float, float, float]:
        """The robot's pose on the map: x and y in metres, theta in radians."""
        return (self.x, self.y, self.theta)

    @property
    def running_command_id(self) -> str | None:
        """The id of the command in the command slot, or None."""
        return self._running.command_id if self._running else None

    async def start(self) -> None:
        """Serve the API on ``address``. Does nothing while serving."""
        if self._server is not None:
            return
        # Without port reuse, a second server on the same port fails rather than shares it.
        server = grpc.aio.server(options=[("grpc.so_reuseport", 0)])
        pb2_grpc.add_KachakaApiServicer_to_server(self, server)
        server.add_insecure_port(self.address)
        await server.start()
        self._server = server
        if self._detector is None:
            self._detector = asyncio.get_running_loop().create_task(self._detect())
        logger.info("The fake Kachaka serves on %s", self.address)

    async def stop(self) -> None:
        """Take the robot off the network: requests fail, the robot keeps its state."""
        server, self._server = self._server, None
        if server is not None:
            await server.stop(None)

    async def close(self) -> None:
        """Stop serving and end the running command without a result."""
        await self.stop()
        running, self._running = self._running, None
        if running is not None and running.task is not None:
            running.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running.task
        detector, self._detector = self._detector, None
        if detector is not None:
            detector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await detector

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def set_ready(self, ready: bool) -> None:
        """What IsReady answers. A robot that is not ready refuses commands."""
        self.ready = ready

    def set_errors(self, codes: Sequence[int]) -> None:
        """The error codes GetError reports."""
        self.errors = list(codes)
        self._error_topic.publish()

    def fail_next(self, code: int = UNREACHABLE, *, on_start: bool = False) -> None:
        """Make the next command fail with ``code``.

        By default the command starts and then ends with a failed result. With
        ``on_start``, StartCommand refuses it.
        """
        if on_start:
            self._refuse_next = code
        else:
            self._fail_next = code

    def preempt(self, command: pb2.Command | None = None, *, finish: bool = False) -> str:
        """Start a command of another client, which cancels the running one.

        Args:
            command: The command; a drive to the charger by default.
            finish: End the command at once, so that its result replaces the cancelled
                command's result before a long-poll reads it.

        Returns:
            The id of the new command.
        """
        if command is None:
            command = pb2.Command(
                move_to_location_command=pb2.MoveToLocationCommand(target_location_id="home")
            )
        running = self._start(command)
        if finish:
            self._finish(running, success=True)
        return running.command_id

    def hold_starts(self) -> None:
        """Keep the answers of StartCommand back: a command starts, but its client does
        not learn its id until ``release_starts()``."""
        self._answer_starts.clear()

    def release_starts(self) -> None:
        """Send the answers of StartCommand that ``hold_starts()`` kept back."""
        self._answer_starts.set()

    def see(
        self,
        *,
        persons: Sequence[float] = (),
        others: Sequence[tuple[pb2.ObjectLabel, float]] = (),
    ) -> None:
        """Set what the detector sees, from its next detection on.

        Args:
            persons: The score of each person.
            others: The label and the score of each other object, such as a shelf.
        """
        labelled = [(pb2.OBJECT_LABEL_PERSON, score) for score in persons] + list(others)
        self.objects = [
            pb2.ObjectDetection(
                label=label,
                roi=pb2.RegionOfInterest(x_offset=160 * index, y_offset=200, height=400, width=120),
                score=score,
                distance_median=1.5,
            )
            for index, (label, score) in enumerate(labelled)
        ]

    def set_detecting(self, detecting: bool) -> None:
        """Whether the detector publishes. While it does not, the latest detection stays."""
        self.detecting = detecting

    def refuse_detection(self, refused: bool = True) -> None:
        """Whether the robot refuses GetObjectDetection, as software without the RPC does."""
        self.detection_refused = refused

    def refuse_results(self, refused: bool = True) -> None:
        """Whether the robot refuses GetLastCommandResult, with INTERNAL."""
        self.result_refused = refused

    def requests(self, method: str) -> list[Any]:
        """The requests received for one RPC, oldest first."""
        return [r.request for r in self.received if r.method == method]

    # The robot.

    def _location(self, location_id: str) -> tuple[float, float, float] | None:
        for lid, _, pose, _ in LOCATIONS:
            if lid == location_id:
                return pose
        return None

    def _check(self, command: pb2.Command) -> int | None:
        """The error code StartCommand refuses the command with, or None."""
        kind = command.WhichOneof("command")
        if kind is None:
            return INVALID_COMMAND
        if kind == "move_to_location_command":
            target = command.move_to_location_command.target_location_id
            if self._location(target) is None:
                return UNKNOWN_LOCATION
        return None

    def _target(self, command: pb2.Command) -> tuple[float, float, float] | None:
        """Where a command drives the robot, or None for a command that does not move."""
        kind = command.WhichOneof("command")
        if kind == "move_to_location_command":
            return self._location(command.move_to_location_command.target_location_id)
        if kind == "return_home_command":
            return self._location("home")
        if kind == "move_to_pose_command":
            pose = command.move_to_pose_command
            return (pose.x, pose.y, pose.yaw)
        return None

    def _start(self, command: pb2.Command) -> _Running:
        """Put a command in the command slot, cancelling the one there."""
        if self._running is not None:
            if self.replaced_result:
                self._finish(self._running, success=False, error_code=CANCELLED)
            else:
                self._drop(self._running)
        running = _Running(command_id=uuid.uuid4().hex, command=command)
        self._running = running
        fail, self._fail_next = self._fail_next, None
        running.task = asyncio.get_running_loop().create_task(self._drive(running, fail))
        self._state_topic.publish()
        logger.info("Command %s started: %s", running.command_id, command.WhichOneof("command"))
        return running

    async def _drive(self, running: _Running, fail: int | None) -> None:
        """Run a command one tick at a time; even a command that does not move takes one."""
        kind = running.command.WhichOneof("command")
        if fail is None and kind == "move_forward_command":
            await self._move_forward(running)
            return
        if fail is None and kind == "rotate_in_place_command":
            await self._rotate_in_place(running)
            return
        target = self._target(running.command)
        if fail is not None or target is None:
            await asyncio.sleep(TICK)
            self._finish(running, success=fail is None, error_code=fail or 0)
            return
        tx, ty, ttheta = target
        while True:
            await asyncio.sleep(TICK)
            dx, dy = tx - self.x, ty - self.y
            distance = math.hypot(dx, dy)
            step = SPEED * self.speed_scale * TICK
            if distance <= step:
                self.x, self.y, self.theta = tx, ty, _wrap(ttheta)
                self._pose_topic.publish()
                break
            self.x += dx / distance * step
            self.y += dy / distance * step
            self.theta = math.atan2(dy, dx)
            self._pose_topic.publish()
        self._finish(running, success=True)

    async def _move_forward(self, running: _Running) -> None:
        """Drive along the heading by the command's distance, backwards for a negative
        one, at the command's speed or, without one, at the robot's."""
        move = running.command.move_forward_command
        sign = math.copysign(1.0, move.distance_meter)
        left = abs(move.distance_meter)
        while True:
            await asyncio.sleep(TICK)
            speed = (move.speed if move.speed > 0 else SPEED) * self.speed_scale
            step = min(speed * TICK, left)
            self.x += sign * step * math.cos(self.theta)
            self.y += sign * step * math.sin(self.theta)
            left -= step
            self._pose_topic.publish()
            if left <= 0:
                break
        self._finish(running, success=True)

    async def _rotate_in_place(self, running: _Running) -> None:
        """Turn by the command's angle, counter-clockwise for a positive one."""
        angle = running.command.rotate_in_place_command.angle_radian
        sign = math.copysign(1.0, angle)
        left = abs(angle)
        while True:
            await asyncio.sleep(TICK)
            step = min(TURN_SPEED * self.speed_scale * TICK, left)
            self.theta = _wrap(self.theta + sign * step)
            left -= step
            self._pose_topic.publish()
            if left <= 0:
                break
        self._finish(running, success=True)

    def _drop(self, running: _Running) -> None:
        """End the command in the slot without a result, as the robot does with a command
        that a new one replaces."""
        self._running = None
        if running.task is not None and running.task is not asyncio.current_task():
            running.task.cancel()
        self._state_topic.publish()

    def _finish(self, running: _Running, *, success: bool, error_code: int = 0) -> None:
        """End the command in the slot with a result, and record it in the history."""
        if self._running is not running:
            return
        self._running = None
        if running.task is not None and running.task is not asyncio.current_task():
            running.task.cancel()
        result = pb2.Result(success=success, error_code=error_code)
        self._last = pb2.GetLastCommandResultResponse(
            result=result, command=running.command, command_id=running.command_id
        )
        # The history lists the newest command first.
        self.history.insert(
            0,
            pb2.History(
                id=running.command_id,
                command=running.command,
                success=success,
                error_code=error_code,
                command_executed_time=int(time.time()),
            ),
        )
        self._result_topic.publish()
        self._state_topic.publish()
        self._history_topic.publish()
        logger.info("Command %s ended: success=%s code=%s", running.command_id, success, error_code)

    async def _detect(self) -> None:
        """Publish what the detector sees, once each period, while it detects."""
        while True:
            await asyncio.sleep(self.detection_period)
            if not self.detecting:
                continue
            stamp = time.time_ns() + int(self.clock_offset * 1e9)
            self._detection = pb2.GetObjectDetectionResponse(
                header=pb2.RosHeader(stamp_nsec=stamp), objects=self.objects
            )
            self._detection_topic.publish()

    def _log(self, method: str, request: Any) -> None:
        self.received.append(Received(method, request))

    # The RPCs.

    @override
    async def IsReady(self, request: pb2.EmptyRequest, context: Any) -> pb2.IsReadyResponse:
        self._log("IsReady", request)
        return pb2.IsReadyResponse(ready=self.ready)

    @override
    async def GetRobotErrorCodeJson(
        self, request: pb2.EmptyRequest, context: Any
    ) -> pb2.GetRobotErrorCodeJsonResponse:
        self._log("GetRobotErrorCodeJson", request)
        table = [
            {
                "code": code,
                "error_type": "Error",
                "title": title,
                "description": "",
                "title_en": title,
                "description_en": "",
                "ref_url": "",
            }
            for code, title in ERROR_CODES.items()
        ]
        return pb2.GetRobotErrorCodeJsonResponse(
            json=json.dumps(table), result=pb2.Result(success=True)
        )

    @override
    async def GetError(self, request: pb2.GetRequest, context: Any) -> pb2.GetErrorResponse:
        self._log("GetError", request)
        await self._error_topic.wait(request.metadata.cursor)
        return pb2.GetErrorResponse(
            metadata=pb2.Metadata(cursor=self._error_topic.cursor), error_codes=self.errors
        )

    @override
    async def GetRobotPose(self, request: pb2.GetRequest, context: Any) -> pb2.GetRobotPoseResponse:
        self._log("GetRobotPose", request)
        await self._pose_topic.wait(request.metadata.cursor)
        return pb2.GetRobotPoseResponse(
            metadata=pb2.Metadata(cursor=self._pose_topic.cursor), pose=_pose(*self.pose)
        )

    @override
    async def GetLocations(self, request: pb2.GetRequest, context: Any) -> pb2.GetLocationsResponse:
        self._log("GetLocations", request)
        await self._static_topic.wait(request.metadata.cursor)
        locations = [
            pb2.Location(id=lid, name=name, pose=_pose(*pose), type=kind)
            for lid, name, pose, kind in LOCATIONS
        ]
        return pb2.GetLocationsResponse(
            metadata=pb2.Metadata(cursor=self._static_topic.cursor),
            locations=locations,
            default_location_id=DEFAULT_LOCATION_ID,
        )

    @override
    async def GetPngMap(self, request: pb2.GetRequest, context: Any) -> pb2.GetPngMapResponse:
        self._log("GetPngMap", request)
        await self._static_topic.wait(request.metadata.cursor)
        robot_map = pb2.Map(
            data=MAP_PNG,
            name=MAP_NAME,
            resolution=MAP_RESOLUTION,
            width=MAP_WIDTH,
            height=MAP_HEIGHT,
            origin=_pose(*MAP_ORIGIN),
        )
        return pb2.GetPngMapResponse(
            metadata=pb2.Metadata(cursor=self._static_topic.cursor), map=robot_map
        )

    @override
    async def GetCurrentMapId(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetCurrentMapIdResponse:
        self._log("GetCurrentMapId", request)
        await self._static_topic.wait(request.metadata.cursor)
        return pb2.GetCurrentMapIdResponse(
            metadata=pb2.Metadata(cursor=self._static_topic.cursor), id=MAP_ID
        )

    @override
    async def GetObjectDetection(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetObjectDetectionResponse:
        self._log("GetObjectDetection", request)
        if self.detection_refused:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Method not implemented!")
        # Before the first detection, even cursor 0 waits for it.
        while self._detection is None:
            await self._detection_topic.wait(self._detection_topic.cursor)
        await self._detection_topic.wait(request.metadata.cursor)
        response = pb2.GetObjectDetectionResponse()
        response.CopyFrom(self._detection)
        response.metadata.cursor = self._detection_topic.cursor
        return response

    @override
    async def StartCommand(
        self, request: pb2.StartCommandRequest, context: Any
    ) -> pb2.StartCommandResponse:
        self._log("StartCommand", request)
        refused, self._refuse_next = self._refuse_next, None
        if refused is None and not self.ready:
            refused = NOT_READY
        if refused is None:
            refused = self._check(request.command)
        if refused is None and self._running is not None and not request.cancel_all:
            refused = BUSY
        if refused is not None:
            return pb2.StartCommandResponse(result=pb2.Result(success=False, error_code=refused))
        running = self._start(request.command)
        await self._answer_starts.wait()
        return pb2.StartCommandResponse(
            result=pb2.Result(success=True), command_id=running.command_id
        )

    @override
    async def CancelCommand(
        self, request: pb2.EmptyRequest, context: Any
    ) -> pb2.CancelCommandResponse:
        self._log("CancelCommand", request)
        running = self._running
        if running is None:
            return pb2.CancelCommandResponse(result=pb2.Result(success=True))
        self._finish(running, success=False, error_code=CANCELLED)
        return pb2.CancelCommandResponse(result=pb2.Result(success=True), command=running.command)

    @override
    async def GetCommandState(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetCommandStateResponse:
        self._log("GetCommandState", request)
        await self._state_topic.wait(request.metadata.cursor)
        metadata = pb2.Metadata(cursor=self._state_topic.cursor)
        running = self._running
        if running is None:
            return pb2.GetCommandStateResponse(metadata=metadata, state=pb2.COMMAND_STATE_PENDING)
        return pb2.GetCommandStateResponse(
            metadata=metadata,
            state=pb2.COMMAND_STATE_RUNNING,
            command=running.command,
            command_id=running.command_id,
        )

    @override
    async def GetLastCommandResult(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetLastCommandResultResponse:
        self._log("GetLastCommandResult", request)
        if self.result_refused:
            await context.abort(grpc.StatusCode.INTERNAL, "No result")
        try:
            async with asyncio.timeout(self.result_wait):
                await self._result_topic.wait(request.metadata.cursor)
        except TimeoutError:
            await context.abort(grpc.StatusCode.CANCELLED, "")
        response = pb2.GetLastCommandResultResponse()
        if self._last is not None:
            response.CopyFrom(self._last)
        response.metadata.cursor = self._result_topic.cursor
        return response

    @override
    async def GetHistoryList(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetHistoryListResponse:
        self._log("GetHistoryList", request)
        await self._history_topic.wait(request.metadata.cursor)
        return pb2.GetHistoryListResponse(
            metadata=pb2.Metadata(cursor=self._history_topic.cursor), histories=self.history
        )

    @override
    async def SetAutoHomingEnabled(
        self, request: pb2.SetAutoHomingEnabledRequest, context: Any
    ) -> pb2.SetAutoHomingEnabledResponse:
        self._log("SetAutoHomingEnabled", request)
        self.auto_homing = request.enable
        self._settings_topic.publish()
        return pb2.SetAutoHomingEnabledResponse(result=pb2.Result(success=True))

    @override
    async def GetAutoHomingEnabled(
        self, request: pb2.GetRequest, context: Any
    ) -> pb2.GetAutoHomingEnabledResponse:
        self._log("GetAutoHomingEnabled", request)
        await self._settings_topic.wait(request.metadata.cursor)
        return pb2.GetAutoHomingEnabledResponse(
            metadata=pb2.Metadata(cursor=self._settings_topic.cursor), enabled=self.auto_homing
        )


async def _serve(host: str, port: int, speed_scale: float) -> None:
    async with FakeKachaka(host=host, port=port, speed_scale=speed_scale):
        await asyncio.Event().wait()


def main(argv: Sequence[str] | None = None) -> None:
    """Serve a fake Kachaka until interrupted."""
    parser = argparse.ArgumentParser(
        prog="python -m openrois_adapter_kachaka.fake_robot",
        description="Serve a fake Kachaka on the Kachaka gRPC API, for the adapter without "
        "a robot.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to serve on")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--speed-scale",
        type=float,
        default=1.0,
        help="how many times faster than 0.5 m/s the robot drives (default 1)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(args.host, args.port, args.speed_scale))


if __name__ == "__main__":
    main()
