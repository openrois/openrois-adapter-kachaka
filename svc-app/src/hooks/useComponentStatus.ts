import { useState, useEffect } from "react";
import type { RoISClient } from "@openrois/sdk";
import { NAVIGATION_REF, STATUS_POLL_MS } from "../config";

/**
 * Poll component_status from the Navigation component at a fixed interval.
 *
 * Returns "READY", "BUSY", or "ERROR". Returns "UNKNOWN" until the first
 * successful poll.
 */
export function useComponentStatus(
  client: RoISClient | null,
  intervalMs: number = STATUS_POLL_MS,
): string {
  const [status, setStatus] = useState("UNKNOWN");

  useEffect(() => {
    if (!client) return;

    let cancelled = false;

    const poll = async () => {
      try {
        if (cancelled) return;

        const results = await client.query(NAVIGATION_REF, "component_status");
        if (cancelled) return;

        const statusResult = results.find((r) => r.name === "status");
        if (statusResult) {
          setStatus(statusResult.value);
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