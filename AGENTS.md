# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this repo is

OpenRoIS adapters for the Kachaka robot (Preferred Robotics), with two integration paths that reach the same robot, and the Kachaka components that both paths host:

- **API path** (`api/`): a flat Python script that talks to the Kachaka gRPC API (port `26400`) through the `kachaka-api` library.
  No ROS 2 required.
- **ROS 2 path** (`ros2/`): an `ament_python` package that talks to the topics and the action of `kachaka_grpc_ros2_bridge`, built from the `kachaka-api` submodule.
- **Components** (`components/`): the package `openrois-components-kachaka`, imported as `openrois.components.kachaka`, with one class per component type and backend.

Each adapter is a thin process.
It reads its settings from `adapter.yaml`, hosts three components in an OpenRoIS `Engine`, and dials an OpenRoIS gateway on `/adapter` with `WsClient`.
The three components are `Navigation` (`GrpcNavigation` or `Ros2Navigation`), `SystemInformation` (`GrpcSystemInformation` or `Ros2SystemInformation`), and `NavigationInformation` (`GrpcNavigationInformation` or `Ros2NavigationInformation`).
`Navigation` and `SystemInformation` are basic RoIS component types.
`NavigationInformation` is a component type Coarobo publishes, with the profile `NAVIGATION_INFORMATION_PROFILE` (authority `Coarobo`) in `profiles.py`.
Both backends of a type declare the same profile constant, so a client cannot tell which one runs.
The OpenRoIS packages the code builds on (`openrois-interfaces`, `openrois-engine`, and `openrois-components-core`) are not on PyPI yet and install from a clone of https://github.com/openrois/openrois.
The project is alpha and not production-ready.

The ROS 2 path is a **ROS 2 Jazzy workspace**: packages build with `colcon`, and `rclpy` and `kachaka_interfaces` only import after sourcing ROS 2, the bridge overlay, and the workspace overlay.

## Repository layout

```
api/                                  # API path (gRPC through kachaka-api)
  kachaka_api_adapter.py              # entry point: KachakaConfig, build_engine, main
  adapter.yaml.example                # tracked settings template (adapter.yaml is gitignored)
  pyproject.toml                      # pytest settings only, the adapter is a script
  requirements.txt                    # kachaka-api
  tests/                              # adapter tests
components/                           # openrois-components-kachaka, with its own LICENSE and NOTICE
  src/openrois/components/kachaka/
    navigation_grpc.py                # GrpcNavigation
    navigation_ros2.py                # Ros2Navigation
    system_information_grpc.py        # GrpcSystemInformation
    system_information_ros2.py        # Ros2SystemInformation
    navigation_information_grpc.py    # GrpcNavigationInformation
    navigation_information_ros2.py    # Ros2NavigationInformation
    settings.py                       # one settings model per section of adapter.yaml
    profiles.py                       # NAVIGATION_INFORMATION_PROFILE
  tests/                              # component tests against the stand-ins of _fakes.py
  CHANGELOG.md
ros2/                                 # ROS 2 path (through kachaka_grpc_ros2_bridge)
  adapter.yaml.example                # tracked settings template
  ros2_ws/src/
    openrois_kachaka_ros2/            # ament_python adapter package
      openrois_kachaka_ros2/
        kachaka_ros2_adapter.py       # entry point, console script kachaka_adapter
      test/                           # adapter tests
    kachaka_description               # symlink to ../../../kachaka-api/ros2/kachaka_description
    kachaka_interfaces                # symlink to ../../../kachaka-api/ros2/kachaka_interfaces
    kachaka_speak                     # symlink to ../../../kachaka-api/ros2/demos/kachaka_speak
kachaka-api/                          # git submodule pf-robotics/kachaka-api (protos, Python library, ROS 2 bridge)
utils/camera_feeder.py                # standalone: Kachaka front camera to a v4l2loopback device
```

## Environment setup

