"""The shared connection to the Kachaka: status, outages, deadlines and commands."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2
from openrois.components.core import CommandFailed, Component, command, component
from openrois.interfaces.components import NAVIGATION_PROFILE
from openrois.interfaces.service import CompletedStatus

from openrois.components.kachaka import CommandPreempted, KachakaConnection
from openrois.components.kachaka.connection import INSTALL_HINT, parse_address
from openrois_adapter_kachaka.fake_robot import (
    CANCELLED,
    ERROR_CODES,
    INVALID_COMMAND,
    LOCATIONS,
    UNREACHABLE,
    FakeKachaka,
)
from tests.conftest import Host, Robot


@component(NAVIGATION_PROFILE)
class Driver(Component):
    """A component for these tests only: ``start`` drives the robot to the kitchen."""

    def __init__(self, connection: KachakaConnection) -> None:
        self.connection = self.uses(connection)
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1

    @command("start")
    async def start(self) -> None:
        result = await self.connection.run_command(move_to("kitchen"), title="test")
        if not result.success:
            raise CommandFailed(CompletedStatus.ERROR, self.connection.describe(result.error_code))


class CountingConnection(KachakaConnection):
    """A connection that counts how often the engine opens and closes it."""

    opened = 0
    closed = 0

    async def open(self) -> None:
        self.opened += 1
        await super().open()

    async def close(self) -> None:
        self.closed += 1
        await super().close()


def move_to(location_id: str) -> pb2.Command:
    return pb2.Command(
        move_to_location_command=pb2.MoveToLocationCommand(target_location_id=location_id)
    )


def connection_to(robot: FakeKachaka, **settings: Any) -> KachakaConnection:
    """A connection with short durations: a robot that does not answer is lost at once."""
    options: dict[str, Any] = {"timeout": 1.0, "retry": 0.05, "lost_after": 0.0}
    options.update(settings)
    return KachakaConnection(address=robot.address, **options)


async def status_is(robot: Robot, name: str, status: str, timeout: float = 3.0) -> None:
    """Wait until component_status of a component reports ``status``."""
    async with asyncio.timeout(timeout):
        while await robot.status(name) != status:
            await asyncio.sleep(0.01)


async def wait_for(condition: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def location(location_id: str) -> tuple[float, float, float]:
    [pose] = [pose for lid, _, pose, _ in LOCATIONS if lid == location_id]
    return pose


async def test_three_components_open_one_connection(host: Host, fake_robot: FakeKachaka) -> None:
    connection = CountingConnection(address=fake_robot.address, timeout=1.0, retry=0.05)
    drivers = {name: Driver(connection) for name in ("first", "second", "third")}
    robot = await host(drivers)
    assert connection.opened == 1
    assert connection.is_open and connection.is_up
    for name, driver in drivers.items():
        assert driver.connects == 1
        assert await robot.status(name) == "READY"
    assert await robot.run("second", "start") == "OK"
    assert fake_robot.pose == location("kitchen")
    await robot.engine.stop()
    assert connection.closed == 1
    assert not connection.is_open


async def test_close_closes_the_channel(host: Host, fake_robot: FakeKachaka) -> None:
    connection = connection_to(fake_robot)
    robot = await host({"navigation": Driver(connection)})
    await robot.engine.stop()
    with pytest.raises(RuntimeError, match="not open"):
        await connection.call("IsReady", pb2.EmptyRequest())
    # The watchdog is gone with the channel.
    checks = len(fake_robot.requests("IsReady"))
    await asyncio.sleep(0.2)
    assert len(fake_robot.requests("IsReady")) == checks


async def test_the_adapter_starts_before_the_robot(host: Host) -> None:
    fake = FakeKachaka(speed_scale=20.0)
    try:
        connection = connection_to(fake)
        driver = Driver(connection)
        robot = await host({"navigation": driver})
        assert connection.is_open and not connection.is_up
        assert driver.connects == 1
        assert await robot.status("navigation") == "ERROR"
        assert await robot.run("navigation", "start") == "ERROR"
        await fake.start()
        await status_is(robot, "navigation", "READY")
        assert await robot.run("navigation", "start") == "OK"
    finally:
        await fake.close()


async def test_an_outage_raises_one_error_per_loss_on_every_component(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot)
    robot = await host({"first": Driver(connection), "second": Driver(connection)})
    assert robot.sent("rois.system.notify_error") == []

    await fake_robot.stop()
    await status_is(robot, "first", "ERROR")
    assert await robot.status("second") == "ERROR"
    await asyncio.sleep(0.3)  # Several checks, each of which still finds the robot gone.
    errors = robot.sent("rois.system.notify_error")
    assert [e["error_type"] for e in errors] == ["COMPONENT_NOT_RESPONDING"] * 2
    detail = await robot.call("rois.system.get_error_detail", error_id=errors[0]["error_id"])
    values = {r["name"]: r["value"] for r in detail["results"]}
    assert values["message"] == f"The Kachaka at {fake_robot.address} does not answer"
    assert values["status"] == "ERROR"
    assert await robot.run("first", "start") == "ERROR"

    await fake_robot.start()
    await status_is(robot, "first", "READY")
    assert await robot.status("second") == "READY"
    assert len(robot.sent("rois.system.notify_error")) == 2
    assert await robot.run("first", "start") == "OK"

    await fake_robot.stop()
    await status_is(robot, "second", "ERROR")
    await asyncio.sleep(0.2)
    assert len(robot.sent("rois.system.notify_error")) == 4


async def test_a_pause_shorter_than_lost_after_raises_nothing(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot, lost_after=2.0)
    robot = await host({"navigation": Driver(connection)})
    await fake_robot.stop()
    await asyncio.sleep(0.4)
    assert await robot.status("navigation") == "READY"
    await fake_robot.start()
    await asyncio.sleep(0.4)
    assert await robot.status("navigation") == "READY"
    assert robot.sent("rois.system.notify_error") == []


async def test_a_pause_longer_than_lost_after_is_an_error(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot, lost_after=0.5)
    robot = await host({"navigation": Driver(connection)})
    stopped = time.monotonic()
    await fake_robot.stop()
    await status_is(robot, "navigation", "ERROR")
    assert time.monotonic() - stopped >= 0.5
    assert len(robot.sent("rois.system.notify_error")) == 1


async def test_a_robot_that_is_not_ready_is_a_warning(host: Host, fake_robot: FakeKachaka) -> None:
    connection = connection_to(fake_robot)
    robot = await host({"navigation": Driver(connection)})
    fake_robot.set_ready(False)
    await status_is(robot, "navigation", "WARNING")
    assert not connection.is_up
    [error] = robot.sent("rois.system.notify_error")
    detail = await robot.call("rois.system.get_error_detail", error_id=error["error_id"])
    values = {r["name"]: r["value"] for r in detail["results"]}
    assert values["message"] == (
        f"The Kachaka at {fake_robot.address} is not ready to accept commands"
    )
    fake_robot.set_ready(True)
    await status_is(robot, "navigation", "READY")
    assert connection.is_up


async def test_without_the_robot_extra_the_components_are_in_error(
    host: Host, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "grpc", None)
    connection = KachakaConnection(address="127.0.0.1")
    driver = Driver(connection)
    robot = await host({"navigation": driver}, bind=False)
    assert await robot.status("navigation") == "ERROR"
    assert driver.connects == 0
    assert not connection.is_open
    assert INSTALL_HINT in caplog.text
    assert 'pip install "openrois-adapter-kachaka[robot]"' in INSTALL_HINT


async def test_an_unresolvable_host_is_a_status(host: Host) -> None:
    # .invalid never resolves (RFC 2606). The resolver may also time out, which is the
    # same to the connection.
    connection = KachakaConnection(address="kachaka.invalid", timeout=1.0, retry=0.05)
    robot = await host({"navigation": Driver(connection)})
    assert connection.is_open
    assert await robot.status("navigation") == "ERROR"
    await asyncio.sleep(0.2)  # The watchdog keeps checking without failing.
    assert await robot.status("navigation") == "ERROR"
    await robot.engine.stop()
    assert not connection.is_open


async def test_calls_have_a_deadline_and_wake_the_watchdog(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot, timeout=0.3, retry=30.0)
    await host({"navigation": Driver(connection)})
    current = await connection.call("GetRobotPose", pb2.GetRequest())
    # A long-poll from the latest cursor waits until the robot moves, which it does not.
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=current.metadata.cursor))
    checks = len(fake_robot.requests("IsReady"))
    started = time.monotonic()
    with pytest.raises(ConnectionError, match="DEADLINE_EXCEEDED"):
        await connection.call("GetRobotPose", request)
    assert 0.25 <= time.monotonic() - started < 1.0
    started = time.monotonic()
    with pytest.raises(ConnectionError):
        await connection.call("GetRobotPose", request, timeout=0.1)
    assert time.monotonic() - started < 0.3
    # The watchdog, which waits 30 s between checks, checks the robot at once.
    await wait_for(lambda: len(fake_robot.requests("IsReady")) > checks, timeout=1.0)


async def test_a_call_to_a_robot_that_is_gone_is_a_connection_error(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot)
    await host({"navigation": Driver(connection)})
    await fake_robot.stop()
    with pytest.raises(ConnectionError, match="UNAVAILABLE"):
        await connection.call("GetLocations", pb2.GetRequest())


async def test_a_call_the_robot_refuses_is_a_runtime_error(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot)
    await host({"navigation": Driver(connection)})
    # The fake does not implement this RPC.
    with pytest.raises(RuntimeError, match="UNIMPLEMENTED"):
        await connection.call("GetRobotSerialNumber", pb2.GetRequest())


async def test_error_codes_are_described_with_the_robot_titles(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = connection_to(fake_robot)
    await host({"navigation": Driver(connection)})
    assert connection.title(UNREACHABLE) == ERROR_CODES[UNREACHABLE]
    assert connection.describe(UNREACHABLE) == (
        f"error {UNREACHABLE} (The robot could not reach the destination)"
    )
    assert connection.title(42) is None
    assert connection.describe(42) == "error 42"


async def test_a_failed_drive_ends_the_command_with_the_title(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    connection = connection_to(fake_robot)
    robot = await host({"navigation": Driver(connection)})
    fake_robot.fail_next(UNREACHABLE)
    assert await robot.run("navigation", "start") == "ERROR"
    assert f"error {UNREACHABLE} (The robot could not reach the destination)" in caplog.text


async def test_errors_the_robot_reports_are_logged_once(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="openrois.components.kachaka.connection")
    connection = connection_to(fake_robot)
    await host({"navigation": Driver(connection)})
    fake_robot.set_errors([UNREACHABLE])
    message = f"reports error {UNREACHABLE} (The robot could not reach the destination)"
    await wait_for(lambda: message in caplog.text)
    await asyncio.sleep(0.2)
    assert caplog.text.count(message) == 1


async def test_hooks_run_each_time_the_robot_comes_up(host: Host, fake_robot: FakeKachaka) -> None:
    connection = connection_to(fake_robot)
    runs: list[str] = []
    failures = [RuntimeError("not yet")]

    async def setting() -> None:
        runs.append("setting")

    async def flaky() -> None:
        if failures:
            raise failures.pop()
        runs.append("flaky")

    connection.on_connect(setting)
    connection.on_connect(flaky)
    robot = await host({"navigation": Driver(connection)})
    assert runs == ["setting"]
    await wait_for(lambda: runs == ["setting", "flaky"])  # Tried again at the next check.
    await asyncio.sleep(0.2)
    assert runs == ["setting", "flaky"]  # Not on every check.

    await fake_robot.stop()
    await status_is(robot, "navigation", "ERROR")
    await fake_robot.start()
    await status_is(robot, "navigation", "READY")
    await wait_for(lambda: len(runs) == 4)
    assert sorted(runs[2:]) == ["flaky", "setting"]

    late: list[bool] = []

    async def added_later() -> None:
        late.append(True)

    connection.on_connect(added_later)
    await wait_for(lambda: late == [True])


async def test_a_channel_that_stays_down_is_replaced(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openrois.components.kachaka.connection")
    connection = connection_to(fake_robot)
    # A reconnect backoff of ten minutes: the channel would not try again in this test.
    connection._backoff_ms = 600_000
    robot = await host({"navigation": Driver(connection)})
    await fake_robot.stop()
    await status_is(robot, "navigation", "ERROR")
    await asyncio.sleep(0.2)
    await fake_robot.start()
    await status_is(robot, "navigation", "READY")
    assert "the channel fails. Opening a new one" in caplog.text
    assert await robot.run("navigation", "start") == "OK"


# -- run_command ---------------------------------------------------------------------------


async def opened(host: Host, robot: FakeKachaka, **settings: Any) -> KachakaConnection:
    connection = connection_to(robot, **settings)
    await host({"navigation": Driver(connection)})
    return connection


async def test_run_command_waits_for_the_result(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    started: list[str] = []
    result = await connection.run_command(
        move_to("meeting_room"), title="to the meeting room", started=started.append
    )
    assert result.success
    assert fake_robot.pose == location("meeting_room")
    [request] = fake_robot.requests("StartCommand")
    assert request.cancel_all and request.title == "to the meeting room"
    assert started == [fake_robot.history[0].id]


async def test_run_command_returns_a_failed_result(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    fake_robot.fail_next(UNREACHABLE)
    result = await connection.run_command(move_to("kitchen"))
    assert not result.success
    assert result.error_code == UNREACHABLE


async def test_run_command_returns_a_refused_start(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    fake_robot.fail_next(INVALID_COMMAND, on_start=True)
    started: list[str] = []
    result = await connection.run_command(move_to("kitchen"), started=started.append)
    assert not result.success
    assert result.error_code == INVALID_COMMAND
    assert started == []
    assert len(fake_robot.requests("GetLastCommandResult")) == 1  # Only the cursor.


async def test_a_command_of_another_client_ends_ours(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    task = asyncio.create_task(connection.run_command(move_to("meeting_room")))
    await wait_for(lambda: fake_robot.running_command_id is not None)
    fake_robot.preempt()
    result = await task
    assert not result.success
    assert result.error_code == CANCELLED


async def test_a_result_skipped_by_the_cursor_comes_from_the_history(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = await opened(host, fake_robot)
    started: list[str] = []
    task = asyncio.create_task(connection.run_command(move_to("kitchen"), started=started.append))
    await wait_for(lambda: fake_robot.running_command_id is not None and bool(started))
    # The other command ends before our long-poll reads the result of ours.
    fake_robot.preempt(finish=True)
    result = await task
    assert (result.success, result.error_code) == (False, CANCELLED)
    assert fake_robot.requests("GetHistoryList")
    assert fake_robot.history[1].id == started[0]


async def test_a_command_without_a_result_was_preempted(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = await opened(host, fake_robot)
    task = asyncio.create_task(connection.run_command(move_to("kitchen")))
    await wait_for(lambda: fake_robot.running_command_id is not None)
    fake_robot.preempt(finish=True)
    fake_robot.history.clear()
    with pytest.raises(CommandPreempted, match="another command replaced it"):
        await task


async def test_a_cancelled_run_cancels_its_command(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    fake_robot.speed_scale = 1.0
    task = asyncio.create_task(connection.run_command(move_to("meeting_room")))
    await wait_for(lambda: fake_robot.running_command_id is not None)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(fake_robot.requests("CancelCommand")) == 1
    assert fake_robot.running_command_id is None
    assert fake_robot.history[0].error_code == CANCELLED


async def test_cancel_leaves_a_command_of_another_client(
    host: Host, fake_robot: FakeKachaka
) -> None:
    connection = await opened(host, fake_robot)
    fake_robot.speed_scale = 1.0
    other = fake_robot.preempt(move_to("meeting_room"))
    assert not await connection.cancel("not-ours")
    assert fake_robot.requests("CancelCommand") == []
    assert fake_robot.running_command_id == other
    assert await connection.cancel()
    assert fake_robot.running_command_id is None


async def test_a_robot_lost_during_a_command_ends_it(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot)
    fake_robot.speed_scale = 1.0
    task = asyncio.create_task(connection.run_command(move_to("meeting_room")))
    await wait_for(lambda: fake_robot.running_command_id is not None)
    await fake_robot.stop()
    with pytest.raises(ConnectionError, match="may still be moving"):
        async with asyncio.timeout(3.0):
            await task


async def test_a_command_outlives_a_short_pause(host: Host, fake_robot: FakeKachaka) -> None:
    connection = await opened(host, fake_robot, lost_after=3.0)
    fake_robot.speed_scale = 5.0
    task = asyncio.create_task(connection.run_command(move_to("meeting_room")))
    await wait_for(lambda: fake_robot.running_command_id is not None)
    await fake_robot.stop()
    await asyncio.sleep(0.3)
    await fake_robot.start()
    async with asyncio.timeout(5.0):
        result = await task
    assert result.success
    assert fake_robot.pose == location("meeting_room")


# -- Settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.168.1.100", ("192.168.1.100", 26400)),
        ("192.168.1.100:26500", ("192.168.1.100", 26500)),
        ("kachaka.local", ("kachaka.local", 26400)),
        (" 127.0.0.1:1 ", ("127.0.0.1", 1)),
        ("[::1]:26401", ("::1", 26401)),
        ("[::1]", ("::1", 26400)),
        ("::1", ("::1", 26400)),
    ],
)
def test_an_address_has_a_default_port(address: str, expected: tuple[str, int]) -> None:
    assert parse_address(address) == expected


@pytest.mark.parametrize(
    "address", ["", "192.168.1.100:", "192.168.1.100:http", "192.168.1.100:70000", ":26400"]
)
def test_a_malformed_address_is_refused(address: str) -> None:
    with pytest.raises(ValueError):
        KachakaConnection(address=address)


def test_the_address_names_the_port() -> None:
    assert KachakaConnection(address="192.168.1.100").address == "192.168.1.100:26400"
    assert KachakaConnection(address="[::1]:26401").address == "[::1]:26401"


@pytest.mark.parametrize("setting", ["timeout", "retry", "poll"])
def test_durations_must_be_positive(setting: str) -> None:
    with pytest.raises(ValueError, match=setting):
        KachakaConnection(address="127.0.0.1", **{setting: 0.0})
    with pytest.raises(ValueError, match="lost_after"):
        KachakaConnection(address="127.0.0.1", lost_after=-1.0)
