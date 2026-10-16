"""NavigationInformation over both backends: destinations, their positions and the map."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from openrois.components.core.ros2 import RclpyExecutor

from openrois.components.kachaka import (
    NAVIGATION_INFORMATION_PROFILE,
    GrpcNavigationInformation,
    NavigationInformationSettings,
    Ros2NavigationInformation,
    Ros2NavigationInformationSettings,
)
from tests._fakes import FakeKachaka, FakeRos2, Location, Pose

type Host = Callable[..., Any]

LOCATIONS = "/kachaka/layout/locations/list"
MAP = "/kachaka/mapping/map"
KITCHEN = Location("L1", "kitchen", Pose(1.5, 2.0, 0.25))
DOCK = Location("L2", "dock", Pose(-1.0, 0.0, 3.0))


async def answer(robot: Any, query_type: str) -> Any:
    result = await robot.call("rois.query.query", query_type=query_type, condition="")
    assert result["return_code"] == "OK", result
    [value] = [r["value"] for r in result["results"] if r["name"] == query_type]
    return json.loads(value)


def test_the_profile_is_published_by_coarobo() -> None:
    assert NAVIGATION_INFORMATION_PROFILE.identifier.authority == "Coarobo"
    assert NAVIGATION_INFORMATION_PROFILE.identifier.code == "NavigationInformation"
    for served in (GrpcNavigationInformation, Ros2NavigationInformation):
        queries = [q.name for q in served.rois_profile().query_profiles]
        assert queries == ["component_status", "destinations", "destination_positions", "map_info"]


class TestGrpc:
    @pytest.fixture
    def kachaka(self, monkeypatch: pytest.MonkeyPatch) -> FakeKachaka:
        fake = FakeKachaka(locations=[KITCHEN, DOCK])
        fake.install(monkeypatch)
        return fake

    async def test_lists_the_destinations_and_their_positions(
        self, host: Host, kachaka: FakeKachaka
    ) -> None:
        robot = await host("NavigationInformation", GrpcNavigationInformation("10.0.0.2:26400"))
        assert await answer(robot, "destinations") == ["kitchen", "dock"]
        positions = await answer(robot, "destination_positions")
        assert positions[0] == {"id": "L1", "name": "kitchen", "x": 1.5, "y": 2.0, "theta": 0.25}

    async def test_map_info_combines_the_map_name_and_the_live_geometry(
        self, host: Host, kachaka: FakeKachaka
    ) -> None:
        settings = NavigationInformationSettings(map_name="coarobo-kachaka_01@unicorn")
        robot = await host(
            "NavigationInformation", GrpcNavigationInformation("10.0.0.2:26400", settings)
        )
        assert await answer(robot, "map_info") == {
            "map_name": "coarobo-kachaka_01@unicorn",
            "resolution": 0.05,
            "origin_x": -10.0,
            "origin_y": -5.0,
            "origin_theta": 0.0,
            "width": 400,
            "height": 200,
        }


class TestRos2:
    @pytest.fixture
    def ros(self, monkeypatch: pytest.MonkeyPatch) -> FakeRos2:
        fake = FakeRos2()
        fake.install(monkeypatch)
        return fake

    async def test_answers_empty_until_the_topics_publish(self, host: Host, ros: FakeRos2) -> None:
        robot = await host("NavigationInformation", Ros2NavigationInformation(RclpyExecutor()))
        assert "add openrois_kachaka_navinfo" in ros.lifecycle
        assert await answer(robot, "destinations") == []
        assert await answer(robot, "destination_positions") == []
        assert await answer(robot, "map_info") == {}

    async def test_answers_from_the_latest_messages(self, host: Host, ros: FakeRos2) -> None:
        settings = Ros2NavigationInformationSettings(map_name="kachaka@home")
        robot = await host(
            "NavigationInformation", Ros2NavigationInformation(RclpyExecutor(), settings)
        )
        ros.publish_locations(LOCATIONS, KITCHEN, DOCK)
        ros.publish_map(MAP, 0.05, -10.0, -5.0, 400, 200)
        assert await answer(robot, "destinations") == ["kitchen", "dock"]
        positions = await answer(robot, "destination_positions")
        assert positions[1] == {"id": "L2", "name": "dock", "x": -1.0, "y": 0.0, "theta": 3.0}
        info = await answer(robot, "map_info")
        assert info["map_name"] == "kachaka@home"
        assert (info["width"], info["height"], info["resolution"]) == (400, 200, 0.05)
