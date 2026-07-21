import { useState, useEffect, useRef } from "react";
import type { RoISClient } from "@openrois/sdk";
import { SYSTEM_INFO_REF, POSITION_POLL_MS } from "../config";

interface RobotPosition {
  x: number;
  y: number;
  theta: number;
}

/**
 * Poll robot_position from the SystemInformation component at a fixed interval.
 *
 * The gateway returns a Result[] with a "position_data" result whose value
 * is a JSON array of strings: ["1.23", "4.56", "0.78"] (x, y, theta).
 */
export function useRobotPosition(
  client: RoISClient | null,
  intervalMs: number = POSITION_POLL_MS,
): RobotPosition | null {
  const [position, setPosition] = useState<RobotPosition | null>(null);
  const clientRef = useRef(client);
  clientRef.current = client;

  useEffect(() => {
    if (!client) return;

    let cancelled = false;

    const poll = async () => {
      try {
        const c = clientRef.current;
        if (!c || cancelled) return;

        const results = await c.query(SYSTEM_INFO_REF, "robot_position");
        if (cancelled) return;

        const posData = results.find((r) => r.name === "position_data");
        if (posData) {
          const [x, y, theta] = JSON.parse(posData.value).map(Number);
          setPosition({ x, y, theta });
        }
      } catch {
        // Ignore polling errors. Position stays at last known value.
      }
    };

    poll(); // immediate first poll
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [client, intervalMs]);

  return position;
}