# Changelog

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
