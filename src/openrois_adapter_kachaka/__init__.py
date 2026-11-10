"""The OpenRoIS adapter process for the Preferred Robotics Kachaka.

The adapter builds an ``Engine`` with the components of ``openrois.components.kachaka``
for one robot and serves it to an OpenRoIS gateway with ``WsClient``. Two adapters with
different engine ids put two robots behind one gateway. ``KachakaConfig`` holds the
settings, which ``adapter.yaml`` gives. ``openrois_adapter_kachaka.fake_robot`` serves the
Kachaka gRPC API without a robot, for tests and for trying the adapter out; it needs the
``robot`` extra, so this package does not import it.
"""

from openrois_adapter_kachaka.cli import KachakaConfig, build_engine, load_config, main

__all__ = ["KachakaConfig", "build_engine", "load_config", "main"]
