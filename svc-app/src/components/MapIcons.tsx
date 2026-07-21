/**
 * SVG icon components for map overlays.
 *
 * Both icons are 24x24 viewBox SVGs from Heroicons, inlined as React components
 * so we can control fill color dynamically (green for normal waypoints, blue
 * for selected, red for the robot).
 */

interface IconProps {
  /** Pixel x position on the map image. */
  x: number;
  /** Pixel y position on the map image. */
  y: number;
  /** Scale factor (1 = original 24px size). */
  scale: number;
  /** Fill color (CSS color string). */
  color: string;
}

/**
 * Waypoint pin icon (map-pin.svg).
 *
 * The pin's bottom tip is at (12, 24) in the 24x24 viewBox.
 * The transform places the tip at (x, y) and scales around that point.
 */
export function WaypointPin({ x, y, scale, color }: IconProps) {
  return (
    <g transform={`translate(${x}, ${y}) scale(${scale}) translate(-12, -24)`}>
      <path
        fillRule="evenodd"
        d="m11.54 22.351.07.04.028.016a.76.76 0 0 0 .723 0l.028-.015.071-.041a16.975 16.975 0 0 0 1.144-.742 19.58 19.58 0 0 0 2.683-2.282c1.944-1.99 3.963-4.98 3.963-8.827a8.25 8.25 0 0 0-16.5 0c0 3.846 2.02 6.837 3.963 8.827a19.58 19.58 0 0 0 2.682 2.282 16.975 16.975 0 0 0 1.145.742ZM12 13.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"
        clipRule="evenodd"
        fill={color}
      />
    </g>
  );
}

/**
 * Robot marker icon (arrow-right-circle.svg).
 *
 * The circle center is at (12, 12) in the 24x24 viewBox.
 * The transform places the center at (x, y), rotates by theta (radians),
 * and scales. The arrow points right (east) at theta=0.
 */
interface RobotMarkerProps extends IconProps {
  /** Heading angle in radians. 0 = east (positive X). */
  theta: number;
}

export function RobotMarker({ x, y, theta, scale, color }: RobotMarkerProps) {
  // Negate theta because SVG rotate() is clockwise (Y-down) but the map
  // coordinate system is Y-up (counterclockwise). The worldToPixel function
  // flips the Y axis, so the rotation direction must also be flipped.
  const degrees = -(theta * 180) / Math.PI;
  return (
    <g transform={`translate(${x}, ${y}) rotate(${degrees}) scale(${scale}) translate(-12, -12)`}>
      <path
        fillRule="evenodd"
        d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25Zm4.28 10.28a.75.75 0 0 0 0-1.06l-3-3a.75.75 0 1 0-1.06 1.06l1.72 1.72H8.25a.75.75 0 0 0 0 1.5h5.69l-1.72 1.72a.75.75 0 1 0 1.06 1.06l3-3Z"
        clipRule="evenodd"
        fill={color}
      />
    </g>
  );
}