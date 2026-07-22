import { useState, useEffect } from "react";
import type { RoISClient } from "@openrois/sdk";
import { ComponentStatusSchema, type ComponentStatus } from "@openrois/interfaces/common";
import { NAVIGATION_REF, STATUS_POLL_MS } from "../config";

/**
 * Poll component_status from the Navigation component at a fixed interval.
 *
 * The gateway returns a Result[] with a single Result whose name is
 * "component_status" and whose value is the ComponentStatus enum string
 * ("READY", "BUSY", etc.).
 *
 * Returns "UNKNOWN" until the first successful poll.
 */
export function useComponentStatus(
  client: RoISClient | null,
  intervalMs: number = STATUS_POLL_MS,
): ComponentStatus | "UNKNOWN" {
  const [status, setStatus] = useState<ComponentStatus | "UNKNOWN">("UNKNOWN");

  useEffect(() => {
    if (!client) return;

    let cancelled = false;

    const poll = async () => {
      try {
        if (cancelled) return;

        const results = await client.query(NAVIGATION_REF, "component_status");
        if (cancelled) return;

        const statusResult = results.find((r) => r.name === "component_status");
        if (statusResult) {
          setStatus(ComponentStatusSchema.parse(statusResult.value));
        }
      } catch {
        // Ignore polling errors. Status stays at last known value.
      }
    };

    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [client, intervalMs]);

  return status;
}