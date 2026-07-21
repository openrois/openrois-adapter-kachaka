import { useState } from "react";
import { GATEWAY_URL, NAVIGATION_REF } from "./config";
import { useRoISClient } from "./hooks/useRoISClient";
import { useWaypoints } from "./hooks/useWaypoints";
import { useRobotPosition } from "./hooks/useRobotPosition";
import { useComponentStatus } from "./hooks/useComponentStatus";
import { ConnectionIndicator } from "./components/ConnectionIndicator";
import { MapView } from "./components/MapView";
import { StatusPanel } from "./components/StatusPanel";
import "./App.css";

function App() {
  const { client, connected, error } = useRoISClient(GATEWAY_URL);
  const waypoints = useWaypoints(client);
  const robotPosition = useRobotPosition(client);
  const status = useComponentStatus(client);
  const [selectedWaypoint, setSelectedWaypoint] = useState<string | null>(null);
  const [commandId, setCommandId] = useState<string | null>(null);

  const handleNavigate = async () => {
    if (!client || !selectedWaypoint || status === "BUSY") return;
    try {
      const response = await client.execute(NAVIGATION_REF, {
        parameters: [
          {
            name: "target_positions",
            data_type_ref: "string[]",
            value: JSON.stringify([selectedWaypoint]),
          },
        ],
      });
      setCommandId(response.command_id);
    } catch {
      // Error is visible via status polling.
    }
  };

  return (
    <div className="app">
      <h1>Kachaka Map</h1>
      <ConnectionIndicator connected={connected} error={error} />
      <div className="main">
        <div className="map-container">
          <MapView
            waypoints={waypoints}
            robotPosition={robotPosition}
            selectedWaypoint={selectedWaypoint}
            onSelectWaypoint={setSelectedWaypoint}
          />
        </div>
        <StatusPanel
          connected={connected}
          status={status}
          selectedWaypoint={selectedWaypoint}
          commandId={commandId}
          onNavigate={handleNavigate}
          onClearSelection={() => setSelectedWaypoint(null)}
        />
      </div>
    </div>
  );
}

export default App;
