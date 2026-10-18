# Changelog

## 0.3.4 (2026-10-18)

- **A SITL already running elsewhere no longer blocks the start**: when the ports of instance 0
  are taken and the scenario does not set `first_instance`, Hangar uses the first free instance
  and says so (preflight warning). The scenario file is not changed; a `first_instance` you
  write is kept as it is.

## 0.3.3 (2026-10-18)

- **`first_instance`** in a scenario: start the SITLs at another instance number (every port
  moves by 10 per instance), to run next to a SITL that is already running. The busy-port error
  suggests it.
- **`goto LAT LON [ALT]`** command: fly there in GUIDED (MAV_CMD_DO_REPOSITION), at the current
  altitude unless one is given.
- **Parameters**: `param fetch` reads the whole list (asking again for any value that got lost),
  `param get NAME`; every value seen is kept, also ones set by a ground station. Setting or
  reading one parameter asks again once when a slow simulation answers late.
- The firmware version is read from the autopilot (AUTOPILOT_VERSION).

## 0.3.2 (2026-10-18)

- **Vehicles without a pose are placed automatically**, 15 m apart and clear of placed ones,
  instead of all starting at the origin (where they fell over each other and never became
  ready). Leave the pose field empty in the vehicle editor for this.
- **Sim speed indicator**: the status bar, the vehicle panel and `hangar up` show how fast the
  simulation runs compared to real time, measured from each autopilot's clock. Below ×0.5 for
  15 s, a warning names the likely causes.
- **Performance warnings before start**: integrated-GPU-only machines (suggests `headless: true`),
  heavy cameras (resolution × fps), many cameras together, and worlds with thousands of
  colliding buildings.
- Worlds from real places use hangar-earth 0.3.0: `hangar world create --no-building-collision`
  and a "buildings are solid" switch in the dialog make buildings visual only.
- `hangar doctor` shows the GPU and warns when only an integrated one is found.

## 0.3.1 (2026-10-18)

- Worlds from real places use hangar-earth 0.2.0: **OpenStreetMap buildings** (one colliding mesh)
  and **your own data** for sharper worlds. `hangar world create` passes `--no-buildings`,
  `--dem`, `--imagery`, `--imagery-url`, `--imagery-zoom`, `--imagery-credit`, `--texture` (up to
  8192) and `--resolution` (up to 4097); the Scenario-tab dialog has a buildings switch and
  fields for your own DEM and orthophoto.
- An older installed hangar-earth is noticed and an update is offered.

## 0.3.0 (2026-10-18)

### Worlds from real places
- `hangar world create --lat --lon --size --name`, or **Create a world from a real place** in
  the Scenario tab, builds a Gazebo world for any point on Earth with real terrain (Copernicus
  DEM, 30 m) and satellite imagery (Sentinel-2, 10 m) through
  [hangar-earth](https://github.com/Enginar140/hangar-earth), installed on first use (about
  200 MB, no sudo). Worlds go to `~/.local/share/hangar/worlds` and appear everywhere.
- Vehicles in such a world start on the ground: a pose's `z` is the height above the terrain.

