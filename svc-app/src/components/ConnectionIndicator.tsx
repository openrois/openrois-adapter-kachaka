interface ConnectionIndicatorProps {
  connected: boolean;
  error: string | null;
}

/**
 * Colored dot + text showing the gateway connection state.
 *
 * Green dot: connected.
 * Red dot: error (with message).
 * Yellow dot: connecting (not connected, no error).
 */
export function ConnectionIndicator({ connected, error }: ConnectionIndicatorProps) {
  const color = connected ? "#22c55e" : error ? "#ef4444" : "#eab308";
  const label = connected
    ? "Connected"
    : error
      ? `Error: ${error}`
      : "Connecting...";

  return (
    <div className="connection-indicator">
      <span
        className="connection-dot"
        style={{ backgroundColor: color }}
      />
      <span className="connection-text">{label}</span>
    </div>
  );
}