Python 3.12+ (the root `.python-version` pins `3.12`, and `components/pyproject.toml` requires `>=3.12`).
`ros2/.python-version` selects `system`, since the ROS 2 path runs on the Python that runs ROS 2.
`ros2/requirements.txt` lists no packages, only the notes that `rclpy` and `kachaka_interfaces` do not come from pip.
Run commands from the repository root unless a block starts with `cd`.

```bash
git submodule update --init
OPENROIS=/path/to/openrois    # a clone of https://github.com/openrois/openrois
pip install -e $OPENROIS/interfaces/python -e $OPENROIS/engine -e $OPENROIS/components/core
pip install -e "./components[grpc]"    # API path: the components with kachaka-api
pip install -e ./components            # ROS 2 path: the components without kachaka-api
pip install -e "./components[dev]"     # tests, mypy, and ruff
```

For the ROS 2 path, install the OpenRoIS packages and the components into the Python that runs ROS 2.
The `grpc` extra and `api/requirements.txt` take `kachaka-api` from PyPI.
To use the copy in the submodule instead, install `kachaka-api/` itself with `pip install -e kachaka-api` (see Gotchas).

The ROS 2 path needs ROS 2 Jazzy at `/opt/ros/jazzy` plus the bridge build dependencies:

```bash
sudo apt install -y libprotobuf-dev libgrpc++-dev nlohmann-json3-dev protobuf-compiler-grpc ros-jazzy-xacro
```

The ROS 2 components also need `rclpy`, `nav_msgs`, and `kachaka_interfaces` from a sourced ROS 2 installation.
If no ROS installation is available in your sandbox, you can still edit code, install the API path, and run every test suite.
You cannot build or run the ROS 2 adapter.

## Build and run

API path (needs a robot reachable at `robot_address`):

```bash
cd api
cp -n adapter.yaml.example adapter.yaml    # set robot_address and gateway_url
python kachaka_api_adapter.py --config adapter.yaml
```

ROS 2 path: build the bridge inside the submodule, then the adapter.

```bash
cd kachaka-api
mkdir -p ros2/kachaka_grpc_ros2_bridge/gen-src
protoc -I protos --grpc_out=ros2/kachaka_grpc_ros2_bridge/gen-src --plugin=protoc-gen-grpc=$(which grpc_cpp_plugin) --cpp_out=ros2/kachaka_grpc_ros2_bridge/gen-src protos/*.proto
cd ros2
source /opt/ros/jazzy/setup.bash
colcon build --packages-select kachaka_interfaces kachaka_description kachaka_grpc_ros2_bridge
source install/setup.bash
cd ../../ros2/ros2_ws
colcon build --packages-select openrois_kachaka_ros2
```

Run the bridge and the adapter (each terminal sources `/opt/ros/jazzy/setup.bash` and `kachaka-api/ros2/install/setup.bash` first):

```bash
ros2 launch kachaka_grpc_ros2_bridge grpc_ros2_bridge.launch.xml server_uri:=<robot IP>:26400    # terminal 1
source ros2/ros2_ws/install/setup.bash                                                            # terminal 2
cp -n ros2/adapter.yaml.example ros2/adapter.yaml                                                 # set gateway_url
ros2 run openrois_kachaka_ros2 kachaka_adapter --config ros2/adapter.yaml
```

Each setting comes from the defaults of `KachakaConfig`, then the file named by `--config` or `OPENROIS_ADAPTER_CONFIG`, then the `OPENROIS_ADAPTER_*` environment variables, then the flags.
The environment and the flags set only the four common settings (`gateway_url`, `engine_id`, `robot_address`, and `log_level`).
The component sections (`navigation`, `system_information`, and `navigation_information`) come only from the file.
The ROS 2 adapter leaves the arguments after `--ros-args` to ROS 2.
Both adapters run until Ctrl+C and reconnect when the gateway goes away.
Clients see the components as `<engine_id>/Navigation`, `<engine_id>/SystemInformation`, and `<engine_id>/NavigationInformation`, where `engine_id` defaults to `kachaka_01` on the API path and `kachaka` on the ROS 2 path.

