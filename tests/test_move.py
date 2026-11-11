"""Move: lines and turns, parameters, time, stop, suspend and resume, and failures."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2

from openrois.components.kachaka import KachakaConnection, KachakaMove, KachakaNavigation
from openrois.components.kachaka.move import (
    LINE_TOLERANCE,
    MAX_DISTANCE,
    MAX_ORIENTATION,
    MAX_SPEED,
    MIN_SPEED,
    TURN_TOLERANCE,
    Pose,
    advanced,
    line_speed,
    remainder,
    turned,
)
from openrois_adapter_kachaka.fake_robot import (
    CANCELLED,
    INVALID_COMMAND,
    UNREACHABLE,
    FakeKachaka,
)
from tests.conftest import Host, Robot


def connection_to(fake: FakeKachaka, **settings: Any) -> KachakaConnection:
    """A connection with short durations: a robot that does not answer is lost at once."""
    options: dict[str, Any] = {"timeout": 1.0, "retry": 0.05, "lost_after": 0.0}
    options.update(settings)
    return KachakaConnection(address=fake.address, **options)


async def move(host: Host, fake: FakeKachaka, **settings: Any) -> Robot:
    robot: Robot = await host({"move": KachakaMove(connection_to(fake, **settings))})
    return robot


async def move_and_navigation(host: Host, fake: FakeKachaka) -> Robot:
    """Move and Navigation of one robot, on one connection."""
    connection = connection_to(fake)
    components = {"move": KachakaMove(connection), "navigation": KachakaNavigation(connection)}
    robot: Robot = await host(components)
    return robot


async def wait_for(condition: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def moving(fake: FakeKachaka) -> str:
    """Wait until the robot runs a command, and return its id."""
    await wait_for(lambda: fake.running_command_id is not None)
    command_id = fake.running_command_id
    assert command_id is not None
    return command_id


def started(fake: FakeKachaka) -> list[pb2.Command]:
    """The commands the robot was asked to start, oldest first."""
    return [request.command for request in fake.requests("StartCommand")]


def kinds(fake: FakeKachaka) -> list[str | None]:
    return [c.WhichOneof("command") for c in started(fake)]


def turns(fake: FakeKachaka) -> list[float]:
    """The angles of the turns the robot was asked to start, in degrees."""
    return [
        math.degrees(c.rotate_in_place_command.angle_radian)
        for c in started(fake)
        if c.WhichOneof("command") == "rotate_in_place_command"
    ]


def lines(fake: FakeKachaka) -> list[pb2.MoveForwardCommand]:
    """The straight lines the robot was asked to start."""
    return [
        c.move_forward_command
        for c in started(fake)
        if c.WhichOneof("command") == "move_forward_command"
    ]


def heading(fake: FakeKachaka) -> float:
    """The robot's heading in degrees, from -180 to 180."""
    return math.degrees(fake.theta)


def to_location(location_id: str) -> pb2.Command:
    return pb2.Command(
        move_to_location_command=pb2.MoveToLocationCommand(target_location_id=location_id)
    )


# -- The profile and the arithmetic --------------------------------------------------------


def test_serves_the_move_profile() -> None:
    profile = KachakaMove.rois_profile()
    assert profile.identifier.authority == "OMG"
    assert profile.identifier.code == "Move"
    assert profile.function == "actuation"
    assert [c.name for c in profile.command_profiles] == ["start", "stop", "suspend", "resume"]
    assert [q.name for q in profile.query_profiles] == ["component_status"]
    assert profile.event_profiles == []
    assert [p.name for p in profile.parameter_profiles] == ["line", "curve", "time"]


@pytest.mark.parametrize(
    ("distance", "seconds", "speed"),
    [
        (1.0, 0, 0.0),
        (1.0, 5, 0.2),
        (-1.0, 5, 0.2),
        (1.0, 4, 0.25),
        (3.0, 1, MAX_SPEED),
        (0.1, 100, MIN_SPEED),
        (0.0, 5, 0.0),
    ],
)
def test_time_sets_a_speed_within_the_limits(distance: float, seconds: int, speed: float) -> None:
    assert line_speed(distance, seconds) == pytest.approx(speed)


