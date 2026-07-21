#!/usr/bin/env python3
"""Render Kachaka landmark locations on top of the exported occupancy-grid map.

Reads the map (.pgm + .yaml) and a list of locations (either from a text dump of
the GetLocations gRPC response, or passed directly on the CLI), and produces a
PNG with each landmark drawn at its real-world pose.

Coordinate conversion:
    pixel_x = (world_x - origin_x) / resolution
    pixel_y = height - (world_y - origin_y) / resolution   # flip Y (image top-down)

Usage:
    # 1. Pull locations from the robot into a text file:
    python3 kachaka-api/python/demos/grpc_samples/get_locations.py \
        <ROBOT_IP>:26400 > locations.txt

    # 2. Render them on the map:
    python3 render_landmarks.py \
        --map maps/kachaka_map.yaml \
        --locations locations.txt \
        --output maps/kachaka_map_landmarks.png

    # Or pass locations directly on the command line:
    python3 render_landmarks.py \
        --map maps/kachaka_map.yaml \
        --output maps/kachaka_map_landmarks.png \
        --loc S01_home,3.773288,-1.30068,1.785376,shelf_home \
        --loc home,0.033114,-0.062141,-0.020478,charger
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml


# ── colour palette per location type ──────────────────────────────────────────
TYPE_COLOURS: dict[str, tuple[int, int, int]] = {
    "charger":      (0, 0, 255),       # red
    "shelf_home":   (0, 165, 255),     # orange
    "slam_marker":  (255, 0, 255),     # magenta
    "unspecified":  (0, 255, 255),     # yellow
}

DEFAULT_COLOUR = (200, 200, 200)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--map", required=True, help="path to the map .yaml file")
    p.add_argument("--locations", help="path to a text dump of GetLocations response")
    p.add_argument("--loc", action="append", default=[],
                   help="inline location: id,x,y,theta[,type]")
    p.add_argument("--output", "-o", default="landmarks_on_map.png",
                   help="output PNG path")
    p.add_argument("--arrow-len", type=float, default=0.5,
                   help="arrow length in metres (default 0.5 m)")
    p.add_argument("--font-scale", type=float, default=0.4,
                   help="label font scale (default 0.4)")
    return p.parse_args()


# ── parse the text dump of GetLocations ──────────────────────────────────────
def parse_locations_file(path: str) -> list[dict]:
    """Parse a text dump of the protobuf GetLocationsResponse.

    Handles the default text_format output:
        locations {
          id: "S01_home"
          name: "シェルフのホーム"
          pose { x: 3.773288  y: -1.30068  theta: 1.785376 }
          type: LOCATION_TYPE_SHELF_HOME
        }
    """
    text = Path(path).read_text(encoding="utf-8")
    locations: list[dict] = []

    # Split on 'locations {' blocks
    blocks = re.split(r"locations\s*\{", text)
    for block in blocks[1:]:  # skip preamble before first 'locations {'
        loc: dict = {}

        m = re.search(r'id:\s*"([^"]*)"', block)
        if m:
            loc["id"] = m.group(1)

        m = re.search(r'name:\s*"([^"]*)"', block)
        if m:
            loc["name"] = m.group(1)

        m = re.search(r"x:\s*([-\d.eE]+)", block)
        if m:
            loc["x"] = float(m.group(1))
        m = re.search(r"y:\s*([-\d.eE]+)", block)
        if m:
            loc["y"] = float(m.group(1))
        m = re.search(r"theta:\s*([-\d.eE]+)", block)
        if m:
            loc["theta"] = float(m.group(1))

        m = re.search(r"type:\s*LOCATION_TYPE_(\w+)", block)
        if m:
            loc["type"] = m.group(1).lower()

        if "x" in loc and "y" in loc:
            locations.append(loc)

    return locations


def parse_inline_loc(spec: str) -> dict:
    """Parse id,x,y,theta[,type] from a CLI --loc argument."""
    parts = spec.split(",")
    if len(parts) < 4:
        sys.exit(f"--loc needs at least id,x,y,theta — got: {spec!r}")
    loc = {
        "id": parts[0].strip(),
        "x": float(parts[1]),
        "y": float(parts[2]),
        "theta": float(parts[3]),
    }
    if len(parts) >= 5:
        loc["type"] = parts[4].strip().lower()
    return loc


# ── world → pixel ─────────────────────────────────────────────────────────────
def world_to_pixel(wx: float, wy: float,
                   origin_x: float, origin_y: float,
                   resolution: float, height: int) -> tuple[int, int]:
    """Convert real-world map coords to pixel coords in the PGM image."""
    px = int(round((wx - origin_x) / resolution))
    py = int(round(height - (wy - origin_y) / resolution))
    return px, py


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    args = parse_args()

    # Load map metadata
    with open(args.map) as f:
        map_meta = yaml.safe_load(f)
    resolution = float(map_meta["resolution"])
    origin = map_meta["origin"]
    origin_x, origin_y = origin[0], origin[1]

    map_path = str(Path(args.map).parent / map_meta["image"])
    img = cv2.imread(map_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        sys.exit(f"cannot read map image: {map_path}")
    h, w = img.shape

    # Convert to colour for drawing
    canvas = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # Gather locations
    locations: list[dict] = []
    if args.locations:
        locations.extend(parse_locations_file(args.locations))
    for spec in args.loc:
        locations.append(parse_inline_loc(spec))

    if not locations:
        sys.exit("no locations provided — use --locations <file> or --loc id,x,y,theta[,type]")

    arrow_px = int(args.arrow_len / resolution)

    for loc in locations:
        px, py = world_to_pixel(loc["x"], loc["y"], origin_x, origin_y,
                                resolution, h)
        ltype = loc.get("type", "unspecified")
        colour = TYPE_COLOURS.get(ltype, DEFAULT_COLOUR)

        # Draw a filled circle for the landmark position
        cv2.circle(canvas, (px, py), 6, colour, -1)
        cv2.circle(canvas, (px, py), 6, (255, 255, 255), 1)

        # Draw heading arrow
        theta = loc.get("theta", 0.0)
        dx = int(arrow_px * math.cos(theta))
        dy = int(arrow_px * math.sin(theta))
        # image y is flipped relative to world y
        end = (px + dx, py - dy)
        cv2.arrowedLine(canvas, (px, py), end, colour, 2, tipLength=0.3)

        # Label: id (and name if present)
        label = loc["id"]
        if "name" in loc and loc["name"]:
            label = f"{loc['id']} ({loc['name']})"
        cv2.putText(canvas, label, (px + 10, py - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, args.font_scale,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, label, (px + 10, py - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, args.font_scale,
                    colour, 1, cv2.LINE_AA)

    cv2.imwrite(args.output, canvas)
    print(f"wrote {args.output}  ({len(locations)} landmarks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())