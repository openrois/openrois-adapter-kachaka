# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a1] - 2026-11-11

The first release. The components pass their tests against the fake robot that comes
with the adapter and have not been verified on a robot yet.

### Added

- The `openrois-adapter-kachaka` command, which serves one Preferred Robotics Kachaka to
  an OpenRoIS gateway under its own engine id, on Python 3.12. It talks to the robot over
  the Kachaka gRPC API, which the `robot` extra installs. Two adapters put two robots
  behind one gateway. Its settings come from `adapter.yaml`, named by `--config` or
  `OPENROIS_ADAPTER_CONFIG`, with one section for the connection and one per component.
  The gateway URL, the engine id, the robot's address and the log level can also come
  from `OPENROIS_ADAPTER_*` variables and flags. The README gives `adapter.yaml` for a
  Kachaka on the network and `adapter.fake.yaml` for the fake robot, which the repository
  and the source distribution also ship. A setting the adapter does not know, or a value
  out of range, stops it with a message that names the setting.
- `KachakaConnection`, one connection to the robot that its components share. While the
  robot does not answer for `connection.lost_after` seconds, every component reports
  ERROR, and they report READY again once it is back. The robot runs one command at a
  time, and the connection records which component started it: a component cancels only
  its own command, and a command that another one replaced ends with ERROR.
- `KachakaSystemInformation`, which answers `robot_position` with the robot's pose
  `x,y,theta` on its map, in meters and radians, and `engine_status` with the status of
  the engine.
- `KachakaNavigation`, which drives the robot to each of `target_positions` in turn, a
  location of the robot by its name or id or a pose `x,y,theta`, and sends
  `reached_target` at each one. `time_limit` ends a start that takes longer with TIMEOUT.
  `suspend` cancels the drive and `resume` drives to the same target again.
  `navigation.auto_homing` sets whether the robot returns to its charger on its own.
- `KachakaMove`, which turns the robot in place and then drives it in a straight line, as
  `line` says: the distance in millimeters and the orientation in degrees. `time`, in
  seconds, sets the speed of the line, between 0.05 and 0.3 m/s. `suspend` cancels the
  motion and `resume` drives what remains. It drives no curves: a `curve` that is not
  empty is refused.
- `KachakaPersonDetection`, which sends `person_detected` with the number of persons the
  robot's own detector sees, on each start and each time that number changes and holds
  for `person_detection.hold` seconds. A person counts from the score
  `person_detection.min_score`. While the robot sends no detection for
  `person_detection.max_age` seconds, the component reports WARNING and no number.
- `python -m openrois_adapter_kachaka.fake_robot`, a stand-in that serves the Kachaka
  gRPC API on this machine, for the tests and for a try of the adapter without a robot.
