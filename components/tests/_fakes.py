"""Stand-ins for the Kachaka backends, installed as the modules the components import.

``FakeKachaka`` plays the gRPC API. ``FakeRos2`` plays rclpy, its executor and the
Kachaka ROS 2 interfaces, and calls the action callbacks from another thread, as an rclpy
executor does.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class Pose:
    x: float
    y: float
    theta: float


@dataclass
class Location:
    id: str
    name: str
    pose: Pose = field(default_factory=lambda: Pose(0.0, 0.0, 0.0))


@dataclass
class PngMap:
    resolution: float
    origin: Pose
    width: int
    height: int


@dataclass
class Result:
    success: bool
    error_code: int = 0


@dataclass
class FakeKachaka:
    """The gRPC API of one Kachaka. A drive lasts until ``arrive`` is set."""

    locations: list[Location] = field(default_factory=lambda: [Location("L1", "kitchen")])
    succeed: bool = True
    pose: Pose = field(default_factory=lambda: Pose(1.25, -0.5, 1.5708))
    moves: list[str] = field(default_factory=list)
    cancels: int = 0
    # The calls in order: "move L1", "cancel", and "cancelled L1" when a drive's call is
    # cancelled.
    log: list[str] = field(default_factory=list)
    auto_homing: bool | None = None
    arrive: asyncio.Event = field(default_factory=asyncio.Event)
    png_map: PngMap = field(
        default_factory=lambda: PngMap(0.05, Pose(-10.0, -5.0, 0.0), 400, 200)
    )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kachaka = self

        class KachakaApiClient:
            def __init__(self, target: str) -> None:
                self.target = target

            async def update_resolver(self) -> None:
                return None

            async def set_auto_homing_enabled(self, enabled: bool) -> None:
                kachaka.auto_homing = enabled

            async def get_locations(self) -> list[Location]:
                return kachaka.locations

            async def move_to_location(self, location_id: str, **options: Any) -> Result:
                assert options == {"wait_for_completion": True}
                kachaka.moves.append(location_id)
                kachaka.log.append(f"move {location_id}")
                try:
                    await kachaka.arrive.wait()
                except asyncio.CancelledError:
                    kachaka.log.append(f"cancelled {location_id}")
                    raise
                return Result(kachaka.succeed, 0 if kachaka.succeed else 10253)

            async def cancel_command(self) -> None:
                kachaka.cancels += 1
                kachaka.log.append("cancel")

            async def get_robot_pose(self) -> Pose:
                return kachaka.pose

            async def get_png_map(self) -> PngMap:
                return kachaka.png_map

        package = types.ModuleType("kachaka_api")
        aio = types.ModuleType("kachaka_api.aio")
        aio.KachakaApiClient = KachakaApiClient  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "kachaka_api", package)
        monkeypatch.setitem(sys.modules, "kachaka_api.aio", aio)


class _Future:
    """An rclpy future whose done callbacks run on another thread, once a gate opens."""

    def __init__(self, value: Any, gate: threading.Event) -> None:
        self._value = value
        self._gate = gate

    def result(self) -> Any:
        return self._value

    def add_done_callback(self, callback: Callable[[_Future], None]) -> None:
        def deliver() -> None:
            self._gate.wait(timeout=5.0)
            callback(self)

        threading.Thread(target=deliver).start()


class _ResultFuture:
    """The rclpy future of a goal's result, done when the goal ends."""

    def __init__(self, handle: GoalHandle) -> None:
        self._handle = handle

    def result(self) -> Any:
        return types.SimpleNamespace(result=types.SimpleNamespace(success=self._handle.success))

    def add_done_callback(self, callback: Callable[[_ResultFuture], None]) -> None:
        def deliver() -> None:
            self._handle.done.wait(timeout=5.0)
            callback(self)

        threading.Thread(target=deliver).start()


class GoalHandle:
    """A goal the fake Kachaka received. ``finish`` ends it, a cancel ends it too."""

    def __init__(self, ros: FakeRos2, location_id: str) -> None:
        self._ros = ros
        self.location_id = location_id
        self.accepted = ros.accept
        self.success = False
        self.done = threading.Event()

    def finish(self, success: bool = True) -> None:
        self.success = success
        self.done.set()

    def get_result_async(self) -> _ResultFuture:
        return _ResultFuture(self)

    def cancel_goal_async(self) -> _Future:
        self._ros.log.append(f"cancel {self.location_id}")
        self.finish(success=False)
        return _Future(None, self.done)


