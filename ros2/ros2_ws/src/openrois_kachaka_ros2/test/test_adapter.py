"""The ROS 2 adapter: its settings from adapter.yaml, and the engine it builds from them.

Runs without ROS 2: building the engine creates no node, and the components import
rclpy only when they connect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openrois.components.core.ros2 import RclpyExecutor
from openrois.components.kachaka import (
    Ros2Navigation,
    Ros2NavigationInformation,
    Ros2SystemInformation,
)
from openrois.engine import Engine
from openrois.interfaces.catalog import METHODS_BY_NAME

from openrois_kachaka_ros2.kachaka_ros2_adapter import (
    KachakaConfig,
    build_engine,
    load,
    without_ros_args,
)

EXAMPLE = Path(__file__).resolve().parents[4] / "adapter.yaml.example"


async def refs(engine: Engine) -> list[str]:
    session = engine.open_session(lambda method, params: None, trusted=True)
    for method in ("rois.system.connect", "rois.system.get_profile"):
        result = await engine.handle(session, method, METHODS_BY_NAME[method].params())
    return sorted(result.model_dump()["profile"]["component_ids"])  # type: ignore[attr-defined]


def test_the_example_file_loads() -> None:
    config = load(["--config", str(EXAMPLE)], environ={})
    assert config.engine_id == "kachaka"
    assert config.navigation.command_action == "/kachaka/kachaka_command/execute"
    assert config.system_information.pose_topic == "/kachaka/odometry/odometry"
    assert config.navigation_information.map_name == "coarobo-kachaka_01@unicorn"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--config", "a.yaml"], ["--config", "a.yaml"]),
        (["--config", "a.yaml", "--ros-args", "-r", "__ns:=/k"], ["--config", "a.yaml"]),
        (["--ros-args", "-r", "__ns:=/k", "--", "--log-level", "debug"], ["--log-level", "debug"]),
    ],
)
def test_the_ros_arguments_of_ros2_run_are_left_to_ros(
    argv: list[str], expected: list[str]
) -> None:
    assert without_ros_args(argv) == expected


async def test_the_components_share_one_executor_under_their_names() -> None:
    engine = build_engine(KachakaConfig())
    navigation, system_information, navigation_information = engine.local_components()
    assert isinstance(navigation, Ros2Navigation)
    assert isinstance(system_information, Ros2SystemInformation)
    assert isinstance(navigation_information, Ros2NavigationInformation)
    executors = {id(r) for c in engine.local_components() for r in c.rois_resources()}
    assert len(executors) == 1
    assert isinstance(navigation.rois_resources()[0], RclpyExecutor)
    assert await refs(engine) == [
        "kachaka/Navigation",
        "kachaka/NavigationInformation",
        "kachaka/SystemInformation",
    ]
