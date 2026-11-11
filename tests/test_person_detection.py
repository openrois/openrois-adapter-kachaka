"""PersonDetection: the count of persons, its hold, its lifecycle and detections that stop."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import grpc
import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2
from kachaka_api.generated import kachaka_api_pb2_grpc as pb2_grpc

from openrois.components.kachaka import (
    KachakaConnection,
    KachakaNavigation,
    KachakaPersonDetection,
)
from openrois.components.kachaka.person_detection import Debounce
from openrois_adapter_kachaka.fake_robot import FakeKachaka
from tests.conftest import Host, Robot

# How often the detector of the fake publishes in these tests, in seconds.
PERIOD = 0.02


@pytest.fixture
async def fake_robot() -> AsyncIterator[FakeKachaka]:
    """A fake Kachaka whose detector publishes every 20 ms, an empty room at first."""
    robot = FakeKachaka(speed_scale=20.0)
    robot.detection_period = PERIOD
    async with robot:
        yield robot


@pytest.fixture
async def silent_robot() -> AsyncIterator[FakeKachaka]:
    """A fake Kachaka whose detector has published nothing yet, and does not publish."""
    robot = FakeKachaka(speed_scale=20.0)
    robot.detection_period = PERIOD
    robot.set_detecting(False)
    async with robot:
        yield robot


@pytest.fixture
async def stub(fake_robot: FakeKachaka) -> AsyncIterator[Any]:
    """The Kachaka API stub on a channel to the fake."""
    async with grpc.aio.insecure_channel(fake_robot.address) as channel:
        yield pb2_grpc.KachakaApiStub(channel)


def connection_to(fake: FakeKachaka, **settings: Any) -> KachakaConnection:
    """A connection with short durations: a robot that does not answer is lost at once."""
    options: dict[str, Any] = {"timeout": 1.0, "retry": 0.05, "lost_after": 0.0}
    options.update(settings)
    return KachakaConnection(address=fake.address, **options)


async def person_detection(
    host: Host,
    fake: FakeKachaka,
    *,
    hold: float = 0.2,
    min_score: float = 0.5,
    max_age: float = 5.0,
    **settings: Any,
) -> Robot:
    component = KachakaPersonDetection(
        connection_to(fake, **settings), hold=hold, min_score=min_score, max_age=max_age
    )
    robot = await host({"person_detection": component})
    await robot.subscribe("person_detection", "person_detected")
    return robot


def numbers(robot: Robot) -> list[int]:
    """The number of persons of every event sent so far."""
    return [int(event["number"]) for event in robot.events()]


def polls(fake: FakeKachaka) -> int:
    return len(fake.requests("GetObjectDetection"))


async def wait_for(condition: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def status_is(robot: Robot, name: str, status: str, timeout: float = 3.0) -> None:
    """Wait until component_status of a component reports ``status``."""
    async with asyncio.timeout(timeout):
        while await robot.status(name) != status:
            await asyncio.sleep(0.005)


async def detection(stub: Any, cursor: int = 0, timeout: float = 1.0) -> Any:
    request = pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor))
    return await stub.GetObjectDetection(request, timeout=timeout)


# -- The profile and the settings ----------------------------------------------------------


def test_serves_the_person_detection_profile() -> None:
    profile = KachakaPersonDetection.rois_profile()
    assert profile.identifier.authority == "OMG"
    assert profile.identifier.code == "PersonDetection"
    assert profile.function == "sensing"
    assert [c.name for c in profile.command_profiles] == ["start", "stop", "suspend", "resume"]
    assert [q.name for q in profile.query_profiles] == ["component_status"]
    assert [e.name for e in profile.event_profiles] == ["person_detected"]
    [detected] = profile.event_profiles
    assert [r.name for r in detected.results] == ["number", "timestamp"]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("hold", -0.1),
        ("hold", float("inf")),
        ("hold", float("nan")),
        ("min_score", -0.1),
        ("min_score", 1.1),
        ("min_score", float("nan")),
        ("max_age", 0.0),
        ("max_age", -1.0),
        ("max_age", float("inf")),
    ],
)
def test_settings_out_of_range_are_refused(setting: str, value: float) -> None:
    connection = KachakaConnection(address="127.0.0.1")
    with pytest.raises(ValueError, match=setting):
        KachakaPersonDetection(connection, **{setting: value})


def test_settings_at_their_limits_are_accepted() -> None:
    connection = KachakaConnection(address="127.0.0.1")
    KachakaPersonDetection(connection, hold=0.0, min_score=0.0, max_age=0.1)
    KachakaPersonDetection(connection, min_score=1.0)


# -- The hold ------------------------------------------------------------------------------


def test_debounce_reports_a_value_once_it_holds() -> None:
    debounce = Debounce(2.0)
    assert debounce.update(1, 10.0) is None
    assert debounce.update(1, 11.9) is None
    assert debounce.update(1, 12.0) == 1
    assert debounce.update(1, 20.0) is None  # Reported already.


def test_debounce_starts_the_hold_again_at_each_change() -> None:
    debounce = Debounce(2.0)
    assert debounce.update(0, 0.0) is None
    assert debounce.update(0, 2.0) == 0
    assert debounce.update(1, 3.0) is None
    assert debounce.update(0, 4.0) is None  # Back to the reported value.
    assert debounce.update(1, 5.0) is None
    assert debounce.update(1, 6.9) is None
    assert debounce.update(1, 7.0) == 1


def test_debounce_starts_the_hold_anew_after_skipped_readings() -> None:
    debounce = Debounce(2.0)
    assert debounce.update(0, 0.0) is None
    assert debounce.update(0, 2.0) == 0
    assert debounce.update(1, 3.0) is None
    # Readings were skipped for a minute; the 1 from before does not count as held.
    debounce.forget()
    assert debounce.update(1, 60.0) is None
    assert debounce.update(1, 62.0) == 1


def test_debounce_without_a_hold_reports_at_once() -> None:
    debounce = Debounce(0.0)
    assert debounce.update(3, 1.0) == 3
    assert debounce.update(3, 1.1) is None
    assert debounce.update(2, 1.2) == 2


# -- The count -----------------------------------------------------------------------------


async def test_start_reports_the_count_once_it_holds(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.see(persons=[0.9, 0.8])
    robot = await person_detection(host, fake_robot, hold=0.3)
    before = datetime.now(UTC)
    started = time.monotonic()
    assert await robot.run("person_detection", "start") == "OK"
    assert robot.events() == []  # The start completes once the count runs.
    await wait_for(lambda: robot.events())
    assert time.monotonic() - started >= 0.3
    [event] = robot.events()
    assert event["number"] == "2"
    assert before <= datetime.fromisoformat(event["timestamp"]) <= datetime.now(UTC)
    await asyncio.sleep(0.5)
    assert numbers(robot) == [2]  # A count that stays is reported once.


async def test_an_empty_room_is_a_count_of_zero(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0])


async def test_a_change_of_the_count_is_reported_once_it_holds(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await person_detection(host, fake_robot, hold=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0])
    changed = time.monotonic()
    fake_robot.see(persons=[0.9])
    await wait_for(lambda: numbers(robot) == [0, 1])
    assert time.monotonic() - changed >= 0.3
    fake_robot.see(persons=[0.9, 0.9, 0.9])
    await wait_for(lambda: numbers(robot) == [0, 1, 3])
    fake_robot.see()
    await wait_for(lambda: numbers(robot) == [0, 1, 3, 0])


async def test_a_count_that_flickers_is_not_reported(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, hold=0.5)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    for _ in range(3):
        fake_robot.see()
        await asyncio.sleep(0.15)
        fake_robot.see(persons=[0.9])
        await asyncio.sleep(0.15)
    await asyncio.sleep(0.6)
    assert numbers(robot) == [1]


async def test_only_persons_at_min_score_or_more_count(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.see(
        persons=[0.9, 0.5, 0.25],
        others=[
            (pb2.OBJECT_LABEL_SHELF, 0.99),
            (pb2.OBJECT_LABEL_CHARGER, 0.99),
            (pb2.OBJECT_LABEL_DOOR, 0.99),
            (pb2.OBJECT_LABEL_UNSPECIFIED, 0.99),
        ],
    )
    robot = await person_detection(host, fake_robot, min_score=0.5)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: robot.events())
    assert numbers(robot) == [2]


async def test_min_score_zero_counts_every_person(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.see(persons=[0.9, 0.5, 0.25], others=[(pb2.OBJECT_LABEL_SHELF, 0.99)])
    robot = await person_detection(host, fake_robot, min_score=0.0)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: robot.events())
    assert numbers(robot) == [3]


async def test_the_timestamp_is_the_time_of_receipt_not_the_clock_of_the_robot(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.clock_offset = 3600.0
    robot = await person_detection(host, fake_robot)
    before = datetime.now(UTC)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: robot.events())
    [event] = robot.events()
    timestamp = datetime.fromisoformat(event["timestamp"])
    assert timestamp.utcoffset() == UTC.utcoffset(None)
    assert before <= timestamp <= datetime.now(UTC)


# -- Start, stop, suspend and resume -------------------------------------------------------


async def test_a_second_start_reports_the_count_again_at_once(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, hold=0.4)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    first = datetime.fromisoformat(robot.events()[0]["timestamp"])
    again = time.monotonic()
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1, 1])
    assert time.monotonic() - again < 0.4  # Without waiting for the hold.
    second = datetime.fromisoformat(robot.events()[1]["timestamp"])
    assert first <= second <= datetime.now(UTC)
    await asyncio.sleep(0.6)
    assert numbers(robot) == [1, 1]


async def test_a_second_start_before_the_first_count_reports_it_once(
    host: Host, fake_robot: FakeKachaka
) -> None:
    robot = await person_detection(host, fake_robot, hold=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: robot.events())
    await asyncio.sleep(0.4)
    assert numbers(robot) == [0]


async def test_stop_ends_the_count_and_the_next_start_reports_it_again(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    assert await robot.run("person_detection", "stop") == "OK"
    read = polls(fake_robot)
    fake_robot.see(persons=[0.9, 0.9])
    await asyncio.sleep(0.5)
    assert numbers(robot) == [1]
    assert polls(fake_robot) == read  # A stopped component does not read.

    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1, 2])


async def test_suspend_holds_the_events_back_and_resume_reports_a_changed_count(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    assert await robot.run("person_detection", "suspend") == "OK"
    fake_robot.see(persons=[0.9, 0.9])
    await asyncio.sleep(0.6)
    assert numbers(robot) == [1]
    assert await robot.run("person_detection", "resume") == "OK"
    await wait_for(lambda: numbers(robot) == [1, 2])


async def test_resume_reports_nothing_when_the_count_did_not_change(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    assert await robot.run("person_detection", "suspend") == "OK"
    # A change that is gone again by the resume.
    fake_robot.see()
    await asyncio.sleep(0.4)
    fake_robot.see(persons=[0.9])
    await asyncio.sleep(0.1)
    assert await robot.run("person_detection", "resume") == "OK"
    await asyncio.sleep(0.6)
    assert numbers(robot) == [1]


async def test_a_stop_from_any_application_stops_the_count_for_all(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    first = await person_detection(host, fake_robot)
    assert await first.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(first) == [1])

    # A second application on the same engine learns the count from its own start.
    second = Robot(first.engine, first.refs)
    second.session = first.engine.open_session(
        lambda method, params: second.notifications.append((method, params.model_dump(mode="json")))
    )
    assert (await second.call("rois.system.connect"))["return_code"] == "OK"
    await second.bind("person_detection")
    await second.subscribe("person_detection", "person_detected")
    assert await second.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(second) == [1])
    assert numbers(first) == [1, 1]

    assert await second.run("person_detection", "stop") == "OK"
    fake_robot.see(persons=[0.9, 0.9])
    await asyncio.sleep(0.5)
    assert numbers(first) == [1, 1]
    assert numbers(second) == [1]


async def test_stop_while_suspended_ends_the_count(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0])
    assert await robot.run("person_detection", "suspend") == "OK"
    assert await robot.run("person_detection", "stop") == "OK"
    read = polls(fake_robot)
    await asyncio.sleep(0.2)
    assert polls(fake_robot) == read
    # The next start is not suspended any more.
    fake_robot.see(persons=[0.9])
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0, 1])


async def test_the_engine_stopping_ends_the_count(host: Host, fake_robot: FakeKachaka) -> None:
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0])
    await robot.engine.stop()
    read = polls(fake_robot)
    await asyncio.sleep(0.2)
    assert polls(fake_robot) == read


# -- Next to Navigation --------------------------------------------------------------------


async def test_the_count_runs_while_navigation_drives(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.speed_scale = 1.0  # The drive to the kitchen takes more than four seconds.
    connection = connection_to(fake_robot)
    robot = await host(
        {
            "navigation": KachakaNavigation(connection),
            "person_detection": KachakaPersonDetection(connection, hold=0.2),
        }
    )
    await robot.subscribe("person_detection", "person_detected")
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [0])

    assert await robot.set("navigation", target_positions=["kitchen"]) == "OK"
    drive = await robot.execute("navigation", "start")
    await wait_for(lambda: fake_robot.running_command_id is not None)
    command_id = fake_robot.running_command_id
    fake_robot.see(persons=[0.9])
    await wait_for(lambda: numbers(robot) == [0, 1])
    assert await robot.run("person_detection", "suspend") == "OK"
    assert await robot.run("person_detection", "resume") == "OK"
    assert await robot.run("person_detection", "stop") == "OK"
    # The drive is still the robot's command: the count started, cancelled and replaced
    # nothing.
    assert fake_robot.running_command_id == command_id
    assert len(fake_robot.requests("StartCommand")) == 1
    assert fake_robot.requests("CancelCommand") == []

    assert await robot.run("navigation", "stop") == "OK"
    assert await robot.completed(drive) == "ABORT"


# -- Detections that do not arrive ---------------------------------------------------------


async def test_no_detection_yet_is_a_warning_on_the_component_only(
    host: Host, silent_robot: FakeKachaka
) -> None:
    # The watchdog checks every 30 s, so a check in between would be one a call woke.
    connection = connection_to(silent_robot, retry=30.0)
    robot = await host(
        {
            "navigation": KachakaNavigation(connection),
            "person_detection": KachakaPersonDetection(connection, hold=0.0, max_age=0.3),
        }
    )
    await robot.subscribe("person_detection", "person_detected")
    checks = len(silent_robot.requests("IsReady"))
    started = time.monotonic()
    assert await robot.run("person_detection", "start") == "OK"
    assert await robot.status("person_detection") == "READY"
    await status_is(robot, "person_detection", "WARNING")
    assert time.monotonic() - started >= 0.3
    await asyncio.sleep(1.0)  # Several waits for a detection end without one.

    assert robot.events() == []
    assert await robot.status("person_detection") == "WARNING"
    assert await robot.status("navigation") == "READY"
    [error] = robot.sent("rois.system.notify_error")
    assert error["error_type"] == "COMPONENT_INTERNAL_ERROR"
    detail = await robot.call("rois.system.get_error_detail", error_id=error["error_id"])
    values = {r["name"]: r["value"] for r in detail["results"]}
    assert values["component_ref"] == robot.refs["person_detection"]
    assert values["status"] == "WARNING"
    assert values["message"] == (
        "No detection from the Kachaka for 0.3 s: the number of persons is not known"
    )
    # The robot was never taken for lost: the waits woke no check of the watchdog.
    assert connection.is_up
    assert len(silent_robot.requests("IsReady")) == checks
    assert polls(silent_robot) >= 3

    silent_robot.set_detecting(True)
    await status_is(robot, "person_detection", "READY")
    await wait_for(lambda: numbers(robot) == [0])
    assert len(robot.sent("rois.system.notify_error")) == 1


async def test_detections_that_stop_are_a_warning_and_the_count_is_reported_afresh(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="openrois.components.kachaka.person_detection")
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, hold=0.1, max_age=0.4)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    assert robot.sent("rois.system.notify_error") == []

    stopped = time.monotonic()
    fake_robot.set_detecting(False)
    await status_is(robot, "person_detection", "WARNING")
    assert time.monotonic() - stopped >= 0.3
    assert "No detection from the Kachaka for 0.4 s" in caplog.text
    # Nothing is emitted for the count that is not known, and a start has no count to
    # report again.
    assert await robot.run("person_detection", "start") == "OK"
    await asyncio.sleep(0.3)
    assert numbers(robot) == [1]

    fake_robot.set_detecting(True)
    await status_is(robot, "person_detection", "READY")
    await wait_for(lambda: numbers(robot) == [1, 1])
    assert "The Kachaka sends detections again" in caplog.text
    await asyncio.sleep(0.3)
    assert numbers(robot) == [1, 1]
    assert len(robot.sent("rois.system.notify_error")) == 1


async def test_an_old_detection_is_not_counted(host: Host, fake_robot: FakeKachaka) -> None:
    fake_robot.see(persons=[0.9])
    await asyncio.sleep(5 * PERIOD)
    fake_robot.set_detecting(False)  # The robot keeps its latest detection, which ages.
    robot = await person_detection(host, fake_robot, hold=0.0, max_age=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    await status_is(robot, "person_detection", "WARNING")
    assert robot.events() == []
    # The first request asked for the latest detection, the others for the ones after it.
    first, *others = fake_robot.requests("GetObjectDetection")
    assert first.metadata.cursor == 0
    assert others
    assert all(request.metadata.cursor != 0 for request in others)

    fake_robot.set_detecting(True)
    await wait_for(lambda: numbers(robot) == [1])
    assert await robot.status("person_detection") == "READY"


async def test_stop_clears_the_warning(host: Host, silent_robot: FakeKachaka) -> None:
    robot = await person_detection(host, silent_robot, max_age=0.2)
    assert await robot.run("person_detection", "start") == "OK"
    await status_is(robot, "person_detection", "WARNING")
    assert await robot.run("person_detection", "stop") == "OK"
    assert await robot.status("person_detection") == "READY"
    await asyncio.sleep(0.4)
    assert await robot.status("person_detection") == "READY"
    # A new start waits max_age again before it warns.
    assert await robot.run("person_detection", "start") == "OK"
    assert await robot.status("person_detection") == "READY"
    await status_is(robot, "person_detection", "WARNING")


async def test_the_warning_is_raised_while_suspended_too(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, hold=0.1, max_age=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    assert await robot.run("person_detection", "suspend") == "OK"
    fake_robot.set_detecting(False)
    await status_is(robot, "person_detection", "WARNING")
    fake_robot.set_detecting(True)
    await status_is(robot, "person_detection", "READY")
    await asyncio.sleep(0.3)
    assert numbers(robot) == [1]
    # The count is reported afresh after the resume, since it was not known in between.
    assert await robot.run("person_detection", "resume") == "OK"
    await wait_for(lambda: numbers(robot) == [1, 1])


async def test_a_refused_request_is_logged_once_and_tried_again(
    host: Host, fake_robot: FakeKachaka, caplog: pytest.LogCaptureFixture
) -> None:
    fake_robot.refuse_detection()
    robot = await person_detection(host, fake_robot, max_age=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    await status_is(robot, "person_detection", "WARNING")
    await wait_for(lambda: polls(fake_robot) >= 3)
    refusals = [r for r in caplog.records if "gives no detection" in r.getMessage()]
    assert len(refusals) == 1
    assert "UNIMPLEMENTED" in refusals[0].getMessage()
    assert robot.events() == []

    fake_robot.refuse_detection(False)
    await wait_for(lambda: numbers(robot) == [0])
    assert await robot.status("person_detection") == "READY"


# -- A robot that goes away ----------------------------------------------------------------


async def test_the_count_resumes_once_the_robot_is_back(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])

    await fake_robot.stop()
    await status_is(robot, "person_detection", "ERROR")
    [error] = robot.sent("rois.system.notify_error")
    assert error["error_type"] == "COMPONENT_NOT_RESPONDING"
    fake_robot.see(persons=[0.9, 0.9])
    await asyncio.sleep(0.5)
    assert numbers(robot) == [1]

    await fake_robot.start()
    await status_is(robot, "person_detection", "READY")
    await wait_for(lambda: numbers(robot) == [1, 2])
    assert len(robot.sent("rois.system.notify_error")) == 1


async def test_an_unchanged_count_is_not_reported_again_after_a_short_outage(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    await fake_robot.stop()
    await status_is(robot, "person_detection", "ERROR")
    await fake_robot.start()
    await status_is(robot, "person_detection", "READY")
    read = polls(fake_robot)
    await wait_for(lambda: polls(fake_robot) >= read + 5)
    await asyncio.sleep(0.4)
    assert numbers(robot) == [1]


async def test_an_outage_longer_than_max_age_reports_the_count_afresh(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, max_age=0.3)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    await fake_robot.stop()
    await status_is(robot, "person_detection", "ERROR")
    await asyncio.sleep(0.6)
    await fake_robot.start()
    await status_is(robot, "person_detection", "READY")
    await wait_for(lambda: numbers(robot) == [1, 1])


async def test_detections_do_not_count_while_the_robot_is_not_ready(
    host: Host, fake_robot: FakeKachaka
) -> None:
    fake_robot.see(persons=[0.9])
    robot = await person_detection(host, fake_robot, hold=0.0)
    assert await robot.run("person_detection", "start") == "OK"
    await wait_for(lambda: numbers(robot) == [1])
    # A slow detector, so the robot stops being ready while a request for its next
    # detection waits.
    fake_robot.detection_period = 0.3
    read = polls(fake_robot)
    await wait_for(lambda: polls(fake_robot) >= read + 2)
    fake_robot.set_ready(False)
    fake_robot.see(persons=[0.9, 0.9])
    await status_is(robot, "person_detection", "WARNING")
    await asyncio.sleep(0.6)
    assert numbers(robot) == [1]
    read = polls(fake_robot)
    await asyncio.sleep(0.4)
    assert polls(fake_robot) == read  # Nor is the robot asked meanwhile.

    fake_robot.set_ready(True)
    await status_is(robot, "person_detection", "READY")
    await wait_for(lambda: numbers(robot) == [1, 2])


# -- The detector of the fake --------------------------------------------------------------


async def test_the_fake_waits_for_its_first_detection(silent_robot: FakeKachaka) -> None:
    async with grpc.aio.insecure_channel(silent_robot.address) as channel:
        stub = pb2_grpc.KachakaApiStub(channel)
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await detection(stub, timeout=0.3)
        assert caught.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        waiting = asyncio.ensure_future(detection(stub))
        await asyncio.sleep(0.1)
        assert not waiting.done()
        silent_robot.set_detecting(True)
        first = await waiting
        assert first.metadata.cursor != 0
        assert list(first.objects) == []


async def test_the_fake_publishes_what_its_detector_sees(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.see(persons=[0.75, 0.5], others=[(pb2.OBJECT_LABEL_SHELF, 0.25)])
    await asyncio.sleep(3 * PERIOD)
    before = time.time_ns()
    current = await detection(stub)  # Cursor 0 answers with the latest detection at once.
    found = [(found.label, found.score) for found in current.objects]
    assert found == [
        (pb2.OBJECT_LABEL_PERSON, 0.75),
        (pb2.OBJECT_LABEL_PERSON, 0.5),
        (pb2.OBJECT_LABEL_SHELF, 0.25),
    ]
    assert all(found.roi.width > 0 and found.distance_median > 0 for found in current.objects)
    assert before - 1_000_000_000 < current.header.stamp_nsec <= time.time_ns()
    # The latest cursor waits for the next detection.
    following = await detection(stub, current.metadata.cursor)
    assert following.metadata.cursor != current.metadata.cursor
    assert following.header.stamp_nsec > current.header.stamp_nsec


async def test_a_detector_that_stopped_keeps_its_latest_detection(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.see(persons=[0.9])
    await asyncio.sleep(3 * PERIOD)
    fake_robot.set_detecting(False)
    latest = await detection(stub)
    assert len(latest.objects) == 1
    again = await detection(stub)
    assert again.metadata.cursor == latest.metadata.cursor
    assert again.header.stamp_nsec == latest.header.stamp_nsec
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await detection(stub, latest.metadata.cursor, timeout=0.3)
    assert caught.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED


async def test_the_fake_stamps_detections_with_its_own_clock(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.clock_offset = 3600.0
    current = await detection(stub)
    following = await detection(stub, current.metadata.cursor)
    ahead = following.header.stamp_nsec - time.time_ns()
    assert 3599e9 < ahead < 3601e9


async def test_the_fake_refuses_the_detection_on_request(
    fake_robot: FakeKachaka, stub: Any
) -> None:
    fake_robot.refuse_detection()
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await detection(stub)
    assert caught.value.code() == grpc.StatusCode.UNIMPLEMENTED
    fake_robot.refuse_detection(False)
    assert (await detection(stub)).metadata.cursor != 0