## Tests and checks

There is no CI configuration.
Three pytest suites run without a robot, without ROS 2, and without `kachaka-api`, once the OpenRoIS packages and `./components[dev]` are installed.
Run each one from its own directory:

```bash
(cd components && pytest && mypy && ruff check src/ tests/)
(cd api && pytest)
(cd ros2/ros2_ws/src/openrois_kachaka_ros2 && python3 -m pytest)
```

The component tests host each component in an engine against stand-ins from `components/tests/_fakes.py`: `FakeKachaka` for the gRPC API, and `FakeRos2` for rclpy and the Kachaka interfaces.
The adapter tests load the `adapter.yaml.example` files and check the engine each adapter builds, without connecting the components.
A Kachaka is the only full check.
`mypy` (strict) and `ruff` are configured in `components/pyproject.toml` only, and cover `components/src` and `components/tests`.
`package.xml` declares `ament_copyright`, `ament_flake8`, `ament_pep257`, and `python3-pytest` as test dependencies, but no test of the package runs the ament linters.
Where the OpenRoIS packages cannot be installed, at least syntax-check the Python sources before every commit:

```bash
python3 -m compileall -q api utils components/src ros2/ros2_ws/src/openrois_kachaka_ros2
```

## Conventions

- Python 3.12, with a module docstring and `from __future__ import annotations` in adapter and component modules.
- One module per component type and backend in `components/src/openrois/components/kachaka/`, named `<type>_grpc.py` or `<type>_ros2.py`.
  Declare each class with the `openrois.components.core` decorators (`@component`, `@query`, `@invoke`, `@subscribe`) and export it from `__init__.py` and its `__all__`.
- The gRPC class and the ROS 2 class of a type declare the same profile constant and serve the same messages.
  Keep the two in step.
- Each component takes its section of `adapter.yaml` as one settings model from `settings.py`, and the models set `extra="forbid"` and `frozen=True`.
  Keep `settings.py`, the `KachakaConfig` of each adapter, the `adapter.yaml.example` files, and the settings blocks of the READMEs in step.
- The gRPC components take the adapter's `robot_address` as their first argument.
  The ROS 2 components take the adapter's one `RclpyExecutor` and record it with `self.uses(ros)`.
- Each component owns its backend connection: it creates its gRPC client or rclpy node in `connect()` and drops it in `disconnect()`.
  A ROS 2 component hands its node to the executor with `add_node`, and takes it back with `remove_node` before `destroy_node`.
- Import `kachaka_api`, `rclpy`, `kachaka_interfaces`, and `nav_msgs` inside `connect()` or the handler that needs them, not at module level, so the package and the tests import without them.
- Never call `rclpy.init` in a component.
  The `RclpyExecutor` initializes rclpy and spins every node in a background thread.
- In the ROS 2 components, rclpy callbacks run on the executor's thread and handlers run on the asyncio event loop.
  Guard shared state with `threading.Lock`, and hand results to the loop with `loop.call_soon_threadsafe`.
- A failing command raises `CommandFailed(CompletedStatus.ERROR, ...)`.
- The engine answers `component_status`, plus `suspend` and `resume` on `Navigation`, and assigns the command ids, so components do not implement them.
- The adapters register the components as `Navigation`, `SystemInformation`, and `NavigationInformation`, the names clients know, and the adapter tests assert these refs.
- `components/` follows strict `mypy` and `ruff` (`line-length = 100`, rules `E`, `F`, `I`, `N`, `W`, and `UP`).
- Record notable changes to `components/` in `components/CHANGELOG.md` under `[Unreleased]`, in the Keep a Changelog format.
- Settings files (`adapter.yaml`, and the old `openrois-profile.yaml` and `openrois-profile-*.yaml`) are **gitignored**: commit changes only to the `.example` files.
- Never modify `kachaka-api/` (upstream submodule).
- Conventional Commits (`<type>(<scope>): <description>`, with scopes such as `ros2` and `readme`, and `!` plus a `BREAKING CHANGE:` footer for a breaking change), and branches named by type (`docs/sync-local-docs-and-symlinks`).
  `dev` holds the current code, and `main` lags behind it.

