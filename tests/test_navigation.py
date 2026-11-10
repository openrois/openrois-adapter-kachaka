"""Navigation: targets, parameters, stop, time_limit, suspend and resume, and failures."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2

from openrois.components.kachaka import KachakaConnection, KachakaNavigation
from openrois.components.kachaka.navigation import parse_pose
from openrois_adapter_kachaka.fake_robot import (
    CANCELLED,
    LOCATIONS,
    UNKNOWN_LOCATION,
    UNREACHABLE,
    FakeKachaka,
)
from tests.conftest import Host, Robot


def connection_to(fake: FakeKachaka, **settings: Any) -> KachakaConnection:
    """A connection with short durations: a robot that does not answer is lost at once."""
    options: dict[str, Any] = {"timeout": 1.0, "retry": 0.05, "lost_after": 0.0}
    options.update(settings)
    return KachakaConnection(address=fake.address, **options)


async def navigation(
    host: Host, fake: FakeKachaka, *, auto_homing: bool | None = None, **settings: Any
) -> Robot:
    component = KachakaNavigation(connection_to(fake, **settings), auto_homing=auto_homing)
    robot = await host({"navigation": component})
    await robot.subscribe("navigation", "reached_target")
    return robot


async def wait_for(condition: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def driving(fake: FakeKachaka) -> str:
    """Wait until the robot runs a command, and return its id."""
    await wait_for(lambda: fake.running_command_id is not None)
    command_id = fake.running_command_id
    assert command_id is not None
    return command_id


def location(location_id: str) -> tuple[float, float, float]:
    [pose] = [pose for lid, _, pose, _ in LOCATIONS if lid == location_id]
    return pose


def started(fake: FakeKachaka) -> list[pb2.Command]:
    """The commands the robot was asked to start, oldest first."""
    return [request.command for request in fake.requests("StartCommand")]


def to_location(location_id: str) -> pb2.Command:
    return pb2.Command(
        move_to_location_command=pb2.MoveToLocationCommand(target_location_id=location_id)
    )


# -- The profile and the targets -----------------------------------------------------------


def test_serves_the_navigation_profile() -> None:
    profile = KachakaNavigation.rois_profile()
    assert profile.identifier.authority == "OMG"
    assert profile.identifier.code == "Navigation"
    assert profile.function == "actuation"
    assert [c.name for c in profile.command_profiles] == ["start", "stop", "suspend", "resume"]
    assert [q.name for q in profile.query_profiles] == ["component_status"]
    assert [e.name for e in profile.event_profiles] == ["reached_target"]
    parameters = {p.name: p.default_value for p in profile.parameter_profiles}
    assert parameters == {"target_positions": None, "time_limit": "0", "routing_policy": "time"}


@pytest.mark.parametrize(
    ("text", "pose"),
    [
        ("1,2,3", (1.0, 2.0, 3.0)),
        (" -1.5 , 0.25,-3.1416 ", (-1.5, 0.25, -3.1416)),
        ("1e1,0,0", (10.0, 0.0, 0.0)),
    ],
)
def test_a_pose_is_x_y_theta(text: str, pose: tuple[float, float, float]) -> None:
    assert parse_pose(text) == pose


@pytest.mark.parametrize("text", ["kitchen", "1,2", "1,2,3,4", "1,2,x", "nan,0,0", "1,inf,0", ""])
def test_other_text_is_not_a_pose(text: str) -> None:
    assert parse_pose(text) is None


async def test_start_without_targets_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.run("navigation", "start") == "ERROR"
    assert "Set target_positions first" in caplog.text
    assert started(fake_robot) == []


async def test_a_location_by_its_name(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["Meeting room"]) == "OK"
    assert await robot.run("navigation", "start") == "OK"
    assert started(fake_robot) == [to_location("meeting_room")]
    assert fake_robot.pose == location("meeting_room")
    assert robot.events() == [{"target": "Meeting room", "is_final_target": "true"}]
    [request] = fake_robot.requests("StartCommand")
    assert request.cancel_all
    assert request.title == "OpenRoIS: Meeting room"


async def test_a_location_by_its_id(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["kitchen"]) == "OK"
    assert await robot.run("navigation", "start") == "OK"
    assert started(fake_robot) == [to_location("kitchen")]
    assert fake_robot.pose == location("kitchen")


async def test_the_charger_is_the_location_home(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.x, fake_robot.y = 1.0, 1.0
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["home"]) == "OK"
    assert await robot.run("navigation", "start") == "OK"
    assert started(fake_robot) == [to_location("home")]
    assert fake_robot.pose == location("home")


async def test_a_pose_on_the_map(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["1.5,-0.5,0.25"]) == "OK"
    assert await robot.run("navigation", "start") == "OK"
    [drive] = started(fake_robot)
    assert drive.WhichOneof("command") == "move_to_pose_command"
    pose = drive.move_to_pose_command
    assert (pose.x, pose.y, pose.yaw) == (1.5, -0.5, 0.25)
    assert fake_robot.pose == (1.5, -0.5, 0.25)
    assert robot.events() == [{"target": "1.5,-0.5,0.25", "is_final_target": "true"}]


async def test_targets_are_driven_in_turn(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    targets = ["Kitchen", "1,1,0", "meeting_room"]
    assert await robot.set("navigation", target_positions=targets) == "OK"
    assert await robot.run("navigation", "start") == "OK"
    assert [c.WhichOneof("command") for c in started(fake_robot)] == [
        "move_to_location_command",
        "move_to_pose_command",
        "move_to_location_command",
    ]
    assert robot.events() == [
        {"target": "Kitchen", "is_final_target": "false"},
        {"target": "1,1,0", "is_final_target": "false"},
        {"target": "meeting_room", "is_final_target": "true"},
    ]
    assert fake_robot.pose == location("meeting_room")
    # The targets are resolved once, before the first drive.
    assert len(fake_robot.requests("GetLocations")) == 2


# -- set_parameter -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"target_positions": ["attic"]},
        {"target_positions": ["kitchen", "1,2"]},
        {"target_positions": []},
        {"target_positions": [" "]},
        {"time_limit": -1},
        {"routing_policy": "scenic"},
    ],
)
async def test_set_parameter_refuses_bad_values(
    host: Host, fake_robot: FakeKachaka, values: dict[str, object]
) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", **values) == "BAD_PARAMETER"
    result = await robot.call("rois.command.get_parameter", component_ref=robot.refs["navigation"])
    stored = {p["name"]: p["value"] for p in result["parameters"]}
    assert stored == {"time_limit": "0", "routing_policy": "time"}


async def test_a_refused_value_keeps_the_old_one(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["kitchen"]) == "OK"
    assert await robot.set("navigation", target_positions=["attic"]) == "BAD_PARAMETER"
    assert await robot.run("navigation", "start") == "OK"
    assert fake_robot.pose == location("kitchen")


async def test_set_parameter_names_the_unknown_targets(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.set("navigation", target_positions=["kitchen", "attic"]) == "BAD_PARAMETER"
    assert "The Kachaka knows no location 'attic'" in caplog.text
    assert "Charger, Kitchen, Meeting room" in caplog.text


async def test_set_parameter_accepts_good_values(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    values = {
        "target_positions": ["Kitchen", "meeting_room", "home", "0.5,0.5,0"],
        "time_limit": 30,
        "routing_policy": "distance",
    }
    assert await robot.set("navigation", **values) == "OK"
    assert await robot.set("navigation", routing_policy="time", time_limit=0) == "OK"


async def test_poses_are_checked_without_the_robot(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    asked = len(fake_robot.requests("GetLocations"))
    assert await robot.set("navigation", target_positions=["1,2,0", "-1,0,3.14"]) == "OK"
    assert len(fake_robot.requests("GetLocations")) == asked


async def test_targets_are_left_to_start_while_the_robot_does_not_answer(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot, lost_after=30.0)
    await fake_robot.stop()
    # The robot is not lost yet, so the engine takes the values, unchecked.
    assert await robot.set("navigation", target_positions=["attic"]) == "OK"
    await fake_robot.start()
    assert await robot.run("navigation", "start") == "ERROR"
    assert started(fake_robot) == []


async def test_a_refused_target_inside_execute_is_an_error(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    ref = robot.refs["navigation"]
    unit = {
        "component_ref": ref,
        "command_type": "set_parameter",
        "arguments": [{"name": "target_positions", "data_type_ref": "", "value": '["attic"]'}],
    }
    result = await robot.call(
        "rois.command.execute",
        command_unit_list=[unit, {"component_ref": ref, "command_type": "start"}],
    )
    assert result["return_code"] == "OK"
    first, second = (u["command_id"] for u in result["command_unit_list"])
    assert await robot.completed(first) == "ERROR"
    assert await robot.completed(second) == "ABORT"
    assert started(fake_robot) == []


async def test_set_parameter_and_start_in_one_execute(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    ref = robot.refs["navigation"]
    unit = {
        "component_ref": ref,
        "command_type": "set_parameter",
        "arguments": [{"name": "target_positions", "data_type_ref": "", "value": '["kitchen"]'}],
    }
    result = await robot.call(
        "rois.command.execute",
        command_unit_list=[unit, {"component_ref": ref, "command_type": "start"}],
    )
    first, second = (u["command_id"] for u in result["command_unit_list"])
    assert await robot.completed(first) == "OK"
    assert await robot.completed(second) == "OK"
    assert fake_robot.pose == location("kitchen")


# -- Failures ------------------------------------------------------------------------------


async def test_a_failed_drive_is_an_error_with_the_code_and_title(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen", "meeting_room"])
    fake_robot.fail_next(UNREACHABLE)
    assert await robot.run("navigation", "start") == "ERROR"
    assert (
        f"The drive to kitchen failed: error {UNREACHABLE} "
        "(The robot could not reach the destination)" in caplog.text
    )
    assert robot.events() == []
    assert len(started(fake_robot)) == 1


async def test_a_refused_start_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"])
    fake_robot.fail_next(UNKNOWN_LOCATION, on_start=True)
    assert await robot.run("navigation", "start") == "ERROR"
    assert f"error {UNKNOWN_LOCATION} (The location does not exist)" in caplog.text


async def test_a_robot_lost_during_the_drive_is_an_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    await fake_robot.stop()
    assert await robot.completed(start) == "ERROR"
    assert "the robot may still be moving" in caplog.text


async def test_a_command_of_another_client_ends_the_start_with_error(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    other = fake_robot.preempt()
    assert await robot.completed(start) == "ERROR"
    assert f"error {CANCELLED} (The command was cancelled)" in caplog.text
    # The start leaves the other command running.
    assert fake_robot.running_command_id == other
    assert fake_robot.requests("CancelCommand") == []


async def test_a_result_skipped_by_the_cursor_comes_from_the_history(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    ours = await driving(fake_robot)
    fake_robot.preempt(finish=True)
    assert await robot.completed(start) == "ERROR"
    assert fake_robot.requests("GetHistoryList")
    assert fake_robot.history[1].id == ours
    assert f"error {CANCELLED} (The command was cancelled)" in caplog.text


async def test_a_drive_without_a_result_was_preempted(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    fake_robot.preempt(finish=True)
    fake_robot.history.clear()
    assert await robot.completed(start) == "ERROR"
    assert "another command replaced it" in caplog.text


# -- stop and the other aborts -------------------------------------------------------------


async def test_stop_cancels_the_drive(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room", "kitchen"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.run("navigation", "stop") == "OK"
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1
    pose = fake_robot.pose
    await asyncio.sleep(0.3)
    assert fake_robot.pose == pose
    assert len(started(fake_robot)) == 1
    assert robot.events() == []


async def test_stop_cancels_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"])
    assert await robot.run("navigation", "start") == "OK"
    fake_robot.speed_scale = 1.0
    fake_robot.preempt(to_location("meeting_room"))
    assert await robot.run("navigation", "stop") == "OK"
    assert fake_robot.running_command_id is None


async def test_a_new_start_aborts_the_running_one(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    first = await robot.execute("navigation", "start")
    old = await driving(fake_robot)
    fake_robot.speed_scale = 20.0
    await robot.set("navigation", target_positions=["kitchen"])
    second = await robot.execute("navigation", "start")
    assert await robot.completed(first) == "ABORT"
    assert await robot.completed(second) == "OK"
    assert fake_robot.pose == location("kitchen")
    assert next(h for h in fake_robot.history if h.id == old).error_code == CANCELLED
    assert robot.events() == [{"target": "kitchen", "is_final_target": "true"}]


async def test_the_engine_stopping_cancels_the_drive(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    await robot.execute("navigation", "start")
    ours = await driving(fake_robot)
    await robot.engine.stop()
    assert fake_robot.running_command_id is None
    assert fake_robot.history[0].id == ours
    assert fake_robot.history[0].error_code == CANCELLED
    assert len(fake_robot.requests("CancelCommand")) == 1


async def test_the_engine_stopping_leaves_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.run("navigation", "suspend") == "OK"
    assert fake_robot.running_command_id is None
    cancels = len(fake_robot.requests("CancelCommand"))
    other = fake_robot.preempt(to_location("kitchen"))
    await robot.engine.stop()
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id == other
    assert len(fake_robot.requests("CancelCommand")) == cancels


async def test_a_release_cancels_only_the_own_drive(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    released = await robot.call("rois.command.release", component_ref=robot.refs["navigation"])
    assert released["return_code"] == "OK"
    assert await robot.completed(start) == "ABORT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1


# -- time_limit ----------------------------------------------------------------------------


async def test_time_limit_ends_the_start_with_timeout(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"], time_limit=1)
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.completed(start, timeout=3.0) == "TIMEOUT"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1
    assert fake_robot.history[0].error_code == CANCELLED
    assert fake_robot.pose != location("meeting_room")
    assert robot.events() == []


async def test_a_drive_within_the_time_limit_is_ok(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"], time_limit=5)
    assert await robot.run("navigation", "start") == "OK"
    assert fake_robot.requests("CancelCommand") == []


async def test_a_suspension_does_not_count_towards_the_time_limit(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"], time_limit=1)
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.run("navigation", "suspend") == "OK"
    await asyncio.sleep(1.2)
    assert await robot.run("navigation", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert fake_robot.pose == location("kitchen")


# -- suspend and resume --------------------------------------------------------------------


async def test_suspend_halts_the_robot_and_resume_drives_on(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 2.0
    await robot.set("navigation", target_positions=["kitchen", "home"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.run("navigation", "suspend") == "OK"
    assert fake_robot.running_command_id is None
    assert len(fake_robot.requests("CancelCommand")) == 1
    pose = fake_robot.pose
    await asyncio.sleep(0.3)
    assert fake_robot.pose == pose
    assert len(started(fake_robot)) == 1

    fake_robot.speed_scale = 20.0
    assert await robot.run("navigation", "resume") == "OK"
    assert await robot.completed(start) == "OK"
    assert started(fake_robot) == [
        to_location("kitchen"),
        to_location("kitchen"),
        to_location("home"),
    ]
    assert robot.events() == [
        {"target": "kitchen", "is_final_target": "false"},
        {"target": "home", "is_final_target": "true"},
    ]


async def test_suspend_leaves_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    await robot.set("navigation", target_positions=["kitchen"])
    assert await robot.run("navigation", "start") == "OK"
    fake_robot.speed_scale = 1.0
    other = fake_robot.preempt(to_location("meeting_room"))
    assert await robot.run("navigation", "suspend") == "OK"
    assert fake_robot.running_command_id == other
    assert await robot.run("navigation", "resume") == "OK"


async def test_stop_while_suspended_aborts_the_start(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await navigation(host, fake_robot)
    fake_robot.speed_scale = 1.0
    await robot.set("navigation", target_positions=["meeting_room"])
    start = await robot.execute("navigation", "start")
    await driving(fake_robot)
    assert await robot.run("navigation", "suspend") == "OK"
    assert await robot.run("navigation", "stop") == "OK"
    assert await robot.completed(start) == "ABORT"
    await asyncio.sleep(0.2)
    assert len(started(fake_robot)) == 1
    assert fake_robot.running_command_id is None


async def test_a_suspend_during_the_start_of_a_drive_halts_it(
    host: Host, fake_robot: FakeKachaka
) -> None:
    component = KachakaNavigation(connection_to(fake_robot))
    await host({"navigation": component})
    fake_robot.speed_scale = 1.0
    # The suspend arrives after StartCommand left, before the robot answered.
    await component.suspend()
    component._started(fake_robot.preempt(to_location("meeting_room")))
    await wait_for(lambda: fake_robot.running_command_id is None)
    assert len(fake_robot.requests("CancelCommand")) == 1
    await component.resume()


async def test_suspend_and_resume_before_a_start_do_nothing(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await navigation(host, fake_robot)
    assert await robot.run("navigation", "suspend") == "OK"
    assert await robot.run("navigation", "resume") == "OK"
    assert await robot.run("navigation", "stop") == "OK"
    assert fake_robot.requests("CancelCommand") == []


# -- auto_homing ---------------------------------------------------------------------------


async def test_auto_homing_none_leaves_the_robot_setting(
    host: Host, fake_robot: FakeKachaka
) -> None:
    await navigation(host, fake_robot)
    await asyncio.sleep(0.2)
    assert fake_robot.requests("SetAutoHomingEnabled") == []
    assert fake_robot.auto_homing


@pytest.mark.parametrize("enable", [False, True])
async def test_auto_homing_is_set_each_time_the_robot_comes_up(
    host: Host, fake_robot: FakeKachaka, enable: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openrois.components.kachaka.navigation")
    fake_robot.auto_homing = not enable
    robot = await navigation(host, fake_robot, auto_homing=enable)
    await wait_for(lambda: fake_robot.auto_homing is enable)
    assert [r.enable for r in fake_robot.requests("SetAutoHomingEnabled")] == [enable]
    assert f"Set auto homing of the Kachaka to {'on' if enable else 'off'}" in caplog.text

    # The robot restarts with its own setting.
    await fake_robot.stop()
    fake_robot.auto_homing = not enable
    async with asyncio.timeout(3.0):
        while await robot.status("navigation") != "ERROR":
            await asyncio.sleep(0.01)
    await fake_robot.start()
    await wait_for(lambda: fake_robot.auto_homing is enable)
    assert [r.enable for r in fake_robot.requests("SetAutoHomingEnabled")] == [enable, enable]


async def test_auto_homing_before_the_robot_is_set_once_it_comes_up(host: Host) -> None:
    fake = FakeKachaka(speed_scale=20.0)
    try:
        component = KachakaNavigation(connection_to(fake), auto_homing=False)
        robot = await host({"navigation": component})
        assert await robot.status("navigation") == "ERROR"
        await fake.start()
        await wait_for(lambda: fake.auto_homing is False)
        assert await robot.status("navigation") == "READY"
    finally:
        await fake.close()
