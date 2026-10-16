# Changelog

All notable changes to openrois-components-kachaka are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** the package moved from the openrois/openrois monorepo to openrois-adapter-kachaka, beside the two adapters that host it. Its name and its import path, `openrois.components.kachaka`, are unchanged.
- **Breaking:** the ROS 2 components take the adapter's `RclpyExecutor` from openrois-components-core as their first argument and no longer call `rclpy.init`; the executor initializes rclpy and spins their nodes.
- **Breaking:** each component takes its settings as one model, its section of the adapter's `adapter.yaml`: `GrpcNavigationSettings`, `Ros2NavigationSettings`, `Ros2SystemInformationSettings`, `NavigationInformationSettings` and `Ros2NavigationInformationSettings`, in place of keyword arguments.

### Added

- NavigationInformation over the gRPC API and over ROS 2 (`GrpcNavigationInformation`, `Ros2NavigationInformation`), with `destinations`, `destination_positions` and `map_info`, under the profile `NAVIGATION_INFORMATION_PROFILE`, authority `Coarobo`.

## [0.1.0a2]

Navigation and SystemInformation over the Kachaka gRPC API and over ROS 2, as released from the openrois/openrois monorepo.
