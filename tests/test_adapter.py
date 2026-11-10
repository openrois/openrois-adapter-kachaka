"""The adapter command: its settings and the engine it builds."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from openrois.components.core import Component
from openrois.engine.config import ConfigError

from openrois.components.kachaka import (
    KachakaConnection,
    KachakaNavigation,
    KachakaSystemInformation,
)
from openrois_adapter_kachaka import KachakaConfig, build_engine, load_config, main
from openrois_adapter_kachaka.cli import ConnectionSettings, NavigationSettings

ROOT = Path(__file__).parent.parent
ADDRESS = "192.168.1.100"


def load(tmp_path: Path, yaml_text: str | None = None, *argv: str, **env: str) -> KachakaConfig:
    args = list(argv)
    if yaml_text is not None:
        path = tmp_path / "adapter.yaml"
        path.write_text(yaml_text)
        args = ["--config", str(path), *args]
    return load_config(args, env)


def test_the_defaults_need_only_the_robot_address(tmp_path: Path) -> None:
    config = load(tmp_path, None, "--robot-address", ADDRESS)
    assert config.engine_id == "kachaka"
    assert config.robot_address == ADDRESS
    assert config.gateway_url == "ws://127.0.0.1:8765"
    assert config.log_level == "info"
    connection = config.connection
    assert (connection.timeout, connection.retry, connection.lost_after, connection.poll) == (
        5.0,
        3.0,
        6.0,
        10.0,
    )
    assert config.navigation.auto_homing is None


def test_adapter_yaml_sets_the_robot_and_its_sections(tmp_path: Path) -> None:
    config = load(
        tmp_path,
        "engine_id: kachaka_2\n"
        "robot_address: 192.168.1.100:26401\n"
        "connection:\n  timeout: 2.0\n  retry: 1.5\n  lost_after: 0\n  poll: 4.0\n"
        "navigation:\n  auto_homing: false\n",
    )
    assert config.engine_id == "kachaka_2"
    assert config.robot_address == "192.168.1.100:26401"
    connection = config.connection
    assert (connection.timeout, connection.retry, connection.lost_after, connection.poll) == (
        2.0,
        1.5,
        0.0,
        4.0,
    )
    assert config.navigation.auto_homing is False


def test_the_environment_and_the_flags_set_the_four_common_settings(tmp_path: Path) -> None:
    config = load(
        tmp_path,
        "engine_id: from_file\nrobot_address: 10.0.0.1\nlog_level: warning\n",
        "--engine-id",
        "kachaka_flag",
        "--log-level",
        "DEBUG",
        OPENROIS_ADAPTER_ENGINE_ID="from_env",
        OPENROIS_ADAPTER_ROBOT_ADDRESS=ADDRESS,
        OPENROIS_ADAPTER_GATEWAY_URL="ws://gateway:8765",
    )
    assert config.engine_id == "kachaka_flag"  # The flag wins over the environment.
    assert config.robot_address == ADDRESS  # The environment wins over the file.
    assert config.gateway_url == "ws://gateway:8765"
    assert config.log_level == "debug"


def test_the_file_is_named_by_the_environment_too(tmp_path: Path) -> None:
    path = tmp_path / "robot.yaml"
    path.write_text(f"robot_address: {ADDRESS}\nconnection:\n  retry: 0.5\n")
    config = load_config([], {"OPENROIS_ADAPTER_CONFIG": str(path)})
    assert config.connection.retry == 0.5


def test_the_sections_are_only_settings_of_the_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        load(tmp_path, None, "--robot-address", ADDRESS, "--auto-homing", "true")


def test_an_unknown_or_invalid_setting_is_refused(tmp_path: Path) -> None:
    robot = f"robot_address: {ADDRESS}\n"
    with pytest.raises(ConfigError, match="navigation.dance"):
        load(tmp_path, robot + "navigation:\n  dance: true\n")
    with pytest.raises(ConfigError, match="map_name"):
        load(tmp_path, robot + "map_name: floor\n")
    with pytest.raises(ConfigError, match="connection.retry"):
        load(tmp_path, robot + "connection:\n  retry: 0\n")
    with pytest.raises(ConfigError, match="connection.lost_after"):
        load(tmp_path, robot + "connection:\n  lost_after: -1\n")
    with pytest.raises(ConfigError, match="navigation.auto_homing"):
        load(tmp_path, robot + "navigation:\n  auto_homing: sometimes\n")


def test_the_robot_address_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="robot_address: set the Kachaka's IP address"):
        load(tmp_path)
    with pytest.raises(ConfigError, match="robot_address"):
        load(tmp_path, "robot_address: '  '\n")


@pytest.mark.parametrize("address", ["192.168.1.100:port", "192.168.1.100:0", "[::1"])
def test_a_malformed_robot_address_is_refused(tmp_path: Path, address: str) -> None:
    with pytest.raises(ConfigError, match="Invalid configuration:\n  robot_address: "):
        load(tmp_path, None, "--robot-address", address)


def test_main_exits_with_2_without_a_robot_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENROIS_ADAPTER_ROBOT_ADDRESS", raising=False)
    monkeypatch.delenv("OPENROIS_ADAPTER_CONFIG", raising=False)
    with pytest.raises(SystemExit) as exited:
        main([])
    assert exited.value.code == 2
    assert "robot_address" in capsys.readouterr().err


def test_the_command_exits_with_2_without_a_robot_address(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "openrois_adapter_kachaka"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "", "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 2
    assert "robot_address" in result.stderr


def test_the_port_defaults_to_26400(tmp_path: Path) -> None:
    engine = build_engine(load(tmp_path, None, "--robot-address", ADDRESS))
    [connection] = engine.local_components()[0].rois_resources()
    assert isinstance(connection, KachakaConnection)
    assert (connection.host, connection.port) == (ADDRESS, 26400)
    assert connection.address == f"{ADDRESS}:26400"

    engine = build_engine(load(tmp_path, None, "--robot-address", f"{ADDRESS}:26500"))
    [connection] = engine.local_components()[0].rois_resources()
    assert isinstance(connection, KachakaConnection)
    assert connection.port == 26500


def test_the_shipped_adapter_files_load() -> None:
    robot = load_config(["--config", str(ROOT / "adapter.yaml")], {})
    fake = load_config(["--config", str(ROOT / "adapter.fake.yaml")], {})
    assert (robot.engine_id, robot.robot_address) == ("kachaka", ADDRESS)
    assert robot.navigation.auto_homing is None
    assert robot.connection == KachakaConfig.model_fields["connection"].default
    assert (fake.engine_id, fake.robot_address) == ("kachaka_fake", "127.0.0.1:26400")
    assert fake.connection.retry == 1.0


def test_build_engine_hosts_the_components_on_one_connection() -> None:
    config = KachakaConfig(
        engine_id="kachaka_2",
        robot_address=ADDRESS,
        connection=ConnectionSettings(timeout=2.0, retry=1.0, lost_after=3.0, poll=4.0),
        navigation=NavigationSettings(auto_homing=False),
    )
    engine = build_engine(config)
    components = engine.local_components()
    assert [type(c) for c in components] == [
        KachakaSystemInformation,
        KachakaNavigation,
    ]
    assert [c.ref for c in components if isinstance(c, Component)] == [
        "kachaka_2/system_information",
        "kachaka_2/navigation",
    ]
    [connection] = components[0].rois_resources()
    assert isinstance(connection, KachakaConnection)
    assert {id(r) for c in components for r in c.rois_resources()} == {id(connection)}
    assert (connection.timeout, connection.retry, connection.lost_after, connection.poll) == (
        2.0,
        1.0,
        3.0,
        4.0,
    )
    navigation = components[1]
    assert isinstance(navigation, KachakaNavigation)
    assert navigation._auto_homing is False


def test_build_engine_needs_the_robot_address() -> None:
    with pytest.raises(ValueError, match="robot_address"):
        build_engine(KachakaConfig())
