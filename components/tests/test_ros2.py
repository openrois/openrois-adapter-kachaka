"""The ROS 2 components against stand-ins for rclpy and the Kachaka interfaces."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from openrois.components.core.ros2 import RclpyExecutor
from openrois.engine import Engine

from openrois.components.kachaka import (
    Ros2Navigation,
    Ros2NavigationSettings,
    Ros2SystemInformation,
    Ros2SystemInformationSettings,
)
from tests._fakes import FakeRos2, Location

type Host = Callable[..., Any]

LOCATIONS = "/kachaka/layout/locations/list"
ODOMETRY = "/kachaka/odometry/odometry"


@pytest.fixture
def ros(monkeypatch: pytest.MonkeyPatch) -> FakeRos2:
    fake = FakeRos2()
    fake.install(monkeypatch)
    return fake


async def navigation(host: Host, ros: FakeRos2, **options: Any) -> Any:
    robot = await host(
        "navigation", Ros2Navigation(RclpyExecutor(), Ros2NavigationSettings(**options))
    )
    ros.publish_locations(LOCATIONS, Location("L1", "kitchen"), Location("L2", "dock"))
    result = await robot.call(
        "rois.command.set_parameter",
        component_ref=robot.ref,
        parameters=[{"name": "target_positions", "data_type_ref": "", "value": '["kitchen"]'}],
    )
    assert await robot.completed(result["command_id"]) == "OK"
    await robot.call(
        "rois.event.subscribe",
        event_type="reached_target",
        condition=f"component_ref = '{robot.ref}'",
    )
    return robot


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5.0):
        while not condition():
            await asyncio.sleep(0.005)


async def test_start_sends_a_goal_and_reports_the_arrival(host: Host, ros: FakeRos2) -> None:
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    assert ros.log == ["goal L1"]
    ros.goals[0].finish(success=True)
    assert await robot.completed("c1") == "OK"
    [event] = robot.sent("rois.event.notify_event")
    assert event["results"][0]["value"] == "kitchen"


async def test_a_failed_drive_ends_with_error(host: Host, ros: FakeRos2) -> None:
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    ros.goals[0].finish(success=False)
    assert await robot.completed("c1") == "ERROR"
    assert robot.sent("rois.event.notify_event") == []


async def test_a_refused_goal_fails_the_start(host: Host, ros: FakeRos2) -> None:
    ros.accept = False
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    assert await robot.completed("c1") == "ERROR"


async def test_a_goal_without_an_answer_fails_the_start(host: Host, ros: FakeRos2) -> None:
    ros.answer.clear()
    robot = await navigation(host, ros, goal_timeout=0.05)
    await robot.execute("start", "c1")
    assert await robot.completed("c1") == "ERROR"
    # The goal the Kachaka accepts too late is cancelled.
    ros.answer.set()
    await until(lambda: ros.log == ["goal L1", "cancel L1"])


async def test_stop_cancels_the_goal(host: Host, ros: FakeRos2) -> None:
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    await asyncio.sleep(0.02)
    await robot.execute("stop", "c2")
    assert await robot.completed("c2") == "OK"
    assert await robot.completed("c1") == "ABORT"
    assert ros.log == ["goal L1", "cancel L1"]
    assert robot.sent("rois.event.notify_event") == []


async def test_a_stop_before_the_goal_is_accepted_cancels_it_once_accepted(
    host: Host, ros: FakeRos2
) -> None:
    ros.answer.clear()
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    await robot.execute("stop", "c2")
    assert await robot.completed("c2") == "OK"
    ros.answer.set()
    assert await robot.completed("c1") == "ABORT"
    assert ros.log == ["goal L1", "cancel L1"]


async def test_a_second_stop_before_the_answer_still_cancels_the_goal(
    host: Host, ros: FakeRos2
) -> None:
    ros.answer.clear()
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    await robot.execute("stop", "c2")
    assert await robot.completed("c2") == "OK"
    # The first stop cancelled the start, which now waits for the answer. The second
    # stop cancels it again while it waits.
    await asyncio.sleep(0.02)
    await robot.execute("stop", "c3")
    assert await robot.completed("c1") == "ABORT"
    ros.answer.set()
    await until(lambda: ros.log == ["goal L1", "cancel L1"])


async def test_a_new_start_cancels_the_running_goal_before_it_sends_its_own(
    host: Host, ros: FakeRos2
) -> None:
    robot = await navigation(host, ros)
    await robot.execute("start", "c1")
    await until(lambda: len(ros.goals) == 1)
    await asyncio.sleep(0.02)
    await robot.execute("start", "c2")
    assert await robot.completed("c1") == "ABORT"
    await until(lambda: len(ros.goals) == 2)
    assert ros.log[:3] == ["goal L1", "cancel L1", "goal L1"]
    ros.goals[1].finish(success=True)
    assert await robot.completed("c2") == "OK"


async def test_robot_position_answers_with_the_latest_odometry(
    host: Host, ros: FakeRos2
) -> None:
    robot = await host("system_information", Ros2SystemInformation(RclpyExecutor()))
    before = await robot.call("rois.query.query", query_type="robot_position", condition="")
    assert before["return_code"] == "ERROR"
    ros.publish_odometry(ODOMETRY, 2.0, 3.0, 0.5)
    after = await robot.call("rois.query.query", query_type="robot_position", condition="")
    values = {r["name"]: r["value"] for r in after["results"]}
    assert values["position_data"] == '["2.0000,3.0000,0.5000"]'


async def test_the_components_share_one_executor_that_owns_rclpy(ros: FakeRos2) -> None:
    executor = RclpyExecutor()
    engine = Engine("kachaka")
    engine.add_component("navigation", Ros2Navigation(executor))
    engine.add_component("system_information", Ros2SystemInformation(executor))
    await engine.start()
    # The executor initializes rclpy once, and each component hands it its node.
    assert ros.lifecycle[0] == "init"
    assert sorted(ros.lifecycle[1:]) == ["add openrois_kachaka_nav", "add openrois_kachaka_sysinfo"]
    await engine.stop()
    # Each node leaves the executor before it is destroyed, and rclpy goes down last.
    for node in ("openrois_kachaka_nav", "openrois_kachaka_sysinfo"):
        assert ros.lifecycle.index(f"remove {node}") < ros.lifecycle.index(f"destroy {node}")
    assert ros.lifecycle[-1] == "shutdown"
    assert ros.lifecycle.count("init") == 1


async def test_the_settings_name_the_nodes_and_the_topics(host: Host, ros: FakeRos2) -> None:
    settings = Ros2SystemInformationSettings(node_name="kachaka_2", pose_topic="/k2/odometry")
    robot = await host("system_information", Ros2SystemInformation(RclpyExecutor(), settings))
    assert "add kachaka_2_sysinfo" in ros.lifecycle
    ros.publish_odometry("/k2/odometry", 1.0, 0.0, 0.0)
    result = await robot.call("rois.query.query", query_type="robot_position", condition="")
    assert result["return_code"] == "OK"
