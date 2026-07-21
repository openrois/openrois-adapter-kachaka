interface StatusPanelProps {
  connected: boolean;
  status: string;
  selectedWaypoint: string | null;
  commandId: string | null;
  onNavigate: () => void;
  onClearSelection: () => void;
}

/**
 * Side panel showing connection state, component status, selected waypoint,
 * command ID, and a Navigate button.
 */
export function StatusPanel({
  connected,
  status,
  selectedWaypoint,
  commandId,
  onNavigate,
  onClearSelection,
}: StatusPanelProps) {
  const canNavigate = connected && selectedWaypoint !== null && status !== "BUSY";
  const statusColor =
    status === "BUSY" ? "#eab308" : status === "ERROR" ? "#ef4444" : "#22c55e";

  return (
    <div className="status-panel">
      <h2>Status</h2>

      <div className="status-row">
        <span className="status-label">Connection</span>
        <span className="status-value">
          {connected ? "Connected" : "Disconnected"}
        </span>
      </div>

      <div className="status-row">
        <span className="status-label">Robot</span>
        <span className="status-value" style={{ color: statusColor }}>
          {status}
        </span>
      </div>

      <div className="status-row">
        <span className="status-label">Target</span>
        <span className="status-value">
          {selectedWaypoint ?? "None"}
        </span>
      </div>

      {commandId && (
        <div className="status-row">
          <span className="status-label">Command</span>
          <span className="status-value mono">{commandId}</span>
        </div>
      )}

      <div className="status-actions">
        <button
          type="button"
          className="navigate-button"
          onClick={onNavigate}
          disabled={!canNavigate}
        >
          Navigate
        </button>
        {selectedWaypoint && (
          <button
            type="button"
            className="clear-button"
            onClick={onClearSelection}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}