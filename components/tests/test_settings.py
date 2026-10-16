"""The settings of the components, as an adapter reads them from adapter.yaml."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from openrois.components.kachaka import (
    GrpcNavigationSettings,
    Ros2NavigationSettings,
    Ros2SystemInformationSettings,
)


def test_the_defaults_match_the_kachaka_ros2_bridge() -> None:
    navigation = Ros2NavigationSettings()
    assert navigation.node_name == "openrois_kachaka"
    assert navigation.locations_topic == "/kachaka/layout/locations/list"
    assert navigation.command_action == "/kachaka/kachaka_command/execute"
    assert navigation.goal_timeout == 10.0
    assert Ros2SystemInformationSettings().pose_topic == "/kachaka/odometry/odometry"
    assert GrpcNavigationSettings().auto_homing is False


def test_a_section_of_adapter_yaml_validates_into_settings() -> None:
    section = {"node_name": "kachaka_2", "goal_timeout": 5}
    settings = Ros2NavigationSettings.model_validate(section)
    assert settings.node_name == "kachaka_2"
    assert settings.goal_timeout == 5.0


@pytest.mark.parametrize(
    "model",
    [GrpcNavigationSettings, Ros2NavigationSettings, Ros2SystemInformationSettings],
)
def test_an_unknown_setting_is_refused(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"poll_interval": 0.5})


def test_a_goal_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Ros2NavigationSettings(goal_timeout=0)
