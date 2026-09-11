"""rclpy Node for the Kachaka ROS 2 adapter.

Subscribes to Kachaka ROS 2 topics (robot pose, locations) and creates
an action client for the ExecKachakaCommand action. The node runs in a
background thread spun by the adapter SDK framework.

The adapter accesses the node's cached state (latest pose, locations)
from the asyncio main loop. Shared state is protected by threading.Lock.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

logger = logging.getLogger(__name__)


class KachakaRos2Client(Node):
    """ROS 2 client that subscribes to Kachaka topics and calls actions.

    Attributes:
        latest_pose: Thread-safe cache of the latest robot pose (x, y, theta).
        latest_locations: Thread-safe cache of the latest location list.
        command_running: Thread-safe flag for whether a command is active.
        on_result: Callback invoked when an action completes. Set by the adapter.
    """

    def __init__(self, config: dict) -> None:
        env = config.get("environment", {})
        node_name = env.get("ros2_node_name", "openrois_kachaka_ros2")
        super().__init__(node_name)

        pose_topic = env.get(
            "robot_pose_topic", "/kachaka/odometry/odometry",
        )
        locations_topic = env.get(
            "locations_topic", "/kachaka/layout/locations/list",
        )
        command_action = env.get(
            "command_action", "/kachaka/kachaka_command/execute",
        )

        self._lock = threading.Lock()
        self._latest_pose: tuple[float, float, float] | None = None
        self._latest_locations: list[dict] | None = None
        self._command_running = False
        self._on_result: Callable[[bool], None] | None = None
        self._goal_handle = None

        cb_group = ReentrantCallbackGroup()

        # Subscribe to odometry for robot position.
        # The kachaka_grpc_ros2_bridge publishes nav_msgs/Odometry with
        # SensorDataQoS (BEST_EFFORT reliability, VOLATILE durability).
        # Match it or no messages will be received.
        from nav_msgs.msg import Odometry

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            depth=5,
        )
        self.create_subscription(
            Odometry,
            pose_topic,
            self._on_odometry,
            sensor_qos,
            callback_group=cb_group,
        )
        logger.info("Subscribed to %s (BEST_EFFORT)", pose_topic)

        # Subscribe to location list.
        # The kachaka_grpc_ros2_bridge publishes kachaka_interfaces/LocationList
        # with QoS KeepLast(1) + transient_local (RELIABLE, TRANSIENT_LOCAL).
        # Match it so the last published list is received on startup.
        from kachaka_interfaces.msg import LocationList

        latched_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )
        self.create_subscription(
            LocationList,
            locations_topic,
            self._on_locations,
            latched_qos,
            callback_group=cb_group,
        )
        logger.info("Subscribed to %s (TRANSIENT_LOCAL)", locations_topic)

        # Action client for ExecKachakaCommand.
        from kachaka_interfaces.action import ExecKachakaCommand

        self._action_client = ActionClient(
            self,
            ExecKachakaCommand,
            command_action,
            callback_group=cb_group,
        )
        logger.info("Action client created for %s", command_action)

    # ─── Topic callbacks (run in rclpy executor thread) ────────

    def _on_odometry(self, msg) -> None:
        """Cache the latest robot pose from odometry."""
        pos = msg.pose.pose.position
        # Extract yaw from quaternion.
        import math
        q = msg.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
        with self._lock:
            self._latest_pose = (pos.x, pos.y, yaw)

    def _on_locations(self, msg) -> None:
        """Cache the latest location list."""
        locations = []
        for loc in msg.locations:
            locations.append({
                "id": loc.id,
                "name": loc.name,
                "x": loc.pose.x,
                "y": loc.pose.y,
                "theta": loc.pose.theta,
            })
        with self._lock:
            self._latest_locations = locations
        logger.info("Received %d locations", len(locations))

    # ─── Action client (called from asyncio loop) ──────────────

    def send_move_to_location(self, location_id: str) -> bool:
        """Send a move_to_location command via the action client.

        Returns True if the goal was sent, False if the server is not ready.
        Does not block. The connect() method ensures the server is available
        before commands are accepted.
        """
        from kachaka_interfaces.action import ExecKachakaCommand
        from kachaka_interfaces.msg import KachakaCommand

        if not self._action_client.server_is_ready():
            logger.error("Action server not available")
            return False

        goal_msg = ExecKachakaCommand.Goal()
        goal_msg.kachaka_command.command_type = (
            KachakaCommand.MOVE_TO_LOCATION_COMMAND
        )
        goal_msg.kachaka_command.move_to_location_command_target_location_id = (
            location_id
        )

        with self._lock:
            self._command_running = True

        self._action_client.send_goal_async(
            goal_msg,
            feedback_callback=lambda _: None,
        ).add_done_callback(self._on_goal_response)
        return True

    def _on_goal_response(self, future) -> None:
        """Handle goal acceptance/rejection."""
        goal_handle = future.result()
        if not goal_handle.accepted:
            logger.warning("Action goal rejected")
            with self._lock:
                self._command_running = False
            return
        logger.info("Action goal accepted")
        with self._lock:
            self._goal_handle = goal_handle
        goal_handle.get_result_async().add_done_callback(self._on_result_callback)

    def _on_result_callback(self, future) -> None:
        """Handle action completion. Emit reached_target event."""
        result = future.result()
        success = result.success if result is not None else False
        logger.info("Action completed (success=%s)", success)

        with self._lock:
            self._command_running = False
            self._goal_handle = None
            callback = self._on_result

        if callback is not None:
            callback(success)

    def cancel_command(self) -> None:
        """Cancel the current command via the action goal handle."""
        with self._lock:
            self._command_running = False
            goal_handle = self._goal_handle
            self._goal_handle = None

        if goal_handle is not None:
            goal_handle.cancel_goal_async()
            logger.info("Cancel sent to action server")
        else:
            logger.info("Cancel requested but no active goal")

    # ─── State accessors (called from asyncio loop) ────────────

    def get_pose(self) -> tuple[float, float, float] | None:
        with self._lock:
            return self._latest_pose

    def get_locations(self) -> list[dict] | None:
        with self._lock:
            return self._latest_locations

    def is_command_running(self) -> bool:
        with self._lock:
            return self._command_running

    def set_result_callback(self, callback: Callable[[bool], None]) -> None:
        with self._lock:
            self._on_result = callback
