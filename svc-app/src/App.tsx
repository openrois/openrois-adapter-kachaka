import { useState, useEffect } from "react";
import { GATEWAY_URL, NAVIGATION_REF } from "./config";
import { useRoISClient } from "./hooks/useRoISClient";
import { useWaypoints } from "./hooks/useWaypoints";
import { useRobotPosition } from "./hooks/useRobotPosition";
import { useComponentStatus } from "./hooks/useComponentStatus";
import { useReachedTarget } from "./hooks/useReachedTarget";
import { ConnectionIndicator } from "./components/ConnectionIndicator";
import { MapView } from "./components/MapView";
import { StatusPanel } from "./components/StatusPanel";
import "./App.css";

function App() {
  const { client, connected, error } = useRoISClient(GATEWAY_URL);
  const waypoints = useWaypoints(client);
  const robotPosition = useRobotPosition(client);
  const status = useComponentStatus(client);
  const { target: arrivedTarget } = useReachedTarget(client);
  const [selectedWaypoint, setSelectedWaypoint] = useState<string | null>(null);
  const [commandId, setCommandId] = useState<string | null>(null);
  const [arrivalMessage, setArrivalMessage] = useState<string | null>(null);

  // Clear selection and show arrival message when reached_target fires.
  // The effect synchronizes with an external event (the subscription),
  // so setState here is the correct pattern.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (arrivedTarget) {
      setSelectedWaypoint(null);
      setCommandId(null);
      setArrivalMessage(`Arrived at ${arrivedTarget}`);
      // Clear the arrival message after 5 seconds.
      const timer = setTimeout(() => setArrivalMessage(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [arrivedTarget]);
  /* eslint-enable react-hooks/set-state-in-effect */

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
          arrivalMessage={arrivalMessage}
          onNavigate={handleNavigate}
          onClearSelection={() => setSelectedWaypoint(null)}
        />
      </div>
    </div>
  );
}

export default App;
