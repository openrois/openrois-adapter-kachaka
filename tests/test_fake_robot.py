"""The fake Kachaka answers the Kachaka gRPC API as the robot does, read with the raw stub."""

from __future__ import annotations

import asyncio
import json
import math
import struct
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2
from kachaka_api.generated import kachaka_api_pb2_grpc as pb2_grpc

from openrois_adapter_kachaka import fake_robot
from openrois_adapter_kachaka.fake_robot import (
    CANCELLED,
    NOT_READY,
    UNKNOWN_LOCATION,
    UNREACHABLE,
    FakeKachaka,
)

# A short reconnect backoff, so a channel finds a restarted fake at once.
_OPTIONS = [
    ("grpc.initial_reconnect_backoff_ms", 100),
    ("grpc.min_reconnect_backoff_ms", 100),
    ("grpc.max_reconnect_backoff_ms", 200),
]


@pytest.fixture
async def stub(fake_robot: FakeKachaka) -> AsyncIterator[Any]:
    """The Kachaka API stub on a channel to the fake."""
    async with grpc.aio.insecure_channel(fake_robot.address, options=_OPTIONS) as channel:
        yield pb2_grpc.KachakaApiStub(channel)


def _move_to(location_id: str) -> pb2.StartCommandRequest:
    command = pb2.Command(
        move_to_location_command=pb2.MoveToLocationCommand(target_location_id=location_id)
    )
    return pb2.StartCommandRequest(command=command, cancel_all=True, title="test")


def _forward(distance: float, speed: float = 0.0) -> pb2.StartCommandRequest:
    command = pb2.Command(
        move_forward_command=pb2.MoveForwardCommand(distance_meter=distance, speed=speed)
    )
    return pb2.StartCommandRequest(command=command, cancel_all=True)


def _rotate(degrees: float) -> pb2.StartCommandRequest:
    command = pb2.Command(
        rotate_in_place_command=pb2.RotateInPlaceCommand(angle_radian=math.radians(degrees))
    )
    return pb2.StartCommandRequest(command=command, cancel_all=True)


async def _cursor(stub: Any) -> int:
    """The current cursor of the last command result."""
    response = await stub.GetLastCommandResult(pb2.GetRequest(), timeout=1.0)
    cursor: int = response.metadata.cursor
    return cursor


async def _next_result(stub: Any, cursor: int, timeout: float = 5.0) -> Any:
    """The first command result published after ``cursor``, by long-polling."""
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor))
    return await stub.GetLastCommandResult(request, timeout=timeout)


async def test_is_ready_follows_set_ready(fake_robot: FakeKachaka, stub: Any) -> None:
    assert (await stub.IsReady(pb2.EmptyRequest(), timeout=1.0)).ready
    fake_robot.set_ready(False)
    assert not (await stub.IsReady(pb2.EmptyRequest(), timeout=1.0)).ready
    assert len(fake_robot.requests("IsReady")) == 2


async def test_locations_are_neutral_with_the_charger_at_home(stub: Any) -> None:
    response = await stub.GetLocations(pb2.GetRequest(), timeout=1.0)
    ids = [location.id for location in response.locations]
    assert ids == ["home", "kitchen", "meeting_room"]
    [charger] = [loc for loc in response.locations if loc.type == pb2.LOCATION_TYPE_CHARGER]
    assert charger.id == "home"
    assert (charger.pose.x, charger.pose.y, charger.pose.theta) == (0.0, 0.0, 0.0)
    names = [location.name for location in response.locations]
    assert names == ["Charger", "Kitchen", "Meeting room"]


async def test_map_is_a_png_with_a_turned_origin(stub: Any) -> None:
    response = await stub.GetPngMap(pb2.GetRequest(), timeout=1.0)
    robot_map = response.map
    assert robot_map.data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", robot_map.data[16:24])
    assert (robot_map.width, robot_map.height) == (width, height)
    assert robot_map.resolution > 0
    assert robot_map.origin.theta != 0.0
    assert robot_map.name == fake_robot.MAP_NAME
    map_id = await stub.GetCurrentMapId(pb2.GetRequest(), timeout=1.0)
    assert map_id.id == fake_robot.MAP_ID


async def test_error_code_table_is_json_with_english_titles(stub: Any) -> None:
    response = await stub.GetRobotErrorCodeJson(pb2.EmptyRequest(), timeout=1.0)
    assert response.result.success
    table = {item["code"]: item for item in json.loads(response.json)}
    assert table[CANCELLED]["title_en"] == "The command was cancelled"
    assert set(table[CANCELLED]) == {
        "code",
        "error_type",
        "title",
        "description",
        "title_en",
        "description_en",
        "ref_url",
    }