## Gotchas

- `kachaka-api/` is empty until `git submodule update --init`.
  The three symlinks in `ros2/ros2_ws/src/` point into it by relative paths, so they dangle until then.
- The bridge and the adapter must use the same ROS 2 distribution: a Humble bridge with a Jazzy adapter causes CDR serialization errors.
- `ros2/README.md` names "Jazzy or Humble", but the components require Python 3.12 or later, so the Python that runs ROS 2 must be 3.12 or later.
  Use Jazzy, as the root `README.md` does.
- `ros2/README.md` builds the bridge without the `protoc` step and without `kachaka_description`.
  Follow the sequence above, since the bridge's `CMakeLists.txt` compiles the generated sources in `gen-src/` and its launch file includes `kachaka_description`.
- The READMEs mix working directories in the ROS 2 run steps.
  From the repository root, source `kachaka-api/ros2/install/setup.bash` and pass `--config ros2/adapter.yaml`, as above.
- Topic and action names carry the bridge namespace (`/kachaka/` by default).
  A bridge started with another namespace needs the topics and the action of all three sections of `ros2/adapter.yaml` changed.
- The bridge publishes odometry with `SensorDataQoS` (best effort), and the location list and the map with depth 1 and `TRANSIENT_LOCAL`.
  The components subscribe with matching QoS, and a subscription with mismatched QoS receives nothing.
- The ROS 2 components learn from topics.
  `start` fails with "The Kachaka knows no location" until the latched location list arrives, and `robot_position` answers ERROR until the first odometry message.
- No settings file is read by default.
  Without `--config` or `OPENROIS_ADAPTER_CONFIG`, an adapter runs on its defaults, and the API adapter then exits with status `2` because `robot_address` is unset.
- `openrois-profile.yaml` is no longer read, and a setting the components do not have, such as the old `poll_interval`, is refused.
  `api/README.md` and `ros2/README.md` map the old keys to the new ones.
- `test_the_example_file_loads` in both adapter suites asserts values of the `adapter.yaml.example` files, so a change to an example file needs the matching change to its test.
- `api/tests/` and `components/tests/` are both packages named `tests`, so one pytest run over both fails at collection.
  Run each suite from its own directory.
- In `ros2/ros2_ws/src/openrois_kachaka_ros2/`, plain `pytest` (as `ros2/README.md` shows) cannot import `openrois_kachaka_ros2` unless the package is already on the Python path.
  `python3 -m pytest` from that directory puts it there.
- `api/README.md` installs `kachaka-api` from `../kachaka-api/python`, which holds no `pyproject.toml` or `setup.py`.
  The packaging is `kachaka-api/pyproject.toml`, which maps the package to `python/`.
- The submodule ignores neither the `build/`, `install/`, and `log/` that the bridge build leaves in `kachaka-api/ros2/`, nor the `python/kachaka_api.egg-info/` that an editable install leaves.
  `git status` then reports `kachaka-api` as changed, and that is no reason to commit the submodule.
- The two `NavigationInformation` backends fill `origin_theta` of `map_info` differently.
  The gRPC one passes the `theta` of the map origin, and the ROS 2 one passes the `z` component of the origin's quaternion.
- The root `README.md` and `LICENSE` say "All rights reserved. No License." for Coarobo GK.
  `components/` carries its own Apache-2.0 `LICENSE` and `NOTICE` and declares `license = "Apache-2.0"`, and `package.xml` and `setup.py` of `openrois_kachaka_ros2` declare `Apache-2.0`.
  Do not change license text or metadata without asking.
