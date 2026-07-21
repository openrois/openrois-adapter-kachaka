import { useState, useEffect } from "react";
import type { RoISClient } from "@openrois/sdk";
import { NAVIGATION_REF } from "../config";

export interface Waypoint {
  id: string;
  name: string;
  pose: { x: number; y: number; theta: number };
}

/**
 * Fetch waypoints from the Navigation component once on connect.
 *
 * The gateway returns a Result[] where each Result has name="waypoint"
 * and value is a JSON string with { id, name, pose: { x, y, theta } }.
 */
export function useWaypoints(client: RoISClient | null): Waypoint[] {
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);

  useEffect(() => {
    if (!client) return;

    client
      .query(NAVIGATION_REF, "waypoints")
      .then((results) => {
        const wps = results
          .filter((r) => r.name === "waypoint")
          .map((r) => JSON.parse(r.value) as Waypoint);
        setWaypoints(wps);
      })
      .catch(() => {
        // Ignore query errors. Waypoints will be empty.
      });
  }, [client]);

  return waypoints;
}