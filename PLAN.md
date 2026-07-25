# OpenRoIS Kachaka Integration Plan

> Implementation plan for the Kachaka BusAdapter, thin gateway, and service
> application. This is a fast-iteration prototype for internal testing against
> the Kachaka development robot. The goal is a working end-to-end test where a
> web client sends the robot to named waypoints and views its position on a map
> through the OpenRoIS middleware.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Bus Adapter (Intern)](#4-bus-adapter-intern)
5. [Gateway (Lead Engineer)](#5-gateway-lead-engineer)
6. [Service Application (svc-app)](#6-service-application-svc-app)
7. [Schedule](#7-schedule)
8. [End-to-End Test](#8-end-to-end-test)
9. [Decisions](#9-decisions)

---

## 1. Overview

### What this is

A non-canonical `BusAdapter` implementation that bridges the OMG RoIS 2.0
middleware contract to the Kachaka robot's gRPC API. The adapter exposes
Navigation and SystemInformation components through the `BusAdapter` contract.
A thin WebSocket gateway dispatches JSON-RPC 2.0 messages from a web client to
the adapter. The web client renders a map with waypoint overlays and a robot
position marker.

### What this is not

- Not the canonical adapter for the ATR demo (that is the `ROS2BusAdapter`, M3).
- Not a formal OpenRoIS release (no npm publish, no conformance claim).
- Not a maintained package (no test suite, no CI, no versioning beyond a tag).
- Not a multi-operator system (no session manager, no `bind`/`release` lifecycle).

### Scope

| In scope | Out of scope |
|----------|--------------|
| Navigate between named waypoints | Navigate to arbitrary x,y,yaw coordinates |
| Query robot position | Query battery level |
| Query waypoints (named locations) | Object detection, surface detection, manipulation |
| Subscribe to `reached_target` events | WebRTC video streaming |
| Component status (BUSY/READY) | Authentication, RBAC |
| Map rendering with overlays | Multi-operator session management |
| Mock adapter for testing without robot | ROS 2 bridge (the `ros2_ws/` path is separate) |

---

## 2. Architecture

```
svc-app (React + Vite)           gateway (Python, asyncio)       Kachaka robot
                                 (one process, thin gateway)     (built-in gRPC)
  RoISClient (TS SDK)              WSServer                       gRPC server
    │ WebSocket + JSON-RPC 2.0     RoISHandler                    port 26400
    ▼                              KachakaAdapter (or MockAdapter)
  Canvas map overlay                 │ gRPC (port 26400)
  Waypoint icons                     ▼
  Robot position marker            Kachaka robot
```

### Data flow

1. The web client connects to the gateway over WebSocket.
2. The client calls `search()` to discover components.
3. The client calls `query("waypoints")` once to get the location list.
4. The client polls `query("robot_position")` every 500ms for the map marker.
5. The client calls `execute({target_positions: ["Kitchen"]})` to navigate.
6. The adapter calls `move_to_location("Kitchen")` via gRPC.
7. The adapter polls `get_command_state()` and emits `reached_target` when done.
8. The gateway pushes the event to the client as a JSON-RPC notification.
9. The client updates the map (clears the selected waypoint).

### What goes over each boundary

| Boundary | Protocol | Data |
|----------|----------|------|
| Client to Gateway | WebSocket + JSON-RPC 2.0 | RoIS symbolic data (position, waypoints, commands, events) |
| Gateway to Adapter | In-process async method call | `BusAdapter` protocol (discover, query, invoke, subscribe) |
| Adapter to Robot | gRPC (port 26400) | Kachaka API calls (get_robot_pose, move_to_location, get_command_state) |
| Client to Map image | Static file (WebP in `public/`) | Map PNG from Kachaka, converted to WebP, served by Vite |

---

## 3. Repository Structure

```
openrois-kachaka/
├── bus-adapter/                        # gRPC BusAdapter package (intern's scope)
│   ├── README.md
│   ├── pyproject.toml                  # openrois-bus-kachaka
│   ├── src/
│   │   └── openrois_bus_kachaka/
│   │       ├── __init__.py
│   │       ├── py.typed
│   │       ├── adapter.py              # KachakaAdapter (BusAdapter impl)
│   │       ├── mapping.py              # kachaka protobuf <-> RoIS conversion
│   │       ├── poller.py               # asyncio polling task for events
│   │       └── config.py               # KachakaAdapterConfig
│   └── (no tests/ directory)
│
├── gateway/                            # thin WebSocket gateway (lead engineer's scope)
│   ├── README.md
│   ├── main.py                          # composition root: argparse, wire adapter + handler + server
│   ├── handler.py                       # RoISHandler: JSON-RPC method dispatch to BusAdapter
│   ├── server.py                        # WSServer: WebSocket accept, JSON-RPC framing, event push
│   └── mock_adapter.py                  # MockAdapter: hardcoded responses for testing without robot
│
├── svc-app/                             # web service application (React + Vite)
│   ├── package.json                     # depends on @openrois/sdk (linked from monorepo)
│   ├── public/
│   │   └── kachaka_map.webp             # map image (converted from PNG, served by Vite)
│   └── src/
│       ├── App.tsx                      # root component, wires client + map
│       ├── hooks/
│       │   ├── useRoISClient.ts         # connect/disconnect lifecycle
│       │   ├── useRobotPosition.ts      # poll robot_position every 500ms
│       │   └── useWaypoints.ts          # fetch waypoints once on connect
│       └── components/
│           └── MapView.tsx              # canvas map with waypoint + robot overlays
│
├── kachaka-api/                         # vendored submodule (gRPC SDK)
├── maps/                                # map assets
│   └── unicorn/
│       ├── kachaka_map.pgm              # raw occupancy grid
│       ├── kachaka_map.yaml             # map metadata (resolution, origin)
│       ├── kachaka_map_preview.png      # map preview image
│       └── kachaka_map_preview_landmarks.png
├── vendor/                              # vendored wheels
│   └── openrois_interfaces-0.1.0a2-py3-none-any.whl
└── ros2_ws/                             # ROS 2 path (canonical, not in scope for this plan)
```

---

## 4. Bus Adapter (Intern)

### Scope

The intern builds `bus-adapter/` only. No gateway, no tests, no svc-app. The
adapter implements the `BusAdapter` protocol from `openrois-interfaces` against
the Kachaka gRPC API.

### Duration

2 days (full-time). Pre-work reading before day 1.

### Pre-work (before day 1)

The intern reads these materials before starting:

1. `openrois/interfaces/python/src/openrois/interfaces/bus.py` on GitHub: the
   `BusAdapter` protocol, request/response models, `EventEnvelope`, `EventSink`.
2. `openrois/interfaces/python/src/openrois/interfaces/components/navigation.py`
   on GitHub: `NavigationSetParameter`, `NavigationReachedTargetEvent`, etc.
3. `openrois/interfaces/python/src/openrois/interfaces/components/system_information.py`
   on GitHub: `SystemInformationRobotPositionResult`.
4. `kachaka-api/python/kachaka_api/aio/base.py` in the submodule: async client
   methods (`get_robot_pose`, `get_locations`, `move_to_location`,
   `get_command_state`, `cancel_command`).
5. Architecture doc section 7.5 on GitHub: The BusAdapter Interface.

### Environment setup

```bash
git clone <openrois-kachaka-repo> && cd openrois-kachaka
git submodule update --init
pip install vendor/openrois_interfaces-0.1.0a2-py3-none-any.whl
pip install -e bus-adapter/
```

### Kachaka-to-RoIS mapping

| RoIS operation | Kachaka gRPC call | RoIS result type |
|---|---|---|
| `discover()` | (none, static list) | `["kachaka/Navigation", "kachaka/SystemInformation"]` |
| `query("robot_position")` | `get_robot_pose()` | `SystemInformationRobotPositionResult` (x, y, theta) |
| `query("waypoints")` | `get_locations()` | Custom: list of `{id, name, pose}` |
| `query("component_status")` | `get_command_state()` | `NavigationStatusResult` (BUSY/READY) |
| `invoke(EXECUTE, ["Kitchen"])` | `move_to_location("Kitchen", wait_for_completion=False)` | `InvokeResponse` with command_id |
| `invoke(EXECUTE, ["unknown"])` | (none) | `InvokeResponse` with `BAD_PARAMETER` |
| `invoke(STOP)` | `cancel_command()` | `InvokeResponse` |
| `subscribe("reached_target")` | poll `get_command_state()` | `EventEnvelope` with target + is_final_target |
| `unsubscribe(sub_id)` | cancel poller task | `ReturnCode.OK` |

### Component status mapping

| Kachaka `CommandState` | RoIS `ComponentStatus` |
|---|---|
| `RUNNING` | `BUSY` |
| `SUCCESS` | `READY` |
| `FAILURE` | `ERROR` |
| `CANCEL` | `READY` |
| (no command) | `READY` |

### Constraints

- Navigation is between named locations only. No `move_to_pose(x, y, yaw)`.
  Unknown location names return `BAD_PARAMETER`.
- No battery level query.
- Use the async kachaka client (`kachaka_api.aio.KachakaApiClient`), not the
  sync client. The `BusAdapter` protocol is async.
- `mypy --strict` clean. `ruff check` clean. No `Any` types.

### Day-by-day plan

#### Day 1: All five BusAdapter methods

**Morning:** Scaffold the package. Implement `config.py`, `mapping.py`,
`discover()`, `query()` (robot_position, waypoints, component_status), and
`invoke()` (EXECUTE, STOP, SET_PARAMETER). Verify in a Python REPL against a
real Kachaka.

**Afternoon:** Implement `poller.py` with `CommandStatePoller`. Implement
`subscribe()` and `unsubscribe()`. Verify: subscribe to `reached_target`,
execute a navigation command, confirm the event fires on arrival. Run
`mypy --strict` and `ruff check`.

#### Day 2: Integration + end-to-end + README

**Morning:** Coordinate with the lead engineer to integrate the adapter into
`gateway/`. Run the full end-to-end test through the gateway against a real
Kachaka. Debug issues.

**Afternoon:** Write `bus-adapter/README.md` with the mapping table. Write
docstrings. Final presentation to the team.

### Deliverables checklist

- [ ] `adapter.py`, `mapping.py`, `poller.py`, `config.py` implemented
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
- [ ] End-to-end test passing against a real Kachaka through the gateway
- [ ] `bus-adapter/README.md` with mapping table
- [ ] All public API documented with docstrings
- [ ] Final presentation delivered

---

## 5. Gateway (Lead Engineer)

### Scope

The lead engineer builds `gateway/`. The gateway is a thin WebSocket server
that dispatches JSON-RPC 2.0 messages to the `BusAdapter`. It is a placeholder
replaced by the monorepo's `openrois-engine` and `openrois-gateway` packages
when they land (M1/M2). The adapter does not change.

### Effort

6-9 hours. Half a day to a full day.

### Files

| File | Responsibility |
|------|----------------|
| `main.py` | Composition root: argparse, create adapter (real or mock), handler, server |
| `handler.py` | RoISHandler: JSON-RPC method dispatch to BusAdapter |
| `server.py` | WSServer: WebSocket accept, JSON-RPC framing, event push |
| `mock_adapter.py` | MockAdapter: hardcoded responses for testing without robot |

### JSON-RPC methods implemented

| Method | Behavior |
|--------|----------|
| `rois.system.connect` | No-op, returns `OK` |
| `rois.system.disconnect` | No-op, returns `OK` |
| `rois.command.search` | Routes to `adapter.discover()` |
| `rois.command.bind` | No-op, returns `OK` |
| `rois.command.release` | No-op, returns `OK` |
| `rois.command.set_parameter` | Routes to `adapter.invoke(SET_PARAMETER)` |
| `rois.command.execute` | Routes to `adapter.invoke(EXECUTE)` |
| `rois.query.query` | Routes to `adapter.query()` |
| `rois.event.subscribe` | Routes to `adapter.subscribe()`, pushes events to client |
| `rois.event.unsubscribe` | Routes to `adapter.unsubscribe()` |

### Server-to-client notifications

| Notification | When |
|--------------|------|
| `rois.event.notify` | Component event (e.g. `reached_target`) |
| `rois.command.completed` | Command completion |

### Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | `192.168.1.100:26400` | Kachaka gRPC target address |
| `--host` | `0.0.0.0` | WebSocket bind host |
| `--port` | `8765` | WebSocket bind port |
| `--poll-interval` | `0.5` | Event poll interval (seconds) |
| `--mock` | off | Use mock adapter instead of real Kachaka |

### Usage

```bash
# With mock adapter (no robot needed)
pip install websockets
pip install ../vendor/openrois_interfaces-0.1.0a2-py3-none-any.whl
python gateway/main.py --mock --port 8765

# With real Kachaka
pip install -e ../bus-adapter/
python gateway/main.py --target 192.168.1.100:26400 --port 8765
```

### Status

All four files are implemented and have no errors. The gateway can be tested
immediately with `--mock` (no robot, no intern's adapter needed).

---

## 6. Service Application (svc-app)

### Scope

A React + Vite web app that uses `@openrois/sdk` (the TypeScript client SDK from
the `openrois` monorepo) to connect to the gateway, query robot position and
waypoints, execute navigation, and render a map with SVG overlays.

### Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Gateway URL | Hardcoded constant | Simplest for a demo. No runtime config UI. |
| Map metadata | Hardcoded constants | Copy from `kachaka_map.yaml` into a TS constants file. |
| Image dimensions | Hardcoded constants | Get once, hardcode. Update if map changes. |
| Rendering | SVG | Better for small overlay count (3-10 waypoints). Native click handling on each element. React-idiomatic (JSX). Partial updates (only robot marker changes at 500ms, no full redraw). |
| Click action | Select then confirm | Click a waypoint to select (highlight). Click a Navigate button to confirm. Prevents accidental navigation. |
| Status display | Side panel | Shows status, current target, command_id, connection state. More information than a text line. |
| Connection UI | Simple text indicator | Colored dot or text: Connecting, Connected, Error. Minimal. |
| Styling | Plain CSS in App.css | No new dependencies. Matches the existing setup. |

### Dependencies

The `svc-app/package.json` already links `@openrois/sdk`:

```json
"@openrois/sdk": "file:../../../openrois/openrois/sdk/typescript"
```

Build the SDK before running the svc-app:

```bash
cd openrois/sdk/typescript
npm install && npm run build
```

### Map rendering

The map is a static WebP image served from `svc-app/public/`. It is not sent
over the RoIS WebSocket (it is not symbolic data). The map metadata (resolution,
origin) comes from `maps/unicorn/kachaka_map.yaml`:

```yaml
resolution: 0.025
origin: [-2.1557, -7.83943, 0.0]
```

### Coordinate transform

World coordinates (meters, from Kachaka) to pixel coordinates on the map image:

```
pixel_x = (world_x - origin_x) / resolution
pixel_y = image_height - (world_y - origin_y) / resolution
```

The Y axis is flipped because the map origin is bottom-left but the image
origin is top-left.

### Convert the map to WebP

```bash
python3 -c "from PIL import Image; Image.open('maps/unicorn/kachaka_map_preview.png').save('svc-app/public/kachaka_map.webp', 'WEBP', quality=90)"
```

Get the image dimensions for the coordinate transform:

```bash
python3 -c "from PIL import Image; print(Image.open('maps/unicorn/kachaka_map_preview.png').size)"
```

### File structure

```
svc-app/src/
├── App.tsx                          # root: wires hooks + MapView + StatusPanel
├── App.css                          # styles for the map UI (plain CSS)
├── config.ts                        # hardcoded constants (gateway URL, map metadata, image dims)
├── hooks/
│   ├── useRoISClient.ts             # connect/disconnect lifecycle
│   ├── useRobotPosition.ts          # poll robot_position every 500ms
│   ├── useWaypoints.ts              # fetch waypoints once on connect
│   ├── useComponentStatus.ts        # poll component_status every 1s
│   └── useReachedTarget.ts          # subscribe to reached_target, clear on arrival
└── components/
    ├── MapView.tsx                  # SVG map with waypoint pins + robot marker
    ├── StatusPanel.tsx              # side panel: status, target, command_id, connection
    └── ConnectionIndicator.tsx      # colored dot + text for connection state
```

### Map icons

Two SVG icons are used as overlays on the map. Place them in `svc-app/public/icons/`:

| Icon | File | Used for | Placement |
|------|------|---------|-----------|
| Map pin | `map-pin.svg` | Waypoint markers | Bottom tip of the pin is at the waypoint position. Pin scales with the SVG viewBox. Selected waypoint uses a larger scale and blue fill. |
| Arrow in circle | `arrow-right-circle.svg` | Robot position and heading | Center of the circle is at the robot position. The arrow rotates by theta to indicate heading direction. |

Both icons are 24x24 viewBox SVGs from Heroicons. They are embedded as `<image>` elements inside the map SVG, or inlined as `<path>` elements for better styling control (fill color changes for selected state).

#### Inlining approach

Inline the SVG paths directly in `MapView.tsx` rather than loading them as external images. This allows dynamic fill colors (green for normal waypoints, blue for selected, red for the robot) and avoids an extra HTTP request. The paths are defined as React components:

```typescript
// Waypoint pin (map-pin.svg path, 24x24 viewBox)
function WaypointPin({ x, y, scale, color }: { x: number; y: number; scale: number; color: string }) {
  // The pin's bottom tip is at (12, 24) in its 24x24 viewBox.
  // Translate so the tip aligns with (x, y), then scale.
  return (
    <g transform={`translate(${x}, ${y}) scale(${scale}) translate(-12, -24)`}>
      <path
        fillRule="evenodd"
        d="m11.54 22.351.07.04.028.016a.76.76 0 0 0 .723 0l.028-.015.071-.041a16.975 16.975 0 0 0 1.144-.742 19.58 19.58 0 0 0 2.683-2.282c1.944-1.99 3.963-4.98 3.963-8.827a8.25 8.25 0 0 0-16.5 0c0 3.846 2.02 6.837 3.963 8.827a19.58 19.58 0 0 0 2.682 2.282 16.975 16.975 0 0 0 1.145.742ZM12 13.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"
        clipRule="evenodd"
        fill={color}
      />
    </g>
  );
}

// Robot marker (arrow-right-circle.svg path, 24x24 viewBox)
function RobotMarker({ x, y, theta, scale, color }: { x: number; y: number; theta: number; scale: number; color: string }) {
  // The circle center is at (12, 12) in its 24x24 viewBox.
  // Translate so the center aligns with (x, y), rotate by theta, then scale.
  return (
    <g transform={`translate(${x}, ${y}) rotate(${theta}) scale(${scale}) translate(-12, -12)`}>
      <path
        fillRule="evenodd"
        d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25Zm4.28 10.28a.75.75 0 0 0 0-1.06l-3-3a.75.75 0 1 0-1.06 1.06l1.72 1.72H8.25a.75.75 0 0 0 0 1.5h5.69l-1.72 1.72a.75.75 0 1 0 1.06 1.06l3-3Z"
        clipRule="evenodd"
        fill={color}
      />
    </g>
  );
}
```

The pin's bottom tip (the pointy end) is at `(12, 24)` in the 24x24 viewBox. The transform `translate(x, y) scale(s) translate(-12, -24)` places the tip at the waypoint's pixel position and scales the icon around that point.

The robot marker's circle center is at `(12, 12)`. The transform `translate(x, y) rotate(theta) scale(s) translate(-12, -12)` places the center at the robot's pixel position, rotates by the heading angle, and scales. The arrow inside the circle points right (east) at theta=0, which matches the standard robotics convention where theta=0 is along the positive X axis.

### Config file (`config.ts`)

```typescript
// Hardcoded constants for the demo. Update if the map or gateway changes.

export const GATEWAY_URL = "ws://localhost:8765";

// Map metadata from maps/unicorn/kachaka_map.yaml
export const MAP_RESOLUTION = 0.025;
export const MAP_ORIGIN_X = -2.1557;
export const MAP_ORIGIN_Y = -7.83943;

// Map image dimensions in pixels (from the WebP file).
// TODO: replace with actual values after converting the map.
export const MAP_IMAGE_WIDTH = 800;
export const MAP_IMAGE_HEIGHT = 600;

// Component refs returned by the adapter.
export const NAVIGATION_REF = "kachaka/Navigation";
export const SYSTEM_INFO_REF = "kachaka/SystemInformation";

// Polling intervals.
export const POSITION_POLL_MS = 500;
export const STATUS_POLL_MS = 1000;
```

### Hooks

#### `useRoISClient`

Connects to the gateway on mount. Returns `{ client, connected, error }`.
On unmount, disconnects. No auto-reconnect (the SDK does not implement it yet).

```typescript
export function useRoISClient(url: string): {
  client: RoISClient | null;
  connected: boolean;
  error: string | null;
};
```

#### `useRobotPosition`

Polls `query("robot_position")` every 500ms. Returns `{ x, y, theta }` or null.

```typescript
export function useRobotPosition(client: RoISClient | null, intervalMs?: number): {
  x: number; y: number; theta: number;
} | null;
```

#### `useWaypoints`

Fetches `query("waypoints")` once on connect. Returns an array of waypoints.

```typescript
interface Waypoint {
  id: string;
  name: string;
  pose: { x: number; y: number; theta: number };
}
export function useWaypoints(client: RoISClient | null): Waypoint[];
```

#### `useComponentStatus`

Polls `query("component_status")` every 1s. Returns `"READY"`, `"BUSY"`, or
`"ERROR"`.

```typescript
export function useComponentStatus(client: RoISClient | null, intervalMs?: number): string;
```

#### `useReachedTarget`

Subscribes to `reached_target` on mount. Returns the last arrival event and
clears the selected waypoint. Unsubscribes on unmount.

```typescript
export function useReachedTarget(client: RoISClient | null): {
  target: string | null;
  isFinal: boolean | null;
};
```

### Components

#### `MapView`

SVG-based map with overlays. The SVG contains:

- `<image>` for the map WebP background.
- `<WaypointPin>` per waypoint (inlined `map-pin.svg` path), with `onClick` to
  select. Green fill normally, blue fill when selected (larger scale). The pin
  tip is positioned at the waypoint's pixel coordinates.
- `<text>` per waypoint label (name), positioned above the pin.
- `<RobotMarker>` (inlined `arrow-right-circle.svg` path) for the robot position.
  Red fill. The circle center is at the robot's pixel coordinates. The arrow
  rotates by theta to show heading direction.
- The SVG viewBox matches the map image dimensions.

Click handling: clicking a waypoint pin sets it as selected. A Navigate button
(in the StatusPanel) sends the `execute` command. This prevents accidental
navigation.

```typescript
interface MapViewProps {
  waypoints: Waypoint[];
  robotPosition: { x: number; y: number; theta: number } | null;
  selectedWaypoint: string | null;
  onSelectWaypoint: (name: string) => void;
}
```

#### `StatusPanel`

Side panel showing:

- Connection state (from `useRoISClient`).
- Component status (READY/BUSY/ERROR, from `useComponentStatus`).
- Current target (the selected waypoint name).
- Command ID (from the last `execute` call).
- Navigate button (disabled when no waypoint selected or status is BUSY).
- On navigate click: calls `client.execute()` with the selected waypoint name.

```typescript
interface StatusPanelProps {
  connected: boolean;
  status: string;
  selectedWaypoint: string | null;
  commandId: string | null;
  onNavigate: () => void;
  onClearSelection: () => void;
}
```

#### `ConnectionIndicator`

A colored dot + text showing the connection state.

```typescript
interface ConnectionIndicatorProps {
  connected: boolean;
  error: string | null;
}
```

### SVG overlay rendering

| Overlay | Source | Frequency | SVG element |
|---------|--------|-----------|-------------|
| Map image | Static WebP in `public/` | Once on load | `<image href="/kachaka_map.webp" />` |
| Waypoint pins | `query("waypoints")` | Once on connect | `<WaypointPin>` (inlined `map-pin.svg` path), green fill, tip at waypoint position |
| Waypoint labels | `query("waypoints")` | Once on connect | `<text>` above each pin |
| Robot marker | `query("robot_position")` | Poll 500ms | `<RobotMarker>` (inlined `arrow-right-circle.svg` path), red fill, rotated by theta |
| Selected waypoint | Click on pin | On click | `<WaypointPin>` with larger scale + blue fill |
| Arrival | `subscribe("reached_target")` | Push event | Clears selected waypoint (React state) |

### App.tsx wiring

```typescript
function App() {
  const { client, connected, error } = useRoISClient(GATEWAY_URL);
  const robotPosition = useRobotPosition(client, POSITION_POLL_MS);
  const waypoints = useWaypoints(client);
  const status = useComponentStatus(client, STATUS_POLL_MS);
  const { target: arrivedTarget } = useReachedTarget(client);

  const [selectedWaypoint, setSelectedWaypoint] = useState<string | null>(null);
  const [commandId, setCommandId] = useState<string | null>(null);

  // Clear selection on arrival.
  useEffect(() => {
    if (arrivedTarget) {
      setSelectedWaypoint(null);
      setCommandId(null);
    }
  }, [arrivedTarget]);

  const handleNavigate = async () => {
    if (!client || !selectedWaypoint || status === "BUSY") return;
    try {
      const response = await client.execute(NAVIGATION_REF, {
        parameters: [
          { name: "target_positions", data_type_ref: "string[]",
            value: JSON.stringify([selectedWaypoint]) },
          ],
        });
      setCommandId(response.command_id);
    } catch (err) {
      // Show error in the status panel.
    }
  };

  return (
    <div className="app">
      <ConnectionIndicator connected={connected} error={error} />
      <div className="main">
        <MapView
          waypoints={waypoints}
          robotPosition={robotPosition}
          selectedWaypoint={selectedWaypoint}
          onSelectWaypoint={setSelectedWaypoint}
        />
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
```

### Usage

```bash
cd svc-app
npm install
npm run dev    # Vite dev server on http://localhost:5173
```

The app connects to `ws://localhost:8765` (the gateway).

### Implementation steps

1. Convert the map PNG to WebP and place in `svc-app/public/kachaka_map.webp`.
2. Copy `map-pin.svg` and `arrow-right-circle.svg` to `svc-app/public/icons/`
   (or inline the paths directly in `MapView.tsx`).
3. Get the image dimensions and update `config.ts`.
4. Create `config.ts` with hardcoded constants.
5. Create the five hooks in `src/hooks/`.
6. Create the three components in `src/components/` (inline the SVG icon paths
   in `MapView.tsx` as `WaypointPin` and `RobotMarker` components).
7. Rewrite `App.tsx` to wire everything together.
8. Add styles to `App.css` for the map layout, side panel, and connection indicator.
9. Build the SDK: `cd openrois/sdk/typescript && npm run build`.
10. Start the gateway: `python gateway/main.py --mock --port 8765`.
11. Start the svc-app: `cd svc-app && npm run dev`.
12. Open `http://localhost:5173`, verify the map renders, waypoint pins appear,
    robot marker is visible with heading direction, clicking a pin selects it,
    clicking Navigate sends the command, and the arrival event clears the selection.

---

## 7. Schedule

### Day 1

| Who | Task |
|-----|------|
| Intern | Scaffold `bus-adapter/`. Implement `discover()`, `query()`, `invoke()`. Verify against real Kachaka in REPL. |
| Intern | Implement `poller.py`, `subscribe()`, `unsubscribe()`. Verify event fires on arrival. Run `mypy --strict`, `ruff check`. |
| Lead engineer | Gateway is already implemented. Test with `--mock` if not already done. |

### Day 2

| Who | Task |
|-----|------|
| Intern | Integrate adapter with gateway. Run end-to-end test against real Kachaka. Write README. Final presentation. |
| Lead engineer | Coordinate integration. Debug real robot issues. |
| Lead engineer | Convert map to WebP. Build `svc-app` hooks and `MapView` component. Run full end-to-end test from browser. |

---

## 8. End-to-End Test

### Gateway + adapter (Python side)

1. Start the gateway:
   ```bash
   python gateway/main.py --target <kachaka-ip>:26400 --port 8765
   ```

2. Connect a WebSocket client and send these JSON-RPC requests:
   - `rois.system.connect` -> `OK`
   - `rois.command.search` -> `["kachaka/Navigation", "kachaka/SystemInformation"]`
   - `rois.command.bind` with `component_ref: "kachaka/Navigation"` -> `OK`
   - `rois.query.query` with `query_type: "robot_position"` -> x, y, theta
   - `rois.query.query` with `query_type: "waypoints"` -> list of locations
   - `rois.event.subscribe` with `event_type: "reached_target"` -> `subscribe_id`
   - `rois.command.execute` with `target_positions: ["Kitchen"]` -> `command_id`
   - Receive `rois.event.notify` notification when robot arrives
   - Receive `rois.command.completed` notification
   - `rois.event.unsubscribe` -> `OK`
   - `rois.system.disconnect` -> `OK`

### svc-app (browser side)

1. Start the gateway (above).
2. Start the svc-app: `npm run dev`.
3. Open `http://localhost:5173`.
4. The app connects to the gateway, fetches waypoints, starts polling position.
5. The map renders with waypoint icons and a robot position marker.
6. Click a waypoint. The robot navigates to it.
7. The robot marker moves across the map as position updates arrive.
8. On arrival, the `reached_target` event fires and the selected waypoint clears.

---

## 9. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport to robot | gRPC (not ROS 2) | No ROS 2 infrastructure needed. `pip install kachaka-api`. |
| Kachaka client | Async (`kachaka_api.aio`) | `BusAdapter` protocol is async. Sync would block the event loop. |
| WebSocket library | `websockets` | Pure asyncio, clean API, no framework weight. |
| Config source | CLI args (argparse) | Simplest for a throwaway. No env vars, no config file. |
| Map delivery | Static WebP in `public/` | Not symbolic data. Not sent over RoIS WebSocket. |
| Event delivery | Both `rois.event.notify` and `rois.command.completed` | Most complete, matches RoIS spec. |
| Multi-connection | Multiple, shared adapter | No bind/release enforcement. Matches demo-roadmap.md weeks 1-2. |
| Error format | RoIS return codes in response payload | JSON-RPC errors only for malformed requests. Clean separation. |
| Gateway packaging | Scripts only, no `pyproject.toml` | Disposable placeholder. |
| Mock adapter | Separate file (`mock_adapter.py`) | Test gateway on day 1 without intern's adapter or robot. |
| `openrois-interfaces` | Vendored wheel in `vendor/` | One `pip install` command. No monorepo clone needed. |
| Navigation scope | Named locations only | No `move_to_pose`. Unknown names return `BAD_PARAMETER`. |
| Battery level | Not implemented | Out of scope. |
| Tests | None | Fast-iteration prototype. Verified by running against real robot. |
| Internship duration | 2 days | Pre-work reading before day 1. |
| Intern scope | `bus-adapter/` only | Gateway is lead engineer's work. Adapter is injected via constructor. |