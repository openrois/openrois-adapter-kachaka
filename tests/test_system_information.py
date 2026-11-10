"""SystemInformation: the robot's pose on its map, and the status of the engine."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

from openrois.interfaces.common import ComponentStatus

from openrois.components.kachaka import KachakaConnection, KachakaSystemInformation
from openrois_adapter_kachaka.fake_robot import FakeKachaka
from tests.conftest import Host, Robot


async def system_information(host: Host, fake: FakeKachaka, **settings: float) -> Robot:
    options = {"timeout": 1.0, "retry": 0.05, "lost_after": 0.0, **settings}
    connection = KachakaConnection(address=fake.address, **options)
    return await host({"system_information": KachakaSystemInformation(connection)})


def test_serves_robot_position_and_engine_status_only() -> None:
    profile = KachakaSystemInformation.rois_profile()
    assert profile.identifier.authority == "OMG"
    assert profile.identifier.code == "SystemInformation"
    assert [q.name for q in profile.query_profiles] == ["robot_position", "engine_status"]
    assert profile.command_profiles == []
    assert profile.event_profiles == []


async def test_robot_position_is_the_pose_on_the_map(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.x, fake_robot.y, fake_robot.theta = 1.23456, -2.5, math.pi / 2
    robot = await system_information(host, fake_robot)
    before = datetime.now(UTC)
    values = await robot.query("system_information", "robot_position")
    assert values["position_data"] == '["1.2346,-2.5000,1.5708"]'
    assert values["robot_ref"] == '["kachaka"]'
    assert before <= datetime.fromisoformat(values["timestamp"]) <= datetime.now(UTC)
    [request] = fake_robot.requests("GetRobotPose")
    # The latest pose at once, rather than a long-poll for the next one.
    assert request.metadata.cursor == 0


async def test_robot_position_follows_the_robot(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await system_information(host, fake_robot)
    values = await robot.query("system_information", "robot_position")
    assert values["position_data"] == '["0.0000,0.0000,0.0000"]'
    fake_robot.x, fake_robot.y, fake_robot.theta = -1.0, 2.0, -0.25
    values = await robot.query("system_information", "robot_position")
    assert values["position_data"] == '["-1.0000,2.0000,-0.2500"]'


async def test_robot_ref_is_the_engine_id(host: Host, fake_robot: FakeKachaka) -> None:
    connection = KachakaConnection(address=fake_robot.address, retry=0.05)
    robot = await host({"system_information": KachakaSystemInformation(connection)}, "kachaka_2")
    values = await robot.query("system_information", "robot_position")
    assert values["robot_ref"] == '["kachaka_2"]'


async def test_engine_status_follows_the_engine(host: Host, fake_robot: FakeKachaka) -> None:
    before = datetime.now(UTC)
    robot = await system_information(host, fake_robot)
    status = await robot.query("system_information", "engine_status")
    assert status["status"] == "READY"
    assert before <= datetime.fromisoformat(status["operable_time"]) <= datetime.now(UTC)

    robot.engine.raise_status(ComponentStatus.WARNING, "The battery is low")
    status = await robot.query("system_information", "engine_status")
    assert status["status"] == "WARNING"
    robot.engine.raise_status(ComponentStatus.ERROR, "The engine failed")
    assert (await robot.query("system_information", "engine_status"))["status"] == "ERROR"
    robot.engine.clear_status()
    assert (await robot.query("system_information", "engine_status"))["status"] == "READY"


async def test_component_status_is_not_served(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await system_information(host, fake_robot)
    condition = f"component_ref = '{robot.refs['system_information']}'"
    result = await robot.call(
        "rois.query.query", query_type="component_status", condition=condition
    )
    assert result["return_code"] == "UNSUPPORTED"


async def test_queries_are_refused_while_the_robot_is_gone(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await system_information(host, fake_robot)
    await fake_robot.stop()
    async with asyncio.timeout(3.0):
        while not robot.sent("rois.system.notify_error"):
            await asyncio.sleep(0.01)
    [error] = robot.sent("rois.system.notify_error")
    assert error["error_type"] == "COMPONENT_NOT_RESPONDING"
    condition = f"component_ref = '{robot.refs['system_information']}'"
    result = await robot.call("rois.query.query", query_type="robot_position", condition=condition)
    assert result["return_code"] == "ERROR"

    await fake_robot.start()
    async with asyncio.timeout(3.0):
        while True:
            result = await robot.call(
                "rois.query.query", query_type="robot_position", condition=condition
            )
            if result["return_code"] == "OK":
                break
            await asyncio.sleep(0.01)
