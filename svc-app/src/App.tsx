import { useState } from "react";
import { GATEWAY_URL } from "./config";
import { useRoISClient } from "./hooks/useRoISClient";
import { useWaypoints } from "./hooks/useWaypoints";
import { useRobotPosition } from "./hooks/useRobotPosition";
import { ConnectionIndicator } from "./components/ConnectionIndicator";
import { MapView } from "./components/MapView";
import "./App.css";

function App() {
  const { client, connected, error } = useRoISClient(GATEWAY_URL);
  const waypoints = useWaypoints(client);
  const robotPosition = useRobotPosition(client);
  const [selectedWaypoint, setSelectedWaypoint] = useState<string | null>(null);

  return (
    <div className="app">
      <h1>Kachaka Map</h1>
      <ConnectionIndicator connected={connected} error={error} />
      <div className="map-container">
        <MapView
          waypoints={waypoints}
          robotPosition={robotPosition}
          selectedWaypoint={selectedWaypoint}
          onSelectWaypoint={setSelectedWaypoint}
        />
      </div>
    </div>
  );
}

export default App;
