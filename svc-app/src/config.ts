/**
 * Hardcoded constants for the Kachaka map demo.
 * Update if the map or gateway changes.
 */

/** Gateway WebSocket URL. */
export const GATEWAY_URL = "ws://localhost:8765";

/** Map metadata from maps/unicorn/kachaka_map.yaml. */
export const MAP_RESOLUTION = 0.025;
export const MAP_ORIGIN_X = -2.1557;
export const MAP_ORIGIN_Y = -7.83943;

/** Map image dimensions in pixels (from the WebP file). */
export const MAP_IMAGE_WIDTH = 447;
export const MAP_IMAGE_HEIGHT = 514;

/** Component refs returned by the adapter. */
export const NAVIGATION_REF = "kachaka/Navigation";
export const SYSTEM_INFO_REF = "kachaka/SystemInformation";

/** Polling intervals in milliseconds. */
export const POSITION_POLL_MS = 250;
export const STATUS_POLL_MS = 1000;

/**
 * Convert world coordinates (meters, from Kachaka) to pixel coordinates
 * on the map image.
 *
 * The Y axis is flipped because the map origin is bottom-left but the
 * image origin is top-left.
 */
export function worldToPixel(x: number, y: number): [number, number] {
  const px = (x - MAP_ORIGIN_X) / MAP_RESOLUTION;
  const py = MAP_IMAGE_HEIGHT - (y - MAP_ORIGIN_Y) / MAP_RESOLUTION;
  return [px, py];
}