async def test_cursor_zero_answers_at_once_and_the_latest_cursor_waits(stub: Any) -> None:
    """The long-poll: cursor 0 gets the latest data, the latest cursor waits for new data."""
    first = await stub.GetRobotPose(pb2.GetRequest(), timeout=1.0)
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=first.metadata.cursor))
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.GetRobotPose(request, timeout=0.3)
    assert caught.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED


async def test_no_last_command_has_an_empty_result(stub: Any) -> None:
    response = await stub.GetLastCommandResult(pb2.GetRequest(), timeout=1.0)
    assert not response.HasField("result")
    assert response.command_id == ""
    assert response.metadata.cursor != 0


async def test_move_to_location_drives_there_and_reports_its_result(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    assert started.result.success
    assert started.command_id

    state = await stub.GetCommandState(pb2.GetRequest(), timeout=1.0)
    assert state.state == pb2.COMMAND_STATE_RUNNING
    assert state.command_id == started.command_id

    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert result.result.success
    assert result.command.move_to_location_command.target_location_id == "kitchen"
    assert fake_robot.pose == pytest.approx((2.0, 1.0, math.pi / 2))

    pose = await stub.GetRobotPose(pb2.GetRequest(), timeout=1.0)
    assert (pose.pose.x, pose.pose.y) == pytest.approx((2.0, 1.0))
    state = await stub.GetCommandState(pb2.GetRequest(), timeout=1.0)
    assert state.state == pb2.COMMAND_STATE_PENDING
    [title] = [r.title for r in fake_robot.requests("StartCommand")]
    assert title == "test"


async def test_move_to_pose_ends_at_the_yaw(fake_robot: FakeKachaka, stub: Any) -> None:
    cursor = await _cursor(stub)
    command = pb2.Command(move_to_pose_command=pb2.MoveToPoseCommand(x=-0.5, y=0.5, yaw=-1.0))
    started = await stub.StartCommand(
        pb2.StartCommandRequest(command=command, cancel_all=True), timeout=1.0
    )
    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert result.result.success
    assert fake_robot.pose == pytest.approx((-0.5, 0.5, -1.0))


async def test_the_drive_takes_time_at_the_speed(fake_robot: FakeKachaka, stub: Any) -> None:
    """2.24 m at 0.5 m/s times 20, 1 m per 0.1 s tick, takes three ticks."""
    cursor = await _cursor(stub)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    await _next_result(stub, cursor)
    assert 0.15 < loop.time() - began < 1.0


async def test_the_pose_cursor_moves_while_driving(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.speed_scale = 2.0
    first = await stub.GetRobotPose(pb2.GetRequest(), timeout=1.0)
    await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=first.metadata.cursor))
    moved = await stub.GetRobotPose(request, timeout=1.0)
    assert moved.metadata.cursor != first.metadata.cursor
    assert 0.0 < moved.pose.x < 2.0


@pytest.mark.parametrize(
    ("distance", "end"), [(0.5, (1.0, 1.5, math.pi / 2)), (-0.5, (1.0, 0.5, math.pi / 2))]
)
async def test_move_forward_drives_along_the_heading(
    fake_robot: FakeKachaka, stub: Any, distance: float, end: tuple[float, float, float]
) -> None:
    fake_robot.x, fake_robot.y, fake_robot.theta = 1.0, 1.0, math.pi / 2
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_forward(distance), timeout=1.0)
    assert started.result.success
    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert result.result.success
    # A negative distance drives backwards, and the robot keeps its heading.
    assert fake_robot.pose == pytest.approx(end)


@pytest.mark.parametrize(("speed", "step"), [(0.0, 0.05), (0.2, 0.02), (1.0, 0.1)])
async def test_move_forward_drives_at_its_speed(
    fake_robot: FakeKachaka, stub: Any, speed: float, step: float
) -> None:
    """Without a speed the robot drives 0.05 m in each 0.1 s tick, at 0.5 m/s."""
    fake_robot.speed_scale = 1.0
    first = await stub.GetRobotPose(pb2.GetRequest(), timeout=1.0)
    await stub.StartCommand(_forward(1.0, speed), timeout=1.0)
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=first.metadata.cursor))
    moved = await stub.GetRobotPose(request, timeout=1.0)
    assert moved.pose.x == pytest.approx(step)


@pytest.mark.parametrize(
    ("degrees", "heading"), [(90.0, 90.0), (-90.0, -90.0), (45.0, 45.0), (270.0, -90.0)]
)
async def test_rotate_in_place_turns_by_the_angle(
    fake_robot: FakeKachaka, stub: Any, degrees: float, heading: float
) -> None:
    fake_robot.x, fake_robot.y = 1.0, 2.0
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_rotate(degrees), timeout=1.0)
    assert started.result.success
    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert result.result.success
    # A positive angle turns counter-clockwise, and the robot stays in place.
    assert fake_robot.pose == pytest.approx((1.0, 2.0, math.radians(heading)))