@dataclass
class FakeRos2:
    """rclpy and the Kachaka interfaces, with one action server and one odometry topic.

    ``answer`` gates the reply to a goal request, so a test can stop a start before the
    Kachaka accepts its goal.
    """

    accept: bool = True
    log: list[str] = field(default_factory=list)
    # rclpy's own calls in order: "init", "shutdown", "add <node>", "remove <node>" and
    # "destroy <node>".
    lifecycle: list[str] = field(default_factory=list)
    initialized: bool = False
    goals: list[GoalHandle] = field(default_factory=list)
    subscriptions: dict[str, Callable[[Any], None]] = field(default_factory=dict)
    answer: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.answer.set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ros = self

        class Node:
            def __init__(self, name: str) -> None:
                self.name = name

            def create_subscription(
                self, kind: Any, topic: str, callback: Callable[[Any], None], *args: Any,
                **kwargs: Any,
            ) -> None:
                ros.subscriptions[topic] = callback

            def __str__(self) -> str:
                return self.name

            def destroy_node(self) -> None:
                ros.lifecycle.append(f"destroy {self.name}")

        class ActionClient:
            def __init__(self, node: Node, kind: Any, name: str, **kwargs: Any) -> None:
                self.name = name

            def server_is_ready(self) -> bool:
                return True

            def send_goal_async(self, goal: Any) -> _Future:
                location_id = goal.kachaka_command.move_to_location_command_target_location_id
                ros.log.append(f"goal {location_id}")
                handle = GoalHandle(ros, location_id)
                ros.goals.append(handle)
                return _Future(handle, ros.answer)

            def destroy(self) -> None:
                return None

        class MultiThreadedExecutor:
            def __init__(self) -> None:
                self._stopped = threading.Event()

            def add_node(self, node: Any) -> None:
                ros.lifecycle.append(f"add {node}")

            def remove_node(self, node: Any) -> None:
                ros.lifecycle.append(f"remove {node}")

            def spin(self) -> None:
                self._stopped.wait(timeout=5.0)

            def shutdown(self, timeout_sec: float | None = None) -> bool:
                self._stopped.set()
                return True

        def init() -> None:
            assert not ros.initialized, "rclpy.init called twice"
            ros.initialized = True
            ros.lifecycle.append("init")

        def shutdown() -> None:
            ros.initialized = False
            ros.lifecycle.append("shutdown")

        def goal() -> Any:
            return types.SimpleNamespace(kachaka_command=types.SimpleNamespace())

        modules: dict[str, dict[str, Any]] = {
            "rclpy": {"ok": lambda: ros.initialized, "init": init, "shutdown": shutdown},
            "rclpy.executors": {"MultiThreadedExecutor": MultiThreadedExecutor},
            "rclpy.action": {"ActionClient": ActionClient},
            "rclpy.callback_groups": {"ReentrantCallbackGroup": object},
            "rclpy.node": {"Node": Node},
            "rclpy.qos": {
                "QoSProfile": lambda **kwargs: kwargs,
                "ReliabilityPolicy": types.SimpleNamespace(RELIABLE=1, BEST_EFFORT=2),
                "DurabilityPolicy": types.SimpleNamespace(TRANSIENT_LOCAL=1, VOLATILE=2),
            },
            "kachaka_interfaces": {},
            "kachaka_interfaces.action": {
                "ExecKachakaCommand": types.SimpleNamespace(Goal=goal),
            },
            "kachaka_interfaces.msg": {
                "LocationList": object,
                "KachakaCommand": types.SimpleNamespace(MOVE_TO_LOCATION_COMMAND=1),
            },
            "nav_msgs": {},
            "nav_msgs.msg": {"Odometry": object, "OccupancyGrid": object},
        }
        for name, attributes in modules.items():
            module = types.ModuleType(name)
            for attribute, value in attributes.items():
                setattr(module, attribute, value)
            monkeypatch.setitem(sys.modules, name, module)

    def publish_locations(self, topic: str, *locations: Location) -> None:
        self.subscriptions[topic](types.SimpleNamespace(locations=list(locations)))

    def publish_odometry(self, topic: str, x: float, y: float, yaw: float) -> None:
        import math

        orientation = types.SimpleNamespace(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2))
        position = types.SimpleNamespace(x=x, y=y)
        pose = types.SimpleNamespace(position=position, orientation=orientation)
        self.subscriptions[topic](types.SimpleNamespace(pose=types.SimpleNamespace(pose=pose)))

    def publish_map(
        self, topic: str, resolution: float, x: float, y: float, width: int, height: int
    ) -> None:
        origin = types.SimpleNamespace(
            position=types.SimpleNamespace(x=x, y=y),
            orientation=types.SimpleNamespace(z=0.0),
        )
        info = types.SimpleNamespace(
            resolution=resolution, origin=origin, width=width, height=height
        )
        self.subscriptions[topic](types.SimpleNamespace(info=info))
