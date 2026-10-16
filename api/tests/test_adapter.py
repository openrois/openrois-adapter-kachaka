"""The API adapter: its settings from adapter.yaml, and the engine it builds from them."""

from __future__ import annotations

from pathlib import Path

import pytest
from openrois.components.kachaka import (
    GrpcNavigation,
    GrpcNavigationInformation,
    GrpcSystemInformation,
)
from openrois.engine import Engine
from openrois.engine.config import ConfigError
from openrois.interfaces.catalog import METHODS_BY_NAME

import kachaka_api_adapter
from kachaka_api_adapter import KachakaConfig, build_engine, load

EXAMPLE = Path(__file__).parent.parent / "adapter.yaml.example"


async def refs(engine: Engine) -> list[str]:
    session = engine.open_session(lambda method, params: None, trusted=True)
    for method in ("rois.system.connect", "rois.system.get_profile"):
        result = await engine.handle(session, method, METHODS_BY_NAME[method].params())
    return sorted(result.model_dump()["profile"]["component_ids"])  # type: ignore[attr-defined]


def test_the_example_file_loads() -> None:
    config = load(["--config", str(EXAMPLE)], environ={})
    assert config.engine_id == "kachaka_01"
    assert config.robot_address == "192.168.1.100:26400"
    assert config.navigation.auto_homing is False
    assert config.navigation_information.map_name == "coarobo-kachaka_01@unicorn"


def test_the_environment_and_the_flags_set_the_common_settings(tmp_path: Path) -> None:
    config = load(
        ["--config", str(EXAMPLE), "--robot-address", "10.0.0.2:26400"],
        environ={"OPENROIS_ADAPTER_GATEWAY_URL": "ws://avatar:8765"},
    )
    assert config.robot_address == "10.0.0.2:26400"
    assert config.gateway_url == "ws://avatar:8765"


def test_a_setting_the_components_do_not_have_is_refused(tmp_path: Path) -> None:
    old = tmp_path / "adapter.yaml"
    old.write_text("robot_address: 10.0.0.2:26400\nnavigation:\n  poll_interval: 0.5\n")
    with pytest.raises(ConfigError, match="navigation.poll_interval"):
        load(["--config", str(old)], environ={})


async def test_the_engine_hosts_the_three_components_under_their_names() -> None:
    config = KachakaConfig(robot_address="10.0.0.2:26400")
    engine = build_engine(config)
    assert engine.engine_id == "kachaka_01"
    assert [type(c) for c in engine.local_components()] == [
        GrpcNavigation,
        GrpcSystemInformation,
        GrpcNavigationInformation,
    ]
    assert await refs(engine) == [
        "kachaka_01/Navigation",
        "kachaka_01/NavigationInformation",
        "kachaka_01/SystemInformation",
    ]


def test_main_refuses_to_run_without_a_robot_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(kachaka_api_adapter.WsClient, "run", lambda self: None)
    with pytest.raises(SystemExit) as exited:
        kachaka_api_adapter.main([], environ={})
    assert exited.value.code == 2
    assert "robot_address" in capsys.readouterr().err