@pytest.mark.parametrize(
    ("start", "axis", "end"), [(_forward(2.0), 0, 2.0), (_rotate(90.0), 2, math.pi / 2)]
)
async def test_cancel_stops_a_line_and_a_turn_midway(
    fake_robot: FakeKachaka, stub: Any, start: pb2.StartCommandRequest, axis: int, end: float
) -> None:
    fake_robot.speed_scale = 1.0
    cursor = await _cursor(stub)
    started = await stub.StartCommand(start, timeout=1.0)
    await asyncio.sleep(0.25)
    await stub.CancelCommand(pb2.EmptyRequest(), timeout=1.0)
    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert result.result.error_code == CANCELLED
    stopped = fake_robot.pose
    await asyncio.sleep(0.25)
    assert fake_robot.pose == stopped
    assert 0.0 < stopped[axis] < end


async def test_cancel_stops_the_drive_with_a_failed_result(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.speed_scale = 1.0
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    await asyncio.sleep(0.25)
    cancelled = await stub.CancelCommand(pb2.EmptyRequest(), timeout=1.0)
    assert cancelled.result.success
    assert cancelled.command.move_to_location_command.target_location_id == "kitchen"

    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert not result.result.success
    assert result.result.error_code == CANCELLED
    stopped = fake_robot.pose
    await asyncio.sleep(0.25)
    assert fake_robot.pose == stopped
    assert 0.0 < stopped[0] < 2.0
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_cancel_without_a_command_succeeds(stub: Any) -> None:
    cancelled = await stub.CancelCommand(pb2.EmptyRequest(), timeout=1.0)
    assert cancelled.result.success
    assert cancelled.command.WhichOneof("command") is None


async def test_a_replaced_command_gets_no_result(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.speed_scale = 1.0
    first = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    cursor = await _cursor(stub)
    second = await stub.StartCommand(_move_to("meeting_room"), timeout=1.0)
    assert second.result.success

    # As on a real robot, the first one ends without a result.
    state = await stub.GetCommandState(pb2.GetRequest(), timeout=1.0)
    assert state.command_id == second.command_id
    assert await _cursor(stub) == cursor
    history = await stub.GetHistoryList(pb2.GetRequest(), timeout=1.0)
    assert first.command_id not in [h.id for h in history.histories]


async def test_a_new_command_cancels_the_running_one(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.replaced_result = True
    fake_robot.speed_scale = 1.0
    first = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    cursor = await _cursor(stub)
    second = await stub.StartCommand(_move_to("meeting_room"), timeout=1.0)
    assert second.result.success

    # The first one's result was published when the second one started.
    replaced = await stub.GetLastCommandResult(pb2.GetRequest(), timeout=1.0)
    assert replaced.metadata.cursor != cursor
    assert replaced.command_id == first.command_id
    assert replaced.result.error_code == CANCELLED

    history = await stub.GetHistoryList(pb2.GetRequest(), timeout=1.0)
    assert [h.id for h in history.histories] == [first.command_id]
    assert not history.histories[0].success


async def test_without_cancel_all_a_busy_robot_refuses(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.speed_scale = 1.0
    await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    request = _move_to("meeting_room")
    request.cancel_all = False
    refused = await stub.StartCommand(request, timeout=1.0)
    assert not refused.result.success
    assert refused.command_id == ""


async def test_unknown_location_is_refused(stub: Any) -> None:
    refused = await stub.StartCommand(_move_to("attic"), timeout=1.0)
    assert not refused.result.success
    assert refused.result.error_code == UNKNOWN_LOCATION


async def test_a_robot_that_is_not_ready_refuses_commands(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.set_ready(False)
    refused = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    assert refused.result.error_code == NOT_READY


async def test_fail_next_ends_the_next_command_with_the_code(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.fail_next(UNREACHABLE)
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    assert started.result.success
    result = await _next_result(stub, cursor)
    assert result.command_id == started.command_id
    assert not result.result.success
    assert result.result.error_code == UNREACHABLE
    assert fake_robot.pose == (0.0, 0.0, 0.0)

    # Only the next one.
    cursor = result.metadata.cursor
    again = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    result = await _next_result(stub, cursor)
    assert result.command_id == again.command_id
    assert result.result.success


async def test_fail_next_on_start_refuses_the_next_command(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.fail_next(12345, on_start=True)
    refused = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    assert not refused.result.success
    assert refused.result.error_code == 12345
    accepted = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    assert accepted.result.success


async def test_a_preempting_command_replaces_ours_and_the_history_keeps_it(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    """A result published between two reads is skipped by the cursor, but not by the history."""
    fake_robot.replaced_result = True
    fake_robot.speed_scale = 1.0
    cursor = await _cursor(stub)
    ours = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    waiting = asyncio.create_task(_next_result(stub, cursor))
    await asyncio.sleep(0.15)
    other = fake_robot.preempt(finish=True)

    result = await waiting
    assert result.command_id == other
    assert result.result.success

    history = await stub.GetHistoryList(pb2.GetRequest(), timeout=1.0)
    by_id = {h.id: h for h in history.histories}
    assert not by_id[ours.command_id].success
    assert by_id[ours.command_id].error_code == CANCELLED
    assert by_id[other].success
    assert by_id[ours.command_id].command.move_to_location_command.target_location_id == "kitchen"


async def test_a_preempting_command_runs_in_the_slot(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.speed_scale = 1.0
    await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    other = fake_robot.preempt()
    state = await stub.GetCommandState(pb2.GetRequest(), timeout=1.0)
    assert state.state == pb2.COMMAND_STATE_RUNNING
    assert state.command_id == other
    assert fake_robot.running_command_id == other


async def test_held_starts_answer_once_released(fake_robot: FakeKachaka, stub: Any) -> None:
    fake_robot.speed_scale = 1.0
    fake_robot.hold_starts()
    call = asyncio.ensure_future(stub.StartCommand(_move_to("kitchen"), timeout=2.0))
    await asyncio.sleep(0.2)
    # The command runs, but its client has no answer yet.
    assert fake_robot.running_command_id is not None
    assert not call.done()
    fake_robot.release_starts()
    started = await call
    assert started.result.success
    assert started.command_id == fake_robot.running_command_id


async def test_auto_homing_is_set_and_logged(fake_robot: FakeKachaka, stub: Any) -> None:
    assert fake_robot.auto_homing
    request = pb2.SetAutoHomingEnabledRequest(enable=False)
    response = await stub.SetAutoHomingEnabled(request, timeout=1.0)
    assert response.result.success
    assert not fake_robot.auto_homing
    [received] = fake_robot.requests("SetAutoHomingEnabled")
    assert received.enable is False
    enabled = await stub.GetAutoHomingEnabled(pb2.GetRequest(), timeout=1.0)
    assert enabled.enabled is False


async def test_get_error_reports_the_codes_set(fake_robot: FakeKachaka, stub: Any) -> None:
    first = await stub.GetError(pb2.GetRequest(), timeout=1.0)
    assert list(first.error_codes) == []
    fake_robot.set_errors([UNREACHABLE])
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=first.metadata.cursor))
    second = await stub.GetError(request, timeout=1.0)
    assert list(second.error_codes) == [UNREACHABLE]


async def test_stop_and_start_serve_again_on_the_same_port(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    address = fake_robot.address
    await stub.IsReady(pb2.EmptyRequest(), timeout=1.0)
    await fake_robot.stop()
    assert not fake_robot.serving
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.IsReady(pb2.EmptyRequest(), timeout=1.0)
    assert caught.value.code() in (
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    )

    await fake_robot.start()
    assert fake_robot.address == address
    response = await stub.IsReady(pb2.EmptyRequest(), timeout=2.0, wait_for_ready=True)
    assert response.ready


async def test_the_robot_keeps_driving_while_off_the_network(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.speed_scale = 4.0
    cursor = await _cursor(stub)
    started = await stub.StartCommand(_move_to("kitchen"), timeout=1.0)
    await fake_robot.stop()
    await asyncio.sleep(0.3)
    await fake_robot.start()
    result = await stub.GetLastCommandResult(
        pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor)), timeout=3.0, wait_for_ready=True
    )
    assert result.command_id == started.command_id
    assert result.result.success


async def test_the_address_is_known_before_the_fake_serves() -> None:
    """A robot that is off at first: the address is fixed, and serves once started."""
    robot = FakeKachaka()
    async with grpc.aio.insecure_channel(robot.address, options=_OPTIONS) as channel:
        stub = pb2_grpc.KachakaApiStub(channel)
        with pytest.raises(grpc.aio.AioRpcError):
            await stub.IsReady(pb2.EmptyRequest(), timeout=0.5)
        async with robot:
            response = await stub.IsReady(pb2.EmptyRequest(), timeout=2.0, wait_for_ready=True)
            assert response.ready
    assert not robot.serving


async def test_runs_as_a_module_on_a_port() -> None:
    port = FakeKachaka().port
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "openrois_adapter_kachaka.fake_robot",
        "--port",
        str(port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}", options=_OPTIONS) as channel:
            stub = pb2_grpc.KachakaApiStub(channel)
            response = await stub.IsReady(pb2.EmptyRequest(), timeout=10.0, wait_for_ready=True)
            assert response.ready
    finally:
        process.terminate()
        await process.wait()


def test_help_names_the_port() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "openrois_adapter_kachaka.fake_robot", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout
