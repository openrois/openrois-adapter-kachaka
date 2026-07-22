import { useState, useEffect } from "react";
import type { RoISClient } from "@openrois/sdk";
import type { EventEmitter } from "events";
import { NavigationReachedTargetEventSchema } from "@openrois/interfaces/components";
import { NAVIGATION_REF } from "../config";

interface ReachedTargetEvent {
  target: string | null;
  isFinal: boolean | null;
}

/**
 * Subscribe to reached_target events from the Navigation component.
 *
 * The gateway pushes a rois.event.notify notification when the robot
 * reaches its target. The SDK emits the event_type ("reached_target")
 * as a convenience event. The notification params contain a results
 * array with { name: "target", value: "Kitchen" } and
 * { name: "is_final_target", value: "true" }.
 *
 * The payload is parsed into a NavigationReachedTargetEvent via its zod
 * schema for type safety.
 *
 * Returns the last arrival event. Unsubscribes on unmount.
 */
export function useReachedTarget(client: RoISClient | null): ReachedTargetEvent {
  const [event, setEvent] = useState<ReachedTargetEvent>({
    target: null,
    isFinal: null,
  });

  useEffect(() => {
    if (!client) return;

    let subId: string | null = null;

    // Subscribe to the reached_target event.
    client
      .subscribe(NAVIGATION_REF, "reached_target")
      .then((id) => {
        subId = id;
      })
      .catch(() => {
        // Ignore subscription errors.
      });

    // Listen for the convenience event emitted by the SDK.
    // RoISClient extends EventEmitter but the type declaration does not
    // expose on/off directly, so we cast to EventEmitter.
    const emitter = client as unknown as EventEmitter;
    const handler = (notification: {
      params: { results?: { name: string; value: string }[] };
    }) => {
      const results = notification.params?.results ?? [];
      const target = results.find((r) => r.name === "target");
      const isFinal = results.find((r) => r.name === "is_final_target");
      if (target && isFinal) {
        const parsed = NavigationReachedTargetEventSchema.parse({
          target: target.value,
          is_final_target: isFinal.value === "true",
        });
        setEvent({
          target: parsed.target,
          isFinal: parsed.is_final_target,
        });
      }
    };

    emitter.on("reached_target", handler);

    return () => {
      emitter.off("reached_target", handler);
      if (subId) {
        client.unsubscribe(subId).catch(() => {});
      }
    };
  }, [client]);

  return event;
}