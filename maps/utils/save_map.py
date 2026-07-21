#!/usr/bin/env python3
"""Save a Kachaka occupancy grid to a standard ROS map (.pgm + .yaml).

Runs inside the ros2_bridge container (or any ROS 2 env with rclpy + nav_msgs).
Subscribes to /kachaka/map (nav_msgs/OccupancyGrid, latched), writes the first
message it receives, then exits.

Usage (from host, via the bridge container):
    docker cp save_map.py ros2_bridge-ros2_bridge-1:/tmp/save_map.py
    docker exec ros2_bridge-ros2_bridge-1 /opt/kachaka/env.sh \\
        python3 /tmp/save_map.py -o /tmp/kachaka_map -t /kachaka/map
    docker cp ros2_bridge-ros2_bridge-1:/tmp/kachaka_map.pgm .
    docker cp ros2_bridge-ros2_bridge-1:/tmp/kachaka_map.yaml .
"""
from __future__ import annotations

import argparse
import sys

import cv2
import numpy as np
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSHistoryPolicy


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="save /kachaka/map to .pgm + .yaml")
    p.add_argument("-t", "--topic", default="/kachaka/map", help="OccupancyGrid topic")
    p.add_argument("-o", "--output", required=True, help="output base path (no extension)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rclpy.init()
    node = rclpy.create_node("map_saver_lite")

    # Latched (transient_local) so we get the last published map immediately.
    qos = QoSProfile(
        depth=1,
        history=QoSHistoryPolicy.KEEP_LAST,
        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    )

    received: dict[str, OccupancyGrid] = {}

    def cb(msg: OccupancyGrid) -> None:
        received["msg"] = msg
        node.get_logger().info(
            f"got map: {msg.info.width}x{msg.info.height} @ {msg.info.resolution} m/px"
        )

    node.create_subscription(OccupancyGrid, args.topic, cb, qos)

    print(f"waiting for a map on {args.topic} ...", flush=True)
    while rclpy.ok() and "msg" not in received:
        rclpy.spin_once(node, timeout_sec=0.5)

    if "msg" not in received:
        print("no map received before shutdown", file=sys.stderr)
        return 1

    msg = received["msg"]
    info = msg.info

    # OccupancyGrid values: -1 unknown, 0 free, 100 occupied.
    # Convert to a grayscale PGM: unknown -> 205, free -> 254, occupied -> 0.
    arr = np.asarray(msg.data, dtype=np.int16).reshape((info.height, info.width))
    img = np.full_like(arr, 205, dtype=np.uint8)
    img[arr == 0] = 254
    img[arr > 0] = 0  # occupied
    # PGM rows are top-down; OccupancyGrid origin is bottom-left, so flip.
    img = np.flipud(img)

    pgm_path = args.output + ".pgm"
    yaml_path = args.output + ".yaml"
    cv2.imwrite(pgm_path, img)

    ox = info.origin.position.x
    oy = info.origin.position.y
    q = info.origin.orientation
    # yaw from quaternion
    import math
    yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                     1.0 - 2.0 * (q.y * q.y + q.z * q.z))
    yaml_text = (
        f"image: {pgm_path.split('/')[-1]}\n"
        f"resolution: {info.resolution}\n"
        f"origin: [{ox}, {oy}, 0.0]\n"
        f"negate: 0\n"
        f"occupied_thresh: 0.65\n"
        f"free_thresh: 0.196\n\n"
    )
    with open(yaml_path, "w") as f:
        f.write(yaml_text)

    print(f"saved {pgm_path} and {yaml_path}")
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())