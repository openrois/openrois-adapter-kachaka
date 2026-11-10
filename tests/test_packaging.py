"""The adapter's packages import next to the OpenRoIS packages they build on."""

from __future__ import annotations

import subprocess
import sys

import openrois.components.core

import openrois.components.kachaka
import openrois_adapter_kachaka

# Run in a fresh interpreter: a finder first on sys.meta_path refuses grpc and kachaka_api,
# so any import of them, direct or through another module, fails.
_WITHOUT_THE_ROBOT_EXTRA = """
import sys

class Refuse:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("grpc", "kachaka_api"):
            raise ImportError(f"{name} is not installed")
        return None

sys.meta_path.insert(0, Refuse())
import openrois.components.kachaka
import openrois_adapter_kachaka
print(sorted(m for m in sys.modules if m.split(".")[0] in ("grpc", "kachaka_api")))
"""


def test_components_share_the_openrois_namespace() -> None:
    """openrois.components.kachaka sits next to openrois.components.core."""
    assert openrois.components.kachaka.__name__ == "openrois.components.kachaka"
    assert openrois.components.core.Component is not None


def test_adapter_package_imports() -> None:
    assert openrois_adapter_kachaka.__name__ == "openrois_adapter_kachaka"


def test_packages_import_without_the_robot_extra() -> None:
    """Importing the components and the adapter never imports grpc or kachaka_api."""
    result = subprocess.run(
        [sys.executable, "-c", _WITHOUT_THE_ROBOT_EXTRA], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_openrois_packages_have_the_engine_and_the_standard_profiles() -> None:
    """The OpenRoIS releases the adapter requires have the engine and both profiles."""
    from openrois.engine import Engine
    from openrois.interfaces.components import NAVIGATION_PROFILE, SYSTEM_INFORMATION_PROFILE

    codes = [p.identifier.code for p in (NAVIGATION_PROFILE, SYSTEM_INFORMATION_PROFILE)]
    assert codes == ["Navigation", "SystemInformation"]
    assert Engine is not None