@pytest.mark.parametrize(
    ("before", "after", "sense", "degrees"),
    [
        (0.0, 90.0, 1.0, 90.0),
        (0.0, -90.0, -1.0, -90.0),
        (30.0, 30.0, 1.0, 0.0),
        # Across the wrap of the heading at 180 degrees, in either sense.
        (170.0, -150.0, 1.0, 40.0),
        (-170.0, 150.0, -1.0, -40.0),
        # Headings that are not wrapped.
        (350.0, 370.0, 1.0, 20.0),
        (-725.0, 5.0, 1.0, 10.0),
        # More than half a turn.
        (0.0, -170.0, 1.0, 190.0),
        (0.0, 170.0, -1.0, -190.0),
        # A robot that turned back a little.
        (10.0, 5.0, 1.0, -5.0),
        (-10.0, -5.0, -1.0, 5.0),
        (-179.0, 179.0, 1.0, -2.0),
    ],
)
def test_a_turn_is_measured_in_its_sense(
    before: float, after: float, sense: float, degrees: float
) -> None:
    measured = turned(math.radians(before), math.radians(after), sense)
    assert math.degrees(measured) == pytest.approx(degrees)


@pytest.mark.parametrize(
    ("before", "after", "meters"),
    [
        ((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), 0.5),
        ((0.0, 0.0, 0.0), (-0.5, 0.0, 0.0), -0.5),
        ((1.0, 1.0, math.pi / 2), (1.0, 1.75, math.pi / 2), 0.75),
        ((1.0, 1.0, math.pi), (1.5, 1.0, math.pi), -0.5),
        ((0.0, 0.0, math.pi / 4), (1.0, 1.0, math.pi / 4), math.sqrt(2.0)),
        # Only the way along the heading counts, whatever the heading is afterwards.
        ((0.0, 0.0, 0.0), (0.4, 0.3, 1.0), 0.4),
        ((0.0, 0.0, 0.0), (0.0, 0.3, 0.0), 0.0),
    ],
)
def test_a_line_is_measured_along_the_heading(before: Pose, after: Pose, meters: float) -> None:
    assert advanced(before, after) == pytest.approx(meters)


@pytest.mark.parametrize(
    ("asked", "done", "tolerance", "left"),
    [
        (90.0, 40.0, TURN_TOLERANCE, 50.0),
        (-90.0, -40.0, TURN_TOLERANCE, -50.0),
        (1.0, 0.25, LINE_TOLERANCE, 0.75),
        (-1.0, -0.25, LINE_TOLERANCE, -0.75),
        (90.0, 0.0, TURN_TOLERANCE, 90.0),
        # Within the tolerance of the end.
        (90.0, 88.5, TURN_TOLERANCE, 0.0),
        (-90.0, -88.0, TURN_TOLERANCE, 0.0),
        (1.0, 0.985, LINE_TOLERANCE, 0.0),
        (90.0, 87.9, TURN_TOLERANCE, 2.1),
        # Past the end.
        (90.0, 95.0, TURN_TOLERANCE, 0.0),
        (-1.0, -1.2, LINE_TOLERANCE, 0.0),
        # A robot that went the other way has the whole part left, and no more.
        (90.0, -5.0, TURN_TOLERANCE, 90.0),
        (-1.0, 0.3, LINE_TOLERANCE, -1.0),
    ],
)
def test_what_remains_of_a_part(asked: float, done: float, tolerance: float, left: float) -> None:
    assert remainder(asked, done, tolerance) == pytest.approx(left)


