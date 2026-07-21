import { MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT, GATEWAY_URL } from "./config";
import { useRoISClient } from "./hooks/useRoISClient";
import { ConnectionIndicator } from "./components/ConnectionIndicator";
import "./App.css";

function App() {
  const { connected, error } = useRoISClient(GATEWAY_URL);

  return (
    <div className="app">
      <h1>Kachaka Map</h1>
      <ConnectionIndicator connected={connected} error={error} />
      <div className="map-container">
        <svg
          width={MAP_IMAGE_WIDTH}
          height={MAP_IMAGE_HEIGHT}
          viewBox={`0 0 ${MAP_IMAGE_WIDTH} ${MAP_IMAGE_HEIGHT}`}
          preserveAspectRatio="xMidYMid meet"
          style={{ maxWidth: "100%", height: "auto", display: "block" }}
        >
          <image
            href="/kachaka_map.webp"
            x={0}
            y={0}
            width={MAP_IMAGE_WIDTH}
            height={MAP_IMAGE_HEIGHT}
          />
        </svg>
      </div>
    </div>
  );
}

export default App;
