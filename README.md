# OpenRoIS Adapter for Kachaka

The [OpenRoIS](https://openrois.org) adapter for the Preferred Robotics
[Kachaka](https://kachaka.life). It serves canonical RoIS components for a Kachaka, so
that a service application drives the robot through an OpenRoIS gateway with the
standard RoIS interfaces. The adapter talks to the robot over the
[Kachaka gRPC API](https://github.com/pf-robotics/kachaka-api).

## RoIS in Two Minutes

RoIS, the OMG Robotic Interaction Service framework, is a standard way for an
application to use a robot's human-robot interaction functions without knowing which
robot it talks to. The robot offers **components** of standard types, such as Navigation
or PersonDetection. An application works with them in a fixed order:

1. **connect** to the gateway, and read its **profile** to see the robots and components.
2. **search** for a component of a type, and **bind** it, which reserves it for the
   application. Only the application that holds the bind of the Navigation drives the
   robot with it.
3. **set_parameter** to configure a component, for example the targets to drive to, and
   **execute** commands such as `start` and `stop`. Each command completes with a status:
   OK, ERROR, ABORT when it was stopped, or TIMEOUT.
4. **subscribe** to events, such as the number of persons in view, and **query** values,
   such as the robot's position.
5. **release** and **disconnect** when done.

When something goes wrong, for example the robot drops off the network, the application
gets a **notify_error** and asks **get_error_detail** what happened. The gateway speaks
JSON-RPC over WebSocket, and the openrois repository has client SDKs for JavaScript,
Python and Unity.

This adapter is the robot's side: one process per robot that connects to a gateway and
serves the robot's four components.

## Status

Version 0.1.0a1, alpha, pre-1.0, unstable API. The four components and the adapter command
run on the released `openrois-engine`, `openrois-components-core` and
`openrois-interfaces`, and pass their tests against the simulated Kachaka that comes with
the adapter. They were also run on a Kachaka: [On a Robot](#on-a-robot) says what that run
showed and what is still open. The settings and the behaviour may still change before
1.0.

## Components

Each adapter serves one robot under its engine id, for example `kachaka/navigation`. All
four components are canonical RoIS types.

| Component | Ref | On the Kachaka |
|-----------|-----|----------------|
| SystemInformation | `<engine id>/system_information` | `robot_position`, the robot's pose `x,y,theta` on its map in meters and radians, and `engine_status` |
| Navigation | `<engine id>/navigation` | `start` drives to each of `target_positions` in turn, and `reached_target` is sent at each one. A target is a location of the robot, by its name or its id, or a pose `x,y,theta` on the map |
| Move | `<engine id>/move` | `start` turns the robot in place and then drives it in a straight line, as `line` says |
| PersonDetection | `<engine id>/person_detection` | From `start` to `stop`, `person_detected` with the number of persons the robot's own detector sees, on each start and each time that number changes. `suspend` holds the events back and `resume` sends them again |

Navigation and Move are actuation components: one application binds each at a time, and
a second one gets OUT_OF_RESOURCES. PersonDetection and SystemInformation are shared, so
several applications bind and use them together. A command a component does not offer is
answered UNSUPPORTED.

The four components share one connection to the robot. While the robot does not answer,
every component reports ERROR in `component_status`, the applications that bound or
subscribed to them get `notify_error`, and their commands and queries are refused. Once
the robot is back, the components report READY again. The robot counts as lost once it
has not answered for `connection.lost_after` seconds.

### One Motion at a Time

The Kachaka runs one command at a time, for every client and for the Kachaka app.
Navigation and Move share that one place:

- `stop` on a component cancels only that component's own motion. A drive of the other
  component, of another client or of the Kachaka app keeps running.
- A `start` of Move while Navigation drives replaces the drive, and the other way round.
  The start that was replaced ends with ERROR at once and says that another command
  replaced it.
- A command from the Kachaka app or another client ends a running `start` with ERROR in
  the same way. The robot does not announce it, so the adapter notices it at its next
  check of the command, which can take up to about ten seconds.
- A `start` that ends with ERROR because the robot refused a call cancels its own
  command, so the robot does not drive on after the application was told of the error.

The Kachaka has no pause. `suspend` cancels the motion on the robot and holds the
`start`, and `resume` continues it: Navigation drives to the same target again, and Move
turns and drives what remains. A `stop` while suspended ends the `start` with ABORT.

### Navigation

- `target_positions`: the targets, in order. Names of the robot's locations are matched
  first, then their ids, as the Kachaka app shows them. The charger is the location
  `home`. Anything else must be a pose `x,y,theta`.
- `time_limit`: seconds, 0 or more. A `start` that has not reached its last target in
  time ends with TIMEOUT and cancels the drive. 0, the default, sets no limit. The time
  of a suspension does not count.
- `routing_policy`: `time` or `distance`. The Kachaka plans its own routes, so it has no
  effect.

`set_parameter` answers BAD_PARAMETER for a target the robot does not know, a negative
`time_limit` and another `routing_policy`.

### Move

- `line`: two integers, the distance in millimeters and the orientation in degrees.
  `start` first turns the robot in place by the orientation, then drives straight by the
  distance. A positive orientation turns counter-clockwise, the sense in which `theta`
  of `robot_position` grows. A negative distance drives backwards. A part that is 0 is
  skipped: `[0, 90]` only turns and `[500, 0]` only drives.
- `time`: seconds, 0 or more. 0, the default, drives at the robot's own speed. A
  positive time sets the speed of the straight line to the distance divided by the time,
  kept between 0.05 and 0.3 m/s. The turn has no speed setting and the robot is slow to
  start a line, so the time is best effort, and a short line takes longer.
- `curve`: this version drives no curves, and a `curve` that is not empty is refused.

A turn of more than 90 degrees is sent to the robot as equal turns of at most 90 degrees,
one after the other. `set_parameter` answers BAD_PARAMETER for a `line` that is not
exactly two integers, a distance beyond 10 m, an orientation beyond 3600 degrees and a
negative `time`. A `start` without a `line`, or with `[0, 0]`, ends with ERROR.

### PersonDetection

A person is an object that the robot's detector labels as a person with a score of
`person_detection.min_score` or more. A new number is reported once it has held for
`person_detection.hold` seconds, so a number that flickers is not reported. The
`timestamp` of the event is the time the adapter received the detection.

The component only reads, so it counts while the robot drives. No detection is not a
count of 0: while the robot has sent no detection for `person_detection.max_age`
seconds, the component reports WARNING and no number, and it reports the number afresh
once detections arrive again.

### On a Robot

The components were run on one Kachaka with software 3.17.10 that carried a shelf. That
run showed:

- The detector sends detections about 2.5 times a second, also while the robot sits on
  its charger and while nothing is in view. The robot reports only persons with a score
  of 0.5 or more, so a `person_detection.min_score` up to 0.5 lets every person through.
- A cancelled motion leaves the robot at rest within a second, about 2 cm further on. A
  line of 1 m that was suspended and resumed came out at 1.003 m.
- The robot is slow to start a line: at 0.1 m/s it had covered 10 cm after 3 seconds,
  and then drove 0.76 m in 9 seconds.
- A line of 300 mm at the robot's own speed took 6.5 seconds and came out at 238 mm by
  the robot's own position, and the same line backwards at 293 mm.
- A turn of 90 degrees came out between 88 and 95 degrees. With the shelf on, a turn in
  place moved the position the robot reports by 7 to 25 cm.

Still open:

- Which speeds the robot accepts for a straight line. A line at 0.1 m/s was run. At
  0.05 m/s the robot had not moved after 1.8 seconds, when another command replaced the
  line, and 0.3 m/s was not run.
- A robot without a shelf, `time_limit`, a pose as a target, two applications at once
  and the loss of the robot during a motion were not part of the run. The tests cover
  them against the simulated Kachaka only.
- That a `start` which another component replaced ends at once was added after the run.
  On the robot it ended 9 seconds later.

## Install

The adapter needs Python 3.12: the Kachaka API package pins a gRPC version that has no
wheel for a later Python. Install the adapter with the `robot` extra, which brings the
Kachaka API package, and the gateway:

```bash
uv venv --python 3.12 ~/venvs/openrois-kachaka
uv pip install --python ~/venvs/openrois-kachaka "openrois-adapter-kachaka[robot]" \
  openrois-gateway
```

The robot's API must be reachable from this machine on port 26400. The Kachaka app
shows the robot's IP address.

## Run

Start an [OpenRoIS gateway](https://pypi.org/project/openrois-gateway/) first,
`openrois-gateway`, on `ws://127.0.0.1:8765` by default. Then start one adapter per
robot, with the settings files of the [next section](#the-settings-files). Each command
runs until Ctrl+C, so give each its own terminal, where the environment is active:

```bash
source ~/venvs/openrois-kachaka/bin/activate

# The gateway.
openrois-gateway

# The Kachaka on the network, at its IP address.
openrois-adapter-kachaka --config adapter.yaml --robot-address 192.168.1.100

# The simulated Kachaka on this machine, for a try without a robot.
python -m openrois_adapter_kachaka.fake_robot
openrois-adapter-kachaka --config adapter.fake.yaml
```

The adapter checks the robot every `connection.retry` seconds and connects again once a
robot that was switched off or dropped off the network is back. The adapter and the
robot start in any order. Two adapters with different engine ids put two robots behind
one gateway.

### The Settings Files

The repository and the source distribution ship both files. `adapter.yaml`, for a
Kachaka on the network:

```yaml
# Settings of the Kachaka adapter for a Kachaka on the network.
# OPENROIS_ADAPTER_* environment variables and flags override the four common settings at
# the top. The sections below them belong to the connection and the components. See
# README.md for every setting.

gateway_url: ws://127.0.0.1:8765
engine_id: kachaka
# The robot's IP address or host name, with :port when the port is not 26400.
robot_address: 192.168.1.100
log_level: info

connection:
  timeout: 5.0
  retry: 3.0
  lost_after: 6.0
  poll: 10.0

navigation:
  # true or false sets whether the robot returns to its charger on its own; null leaves
  # the robot's setting as it is.
  auto_homing: null

person_detection:
  # Seconds a new number of persons has to hold before it is reported.
  hold: 2.0
  # The score, from 0 to 1, from which an object the robot's detector labels as a person
  # counts.
  min_score: 0.5
  # Seconds the robot may send no detection before the component reports WARNING and no
  # number of persons.
  max_age: 5.0
```

`adapter.fake.yaml`, for the simulated Kachaka on this machine:

```yaml
# Settings of the Kachaka adapter for the simulated Kachaka on this machine, started with
# python -m openrois_adapter_kachaka.fake_robot. Select it with --config adapter.fake.yaml.

gateway_url: ws://127.0.0.1:8765
engine_id: kachaka_fake
robot_address: 127.0.0.1:26400
log_level: info

connection:
  retry: 1.0
  lost_after: 2.0

person_detection:
  # Seconds a new number of persons has to hold before it is reported.
  hold: 2.0
  # The score, from 0 to 1, from which an object the robot's detector labels as a person
  # counts.
  min_score: 0.5
  # Seconds the robot may send no detection before the component reports WARNING and no
  # number of persons.
  max_age: 5.0
```

### Settings

The settings come from `adapter.yaml`, named by `--config` or `OPENROIS_ADAPTER_CONFIG`.
The four settings at the top can also come from the environment or a flag, which win
over the file:

| Setting | Flag | Environment | Default | What it is |
|---------|------|-------------|---------|------------|
| `gateway_url` | `--gateway-url` | `OPENROIS_ADAPTER_GATEWAY_URL` | `ws://127.0.0.1:8765` | The gateway to connect to |
| `engine_id` | `--engine-id` | `OPENROIS_ADAPTER_ENGINE_ID` | `kachaka` | The engine id, the first part of every ref |
| `robot_address` | `--robot-address` | `OPENROIS_ADAPTER_ROBOT_ADDRESS` | none | The robot's IP address or host name, with `:port` when the port is not 26400 |
| `log_level` | `--log-level` | `OPENROIS_ADAPTER_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error` |

The others are only in the file:

| Setting | Default | What it is |
|---------|---------|------------|
| `connection.timeout` | `5.0` | Seconds to wait for an answer of the robot |
| `connection.retry` | `3.0` | Seconds between two checks of the robot |
| `connection.lost_after` | `6.0` | Seconds a robot that answered before may stop answering before it counts as lost |
| `connection.poll` | `10.0` | Seconds one wait for the result of a command lasts before the adapter checks that the command still runs. The robot ends such a wait itself after about 9 seconds |
| `navigation.auto_homing` | none | `true` or `false` sets whether the robot returns to its charger on its own after a while without commands, each time the robot comes up. Without it the robot's setting stays as it is |
| `person_detection.hold` | `2.0` | Seconds a new number of persons holds before it is reported |
| `person_detection.min_score` | `0.5` | The score from which a person counts, from 0 to 1 |
| `person_detection.max_age` | `5.0` | Seconds the robot may send no detection before the component reports WARNING and no number |

A setting the adapter does not know, or a value out of range, stops it with a message
that names the setting.

The adapter reconnects to the gateway whenever the connection drops. Ctrl+C stops it.

## Layout

- `src/openrois/components/kachaka/`: the components and their shared connection,
  imported as `openrois.components.kachaka`.
- `src/openrois_adapter_kachaka/`: the adapter command, one process per robot, and
  `fake_robot`, a simulated Kachaka that serves the Kachaka gRPC API on this machine.

## Development

The tests run without a robot: the simulated Kachaka plays it. From a clone of the repository:

```bash
python -m pip install -e ".[dev]"   # The OpenRoIS packages and the gateway, for the tests
ruff check .
ruff format --check .
mypy
pytest
```

A tag such as `v0.1.0-alpha.1` releases the version of `pyproject.toml`, 0.1.0a1, to
PyPI, and then creates the GitHub release with its section of the changelog.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