# -- set_parameter -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"line": []},
        {"line": [500]},
        {"line": [500, 90, 1]},
        {"line": [500.5, 90]},
        {"line": ["500", "90"]},
        {"line": [MAX_DISTANCE + 1, 0]},
        {"line": [-MAX_DISTANCE - 1, 0]},
        {"line": [0, MAX_ORIENTATION + 1]},
        {"line": [0, -MAX_ORIENTATION - 1]},
        {"curve": [500, 90]},
        {"curve": [0]},
        {"time": -1},
        {"line": [500, 90], "time": -1},
        {"line": [500, 90], "curve": [500, 90]},
    ],
)
async def test_set_parameter_refuses_bad_values(
    host: Host, fake_robot: FakeKachaka, values: dict[str, object]
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", **values) == "BAD_PARAMETER"
    result = await robot.call("rois.command.get_parameter", component_ref=robot.refs["move"])
    assert [p["value"] for p in result["parameters"] if p["value"]] == []


@pytest.mark.parametrize(
    "values",
    [
        {"line": [500, 90]},
        {"line": [-500, -90]},
        {"line": [MAX_DISTANCE, MAX_ORIENTATION]},
        {"line": [-MAX_DISTANCE, -MAX_ORIENTATION]},
        {"line": [0, 0]},
        {"curve": []},
        {"time": 0},
        {"time": 30},
        {"line": [500, 270], "curve": [], "time": 5},
    ],
)
async def test_set_parameter_accepts_good_values(
    host: Host, fake_robot: FakeKachaka, values: dict[str, object]
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", **values) == "OK"


async def test_set_parameter_says_why_it_refuses(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", curve=[500, 90]) == "BAD_PARAMETER"
    assert "no curves: curve stays empty, not [500, 90]" in caplog.text
    assert await robot.set("move", line=[500]) == "BAD_PARAMETER"
    assert "line is two integers, a distance in mm and an orientation in degrees" in caplog.text
    assert await robot.set("move", line=[20000, 0]) == "BAD_PARAMETER"
    assert f"The distance of line is at most {MAX_DISTANCE} mm, not 20000" in caplog.text
    assert await robot.set("move", line=[0, 4000]) == "BAD_PARAMETER"
    assert f"The orientation of line is at most {MAX_ORIENTATION} degrees, not 4000" in caplog.text
    assert await robot.set("move", time=-5) == "BAD_PARAMETER"
    assert "time is in seconds, 0 or more, not -5" in caplog.text


async def test_a_refused_value_keeps_the_old_one(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[500, 0]) == "OK"
    assert await robot.set("move", line=[500]) == "BAD_PARAMETER"
    assert await robot.run("move", "start") == "OK"
    assert fake_robot.pose == pytest.approx((0.5, 0.0, 0.0))


# -- Lines and turns -----------------------------------------------------------------------


async def test_start_without_a_line_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.run("move", "start") == "ERROR"
    assert "Set line first" in caplog.text
    assert started(fake_robot) == []


async def test_a_line_of_nothing_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[0, 0]) == "OK"
    assert await robot.run("move", "start") == "ERROR"
    assert "Set line first" in caplog.text
    assert started(fake_robot) == []


async def test_start_turns_then_drives_straight(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[500, 90]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert kinds(fake_robot) == ["rotate_in_place_command", "move_forward_command"]
    assert turns(fake_robot) == pytest.approx([90.0])
    [line] = lines(fake_robot)
    assert (line.distance_meter, line.speed) == (0.5, 0.0)
    # A positive orientation is counter-clockwise: the robot now faces along +y.
    assert fake_robot.pose == pytest.approx((0.0, 0.5, math.pi / 2), abs=1e-9)
    first, second = fake_robot.requests("StartCommand")
    assert first.cancel_all and second.cancel_all
    assert (first.title, second.title) == ("OpenRoIS: turn 90 deg", "OpenRoIS: line 500 mm")


async def test_a_negative_orientation_turns_clockwise(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[1000, -90]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert turns(fake_robot) == pytest.approx([-90.0])
    assert fake_robot.pose == pytest.approx((0.0, -1.0, -math.pi / 2), abs=1e-9)


async def test_a_line_without_an_orientation_only_drives(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.x, fake_robot.y, fake_robot.theta = 1.0, 1.0, math.pi / 2
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[500, 0]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert kinds(fake_robot) == ["move_forward_command"]
    assert fake_robot.pose == pytest.approx((1.0, 1.5, math.pi / 2))


async def test_a_line_without_a_distance_only_turns(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.x, fake_robot.y = 1.0, 1.0
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[0, 45]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert kinds(fake_robot) == ["rotate_in_place_command"]
    assert fake_robot.pose == pytest.approx((1.0, 1.0, math.pi / 4))


async def test_a_negative_distance_drives_backwards(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[-500, 0]) == "OK"
    assert await robot.run("move", "start") == "OK"
    [line] = lines(fake_robot)
    assert line.distance_meter == -0.5
    # The robot keeps its heading.
    assert fake_robot.pose == pytest.approx((-0.5, 0.0, 0.0))


@pytest.mark.parametrize(
    ("orientation", "steps", "final"),
    [
        (90, [90.0], 90.0),
        (91, [45.5, 45.5], 91.0),
        (180, [90.0, 90.0], 180.0),
        (-180, [-90.0, -90.0], 180.0),
        (270, [90.0, 90.0, 90.0], -90.0),
        (-200, [-200 / 3, -200 / 3, -200 / 3], 160.0),
        (360, [90.0, 90.0, 90.0, 90.0], 0.0),
        (450, [90.0, 90.0, 90.0, 90.0, 90.0], 90.0),
    ],
)
async def test_a_large_turn_is_sent_in_equal_turns_of_at_most_90_degrees(
    host: Host, fake_robot: FakeKachaka, orientation: int, steps: list[float], final: float
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[0, orientation]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert turns(fake_robot) == pytest.approx(steps)
    assert math.remainder(fake_robot.theta - math.radians(final), math.tau) == pytest.approx(0.0)


async def test_the_smallest_line_is_sent_as_it_is(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[1, 1]) == "OK"
    assert await robot.run("move", "start") == "OK"
    assert turns(fake_robot) == pytest.approx([1.0])
    assert [line.distance_meter for line in lines(fake_robot)] == [0.001]


async def test_set_parameter_and_start_in_one_execute(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    ref = robot.refs["move"]
    unit = {
        "component_ref": ref,
        "command_type": "set_parameter",
        "arguments": [{"name": "line", "data_type_ref": "", "value": "[250, 0]"}],
    }
    result = await robot.call(
        "rois.command.execute",
        command_unit_list=[unit, {"component_ref": ref, "command_type": "start"}],
    )
    first, second = (u["command_id"] for u in result["command_unit_list"])
    assert await robot.completed(first) == "OK"
    assert await robot.completed(second) == "OK"
    assert fake_robot.pose == pytest.approx((0.25, 0.0, 0.0))


# -- time ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "seconds", "speed"),
    [
        ([1000, 0], 0, 0.0),
        ([1000, 0], 5, 0.2),
        ([-1000, 0], 5, 0.2),
        ([1000, 90], 4, 0.25),
        ([3000, 0], 1, MAX_SPEED),
        ([100, 0], 100, MIN_SPEED),
    ],
)
async def test_time_sets_the_speed_of_the_line(
    host: Host, fake_robot: FakeKachaka, line: list[int], seconds: int, speed: float
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=line, time=seconds) == "OK"
    assert await robot.run("move", "start") == "OK"
    [sent] = lines(fake_robot)
    assert sent.distance_meter == line[0] / 1000
    assert sent.speed == pytest.approx(speed)


async def test_without_a_time_the_robot_drives_at_its_own_speed(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.set("move", line=[1000, 0]) == "OK"
    assert await robot.run("move", "start") == "OK"
    [sent] = lines(fake_robot)
    assert sent.speed == 0.0


# -- Failures ------------------------------------------------------------------------------


async def test_a_failed_turn_is_an_error_with_the_code_and_title(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    await robot.set("move", line=[500, 90])
    fake_robot.fail_next(UNREACHABLE)
    assert await robot.run("move", "start") == "ERROR"
    assert (
        f"The turn failed: error {UNREACHABLE} (The robot could not reach the destination)"
        in caplog.text
    )
    # The line does not start after a turn that failed.
    assert kinds(fake_robot) == ["rotate_in_place_command"]
    assert fake_robot.pose == (0.0, 0.0, 0.0)


async def test_a_failed_line_is_an_error_with_the_code_and_title(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    await robot.set("move", line=[500, 0])
    fake_robot.fail_next(UNREACHABLE)
    assert await robot.run("move", "start") == "ERROR"
    assert (
        f"The line failed: error {UNREACHABLE} (The robot could not reach the destination)"
        in caplog.text
    )


async def test_a_refused_part_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    await robot.set("move", line=[500, 90])
    fake_robot.fail_next(INVALID_COMMAND, on_start=True)
    assert await robot.run("move", "start") == "ERROR"
    assert f"The turn failed: error {INVALID_COMMAND} (The command is not valid)" in caplog.text
    assert len(started(fake_robot)) == 1


async def test_a_robot_lost_during_the_motion_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    await fake_robot.stop()
    assert await robot.completed(start) == "ERROR"
    assert "the robot may still be moving" in caplog.text


async def test_a_pose_the_robot_does_not_give_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot, lost_after=30.0)
    await robot.set("move", line=[500, 0])
    await fake_robot.stop()
    # The robot is not lost yet, so the engine runs the start.
    assert await robot.run("move", "start") == "ERROR"
    assert "Could not read the pose of the Kachaka" in caplog.text
    await fake_robot.start()
    assert started(fake_robot) == []


async def test_a_command_of_another_client_ends_the_start_with_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    other = fake_robot.preempt()
    assert await robot.completed(start) == "ERROR"
    assert f"The turn failed: error {CANCELLED} (The command was cancelled)" in caplog.text
    # The start leaves the other command running, and does not go on to its line.
    assert fake_robot.running_command_id == other
    assert fake_robot.requests("CancelCommand") == []
    assert len(started(fake_robot)) == 1


# -- Two motion components on one robot ----------------------------------------------------


async def test_a_start_of_move_replaces_a_drive_of_navigation(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move_and_navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    await robot.set("move", line=[0, 45])
    drive = await robot.execute("navigation", "start")
    await moving(fake_robot)
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "start") == "OK"
    assert await robot.completed(drive) == "ERROR"
    assert "The drive to meeting_room failed" in caplog.text
    assert "another command replaced it" in caplog.text
    assert kinds(fake_robot) == ["move_to_location_command", "rotate_in_place_command"]
    assert fake_robot.requests("CancelCommand") == []


async def test_a_start_of_navigation_replaces_a_motion_of_move(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move_and_navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["kitchen"])
    await robot.set("move", line=[2000, 0])
    motion = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.speed_scale = 20.0
    assert await robot.run("navigation", "start") == "OK"
    assert await robot.completed(motion) == "ERROR"
    assert "The line failed" in caplog.text
    assert "another command replaced it" in caplog.text
    assert kinds(fake_robot) == ["move_forward_command", "move_to_location_command"]
    assert fake_robot.requests("CancelCommand") == []


async def test_stop_of_move_leaves_a_drive_of_navigation(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move_and_navigation(host, fake_robot)
    await robot.set("move", line=[0, 45])
    assert await robot.run("move", "start") == "OK"
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    drive = await robot.execute("navigation", "start")
    ours = await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    assert await robot.run("move", "suspend") == "OK"
    assert await robot.run("move", "resume") == "OK"
    assert fake_robot.running_command_id == ours
    assert fake_robot.requests("CancelCommand") == []
    fake_robot.speed_scale = 20.0
    assert await robot.completed(drive) == "OK"


async def test_stop_of_navigation_leaves_a_motion_of_move(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move_and_navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"])
    assert await robot.run("navigation", "start") == "OK"
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    motion = await robot.execute("move", "start")
    ours = await moving(fake_robot)
    assert await robot.run("navigation", "stop") == "OK"
    assert fake_robot.running_command_id == ours
    assert fake_robot.requests("CancelCommand") == []
    fake_robot.speed_scale = 20.0
    assert await robot.completed(motion) == "OK"


# -- stop and the other aborts -------------------------------------------------------------


async def test_stop_cancels_the_turn_and_the_line_never_starts(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1
    pose = fake_robot.pose
    await asyncio.sleep(0.3)
    assert fake_robot.pose == pose
    assert kinds(fake_robot) == ["rotate_in_place_command"]


async def test_stop_cancels_the_line(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert fake_robot.history[0].error_code == CANCELLED
    assert 0.0 <= fake_robot.x < 2.0


async def test_a_stop_that_does_not_reach_the_robot_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot, lost_after=30.0)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    await fake_robot.stop()
    assert await robot.run("move", "stop") == "ERROR"
    assert "Could not cancel the motion of the Kachaka" in caplog.text
    assert await robot.completed(start) == "ABORT"


async def test_stop_leaves_a_command_of_another_client(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    await robot.set("move", line=[500, 0])
    assert await robot.run("move", "start") == "OK"
    fake_robot.speed_scale = 1.0
    other = fake_robot.preempt(to_location("meeting_room"))
    assert await robot.run("move", "stop") == "OK"
    assert fake_robot.running_command_id == other
    assert fake_robot.requests("CancelCommand") == []


async def test_a_stop_during_the_start_of_a_part_cancels_it(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    # The stop arrives after StartCommand left, before the robot answered.
    fake_robot.hold_starts()
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    fake_robot.release_starts()
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_a_new_start_aborts_the_running_one(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    first = await robot.execute("move", "start")
    old = await moving(fake_robot)
    fake_robot.speed_scale = 20.0
    await robot.set("move", line=[0, 90])
    second = await robot.execute("move", "start")
    assert await robot.completed(first) == "ABORT"
    assert await robot.completed(second) == "OK"
    assert next(h for h in fake_robot.history if h.id == old).error_code == CANCELLED
    assert heading(fake_robot) == pytest.approx(90.0)


async def test_the_engine_stopping_cancels_the_motion(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    await robot.execute("move", "start")
    ours = await moving(fake_robot)
    await robot.engine.stop()
    assert fake_robot.running_command_id is None
    assert fake_robot.history[0].id == ours
    assert fake_robot.history[0].error_code == CANCELLED
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_the_engine_stopping_leaves_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "suspend") == "OK"
    assert fake_robot.running_command_id is None
    cancels = len(fake_robot.requests("CancelCommand"))
    other = fake_robot.preempt(to_location("kitchen"))
    await robot.engine.stop()
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id == other
    assert len(fake_robot.requests("CancelCommand")) == cancels


async def test_a_release_cancels_the_motion(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    released = await robot.call("rois.command.release", component_ref=robot.refs["move"])
    assert released["return_code"] == "OK"
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1


# -- suspend and resume --------------------------------------------------------------------


async def test_suspend_halts_the_line_and_resume_drives_what_remains(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0], time=10)
    start = await robot.execute("move", "start")
    await wait_for(lambda: fake_robot.x > 0.0)
    assert await robot.run("move", "suspend") == "OK"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1
    halted = fake_robot.pose
    await asyncio.sleep(0.3)
    assert fake_robot.pose == halted
    assert len(started(fake_robot)) == 1
    assert 0.0 < halted[0] < 2.0

    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    first, second = lines(fake_robot)
    assert first.distance_meter == 2.0
    assert second.distance_meter == pytest.approx(2.0 - halted[0])
    # The speed stays the one that time set for the whole line.
    assert (first.speed, second.speed) == (0.2, 0.2)
    assert fake_robot.pose == pytest.approx((2.0, 0.0, 0.0))


async def test_resume_drives_what_remains_of_a_line_backwards(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.theta = math.pi / 2
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[-2000, 0])
    start = await robot.execute("move", "start")
    await wait_for(lambda: fake_robot.y < 0.0)
    assert await robot.run("move", "suspend") == "OK"
    halted = fake_robot.pose
    assert -2.0 < halted[1] < 0.0

    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    first, second = lines(fake_robot)
    assert first.distance_meter == -2.0
    assert second.distance_meter == pytest.approx(-2.0 - halted[1])
    assert fake_robot.pose == pytest.approx((0.0, -2.0, math.pi / 2))


async def test_suspend_halts_the_turn_and_resume_turns_what_remains(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[500, 90])
    start = await robot.execute("move", "start")
    await wait_for(lambda: fake_robot.theta > 0.0)
    assert await robot.run("move", "suspend") == "OK"
    assert fake_robot.running_command_id is None
    halted = heading(fake_robot)
    assert 0.0 < halted < 90.0
    await asyncio.sleep(0.3)
    assert kinds(fake_robot) == ["rotate_in_place_command"]

    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert turns(fake_robot) == pytest.approx([90.0, 90.0 - halted])
    assert kinds(fake_robot)[-1] == "move_forward_command"
    assert fake_robot.pose == pytest.approx((0.0, 0.5, math.pi / 2), abs=1e-9)


async def test_what_remains_of_a_turn_is_measured_across_the_wrap_of_the_heading(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.theta = math.radians(170.0)
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0  # The robot starts its commands, and stays where it is.
    await robot.set("move", line=[0, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    # The robot turned 40 degrees, past 180, when the suspend cancels the turn.
    fake_robot.theta = math.radians(-150.0)
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert turns(fake_robot) == pytest.approx([90.0, 50.0])
    assert heading(fake_robot) == pytest.approx(-100.0)


async def test_what_remains_of_a_clockwise_turn_is_measured_across_the_wrap(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.theta = math.radians(-170.0)
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[0, -90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.theta = math.radians(130.0)
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert turns(fake_robot) == pytest.approx([-90.0, -30.0])
    assert heading(fake_robot) == pytest.approx(100.0)


async def test_resume_turns_what_remains_of_a_large_turn(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[0, 270])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    # 60 of the first 90 degrees are done: 210 remain, sent as three turns of 70.
    fake_robot.theta = math.radians(60.0)
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert turns(fake_robot) == pytest.approx([90.0, 70.0, 70.0, 70.0])
    assert heading(fake_robot) == pytest.approx(-90.0)


async def test_a_turn_that_was_almost_done_counts_as_done(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[500, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.theta = math.radians(90.0 - TURN_TOLERANCE / 2)
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    # The turn is not sent again, and the line follows.
    assert kinds(fake_robot) == ["rotate_in_place_command", "move_forward_command"]
    assert turns(fake_robot) == pytest.approx([90.0])


async def test_a_line_that_was_almost_done_counts_as_done(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.x = 2.0 - LINE_TOLERANCE / 2
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert len(started(fake_robot)) == 1


async def test_a_line_that_went_past_its_end_counts_as_done(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.x = 2.3
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert len(started(fake_robot)) == 1
    assert fake_robot.x == 2.3


async def test_a_robot_that_went_the_other_way_drives_the_line_and_no_more(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[1000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.x = -0.5
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert [line.distance_meter for line in lines(fake_robot)] == [1.0, 1.0]


async def test_a_second_suspension_measures_from_where_the_first_ended(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 0.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    fake_robot.x = 0.5
    assert await robot.run("move", "suspend") == "OK"
    assert await robot.run("move", "resume") == "OK"
    await moving(fake_robot)
    fake_robot.x = 1.25
    assert await robot.run("move", "suspend") == "OK"
    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert [line.distance_meter for line in lines(fake_robot)] == pytest.approx([2.0, 1.5, 0.75])
    assert fake_robot.pose == pytest.approx((2.0, 0.0, 0.0))


async def test_stop_while_suspended_aborts_the_start(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "suspend") == "OK"
    assert await robot.run("move", "stop") == "OK"
    assert await robot.completed(start) == "ABORT"
    await asyncio.sleep(0.2)
    assert len(started(fake_robot)) == 1
    assert fake_robot.running_command_id is None


async def test_a_suspend_during_the_start_of_a_part_halts_it(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    # The suspend arrives after StartCommand left, before the robot answered.
    fake_robot.hold_starts()
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "suspend") == "OK"
    assert fake_robot.requests("CancelCommand") == []
    fake_robot.release_starts()
    await wait_for(lambda: fake_robot.running_command_id is None)
    assert len(fake_robot.requests("CancelCommand")) == 1
    await asyncio.sleep(0.2)
    assert len(started(fake_robot)) == 1

    fake_robot.speed_scale = 20.0
    assert await robot.run("move", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert fake_robot.pose == pytest.approx((2.0, 0.0, 0.0))


async def test_a_suspend_that_does_not_reach_the_robot_is_an_error_and_holds_nothing(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await move(host, fake_robot, lost_after=30.0)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[500, 90])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    await fake_robot.stop()
    assert await robot.run("move", "suspend") == "ERROR"
    assert "Could not halt the motion of the Kachaka" in caplog.text
    # The robot was never halted, and the start goes on once the robot answers again.
    fake_robot.speed_scale = 20.0
    await fake_robot.start()
    assert await robot.completed(start) == "OK"
    assert kinds(fake_robot) == ["rotate_in_place_command", "move_forward_command"]
    assert fake_robot.pose == pytest.approx((0.0, 0.5, math.pi / 2), abs=1e-9)


async def test_suspend_leaves_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    await robot.set("move", line=[500, 0])
    assert await robot.run("move", "start") == "OK"
    fake_robot.speed_scale = 1.0
    other = fake_robot.preempt(to_location("meeting_room"))
    assert await robot.run("move", "suspend") == "OK"
    assert fake_robot.running_command_id == other
    assert await robot.run("move", "resume") == "OK"


async def test_suspend_and_resume_before_a_start_do_nothing(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    assert await robot.run("move", "suspend") == "OK"
    assert await robot.run("move", "resume") == "OK"
    assert await robot.run("move", "stop") == "OK"
    assert fake_robot.requests("CancelCommand") == []
    assert fake_robot.requests("GetCommandState") == []


# -- Cancels that race with a start --------------------------------------------------------


async def test_a_second_stop_during_the_start_of_a_part_still_cancels_it(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    # Two stops arrive after StartCommand left, before the robot answered.
    fake_robot.hold_starts()
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    assert await robot.run("move", "stop") == "OK"
    fake_robot.release_starts()
    assert await robot.completed(start) == "ABORT"
    await wait_for(lambda: fake_robot.running_command_id is None)
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_the_engine_stopping_during_the_start_of_a_part_cancels_it(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    fake_robot.hold_starts()
    await robot.execute("move", "start")
    await moving(fake_robot)
    assert await robot.run("move", "stop") == "OK"
    stopping = asyncio.create_task(robot.engine.stop())
    await asyncio.sleep(0.05)
    fake_robot.release_starts()
    await stopping
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_a_stop_does_not_cancel_the_motion_that_replaced_its_own(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot)
    components = {"move": KachakaMove(connection), "navigation": KachakaNavigation(connection)}
    robot: Robot = await host(components)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    await robot.set("move", line=[2000, 0])
    drive = await robot.execute("navigation", "start")
    old = await moving(fake_robot)

    # The answer about the running command is slow on its way to the stop of Navigation.
    asked = asyncio.Event()
    answer = asyncio.Event()
    ask = connection._running_command

    async def slow() -> str | None:
        running = await ask()
        if not asked.is_set():
            asked.set()
            await answer.wait()
        return running

    connection._running_command = slow  # type: ignore[method-assign]
    stop = await robot.execute("navigation", "stop")
    await asked.wait()
    motion = await robot.execute("move", "start")
    await asyncio.sleep(0.1)
    # The start of Move waits for the stop, which cancels the drive and nothing else.
    assert fake_robot.running_command_id == old
    answer.set()
    assert await robot.completed(stop) == "OK"
    assert await robot.completed(drive) == "ABORT"
    await wait_for(lambda: fake_robot.running_command_id not in (None, old))
    assert len(fake_robot.requests("CancelCommand")) == 1
    fake_robot.speed_scale = 20.0
    assert await robot.completed(motion) == "OK"


async def test_a_motion_cancelled_after_a_failed_suspend_is_not_driven_again(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await move(host, fake_robot, lost_after=30.0)
    fake_robot.speed_scale = 1.0
    await robot.set("move", line=[2000, 0])
    start = await robot.execute("move", "start")
    await moving(fake_robot)
    await fake_robot.stop()
    assert await robot.run("move", "suspend") == "ERROR"
    await fake_robot.start()
    # Another client cancels the motion, as the Kachaka app does.
    await fake_robot.CancelCommand(pb2.EmptyRequest(), None)
    assert await robot.completed(start) == "ERROR"
    assert len(started(fake_robot)) == 1
    assert fake_robot.running_command_id is None
