import { useState, useEffect } from "react";
import type { RoISClient } from "@openrois/sdk";
import { SystemInformationRobotPositionResultSchema } from "@openrois/interfaces/components";
import { SYSTEM_INFO_REF, POSITION_POLL_MS } from "../config";

interface RobotPosition {
  x: number;
  y: number;
  theta: number;
}

/**
 * Poll robot_position from the SystemInformation component at a fixed interval.
 *
 * The gateway returns a Result[] with a single Result whose name is
 * "robot_position" and whose value is a JSON-serialised
 * SystemInformationRobotPositionResult model:
 * { timestamp, robot_ref, position_data: ["x", "y", "theta"] }.
 */
export function useRobotPosition(
  client: RoISClient | null,
  intervalMs: number = POSITION_POLL_MS,
): RobotPosition | null {
  const [position, setPosition] = useState<RobotPosition | null>(null);

  useEffect(() => {
    if (!client) return;

    let cancelled = false;

    const poll = async () => {
      try {
        if (cancelled) return;

        const results = await client.query(SYSTEM_INFO_REF, "robot_position");
        if (cancelled) return;

        const posResult = results.find((r) => r.name === "robot_position");
        if (posResult) {
          const model = SystemInformationRobotPositionResultSchema.parse(
            JSON.parse(posResult.value),
          );
          const [x, y, theta] = model.position_data.map(Number);
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