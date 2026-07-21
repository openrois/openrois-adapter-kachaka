import { MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT, worldToPixel } from "../config";
import { WaypointPin } from "./MapIcons";
import type { Waypoint } from "../hooks/useWaypoints";

interface MapViewProps {
  waypoints: Waypoint[];
  robotPosition: { x: number; y: number; theta: number } | null;
  selectedWaypoint: string | null;
  onSelectWaypoint: (name: string) => void;
}

/**
 * SVG-based map with waypoint pin overlays.
 *
 * The robot marker is not rendered yet (stage 4).
 */
export function MapView({
  waypoints,
  robotPosition,
  selectedWaypoint,
  onSelectWaypoint,
}: MapViewProps) {
  return (
    <svg
      width={MAP_IMAGE_WIDTH}
      height={MAP_IMAGE_HEIGHT}
      viewBox={`0 0 ${MAP_IMAGE_WIDTH} ${MAP_IMAGE_HEIGHT}`}
      preserveAspectRatio="xMidYMid meet"
      style={{ maxWidth: "100%", height: "auto", display: "block" }}
    >
      {/* Map background */}
      <image
        href="/kachaka_map.webp"
        x={0}
        y={0}
        width={MAP_IMAGE_WIDTH}
        height={MAP_IMAGE_HEIGHT}
      />

      {/* Waypoint pins */}
      {waypoints.map((wp) => {
        const [px, py] = worldToPixel(wp.pose.x, wp.pose.y);
        const isSelected = wp.name === selectedWaypoint;
        return (
          <g
            key={wp.id}
            onClick={() => onSelectWaypoint(wp.name)}
            style={{ cursor: "pointer" }}
          >
            <WaypointPin
              x={px}
              y={py}
              scale={isSelected ? 1.3 : 1.0}
              color={isSelected ? "#ef4444" : "#6b7280"}
            />
            <text
              x={px}
              y={py + 16}
              textAnchor="middle"
              fill="#ffffff"
              stroke="#000000"
              strokeWidth={3}
              paintOrder="stroke"
              fontSize={11}
              fontFamily="sans-serif"
            >
              {wp.name}
            </text>
          </g>
        );
      })}

      {/* Robot marker placeholder (stage 4) */}
      {robotPosition && null}
    </svg>
  );
}