### Ready-made worlds
- `hangar sources fetch hangar_worlds` installs [hangar-worlds](https://github.com/Enginar140/hangar-worlds):
  an airfield with a 400 m runway, a 1 km mission arena with targets and air-defence zones, and
  a small town. The installer offers it together with Gazebo.

### Location
- Vehicles start where the Gazebo world is: a world's `<spherical_coordinates>` becomes the SITL
  home unless the scenario sets `home`, and a world without one gets the scenario's, so
  QGroundControl and the MAVProxy map show the vehicles in the right place. `hangar worlds` lists
  each world's location.

### Under the hood
- Relative files in a world (heightmaps, textures, meshes) keep working after Hangar copies the
  world into the run directory.
- New `archive` source kind: release tarballs, unpacked safely without git.

## 0.2.0 (2026-10-18)

### Shared map (MAVProxy)
- The **Map** button (`m`) opens MAVProxy's map and console with every vehicle on one map,
  on a temporary port Hangar adds and removes by itself; vehicle restarts keep it.
- `!command` in the command line goes to MAVProxy for the selected vehicle, e.g. `!wp list`;
  MAVProxy's answers appear in the Logs tab. Headless: `hangar up --map`.
- MAVProxy installs on demand into Hangar's own folder: the Map button offers it, or
  `hangar sources fetch mavproxy`, or `install.sh --mavproxy`. Uses the official wxPython
  wheel for Ubuntu 22.04/24.04 and skips VTK (about 900 MB instead of 1.4 GB); no sudo.
  `hangar doctor` reports it.

### Cameras to other devices
- `camera.outputs` sends a camera to other computers (host:port, `{i}` templates, multicast).
- `camera.stream: relay` (default): Hangar copies the RTP stream untouched to this computer
  and every output; outputs can be added and removed while running in the Ports tab
  (**Camera outputs**). `camera.stream: direct`: the camera plugin sends straight to its one
  address and this computer gets nothing.
- In relay mode the camera plugin now sends to 5700 + index; readers keep using 5600 + index.
- `examples/camera_check.py` receives a camera on any computer, Windows included: it checks
  that packets arrive (and says why when they don't), then shows the picture with pip's
  OpenCV through an SDP file. Verified on Windows.

### Project
- README: supported systems, how multi-vehicle simulation and cameras work (with diagrams).
- Releases are automated: pushing a `vX.Y.Z` tag checks the version and changelog, tests,
  builds and publishes the GitHub release. The version lives only in `src/hangar/__init__.py`.

## 0.1.0 (2026-10-18)

First release.

### Simulation
- Any number of ArduPilot vehicles (copter, plane, VTOL, rover, boat) in Gazebo Harmonic or
  with ArduPilot's built-in physics, described in one YAML scenario file.
- Per-vehicle Gazebo model copies with their own ports; the original models are never modified.
- Models with undeclared XML prefixes (e.g. SITL_Models' catamaran) load.
- Everything runs as background processes in their own process groups; stopping always
  cleans up, no SITL or Gazebo keeps running.

### Terminal UI
- Fleet table with live telemetry; Vehicle, Logs, Ports and Scenario tabs; English and Turkish.
- Arm, disarm, takeoff, RTL, land, mode and a command line per vehicle.
- Never blocks while Gazebo and the SITLs start.

### Ports
- One port plan for every vehicle, checked for conflicts before anything starts.
- Built-in MAVLink router instead of one MAVProxy per vehicle; outputs (udp, udpin, tcpin) can
  be added and removed in the Ports tab while running, with per-vehicle templates like `{14560+i}`.
- Per-vehicle script ports (tcp 5762 + 10*i) so scripts never mix vehicles up.

### Cameras
- `camera: true` adds a camera to any Gazebo vehicle; resolution, FPS, field of view and
  direction are configurable; a model's own camera can be tuned the same way.
- Camera window with ready-to-paste Python/OpenCV, GStreamer, v4l2 and gst-launch code;
  `examples/camera_opencv.py`, optional v4l2loopback bridge.

### Logs
- Every run writes `logs/hangar.log` with all events and MAVLink messages; Save/Copy/Open in
  the Logs tab; `hangar logs` with follow and filters.

### Install
- `curl | bash` installer: uv + Hangar, ArduPilot's official prebuilt SITL 4.7.2 (seconds),
  clipboard tool, optional Gazebo Harmonic + ArduPilot Gazebo plugin + SITL_Models.
- Third-party projects are downloaded from upstream only after their licenses are shown.
- `hangar doctor` checks the setup and says how to fix what is missing.

### Known limitations
- Tested on Ubuntu 24.04 with Gazebo Harmonic 8.15. Ubuntu 22.04 is supported by the
  installer but not tested yet.
- Building ArduPilot from source (`--build-ardupilot`) is implemented but not yet tested
  end to end; the prebuilt SITL path is the tested default.
- Camera bitrate is fixed at 800 kbit/s by ArduPilot's GstCameraPlugin.
- Vehicles cannot be added to a running Gazebo world; stop, edit, start.
