# Hangar

**Multi-vehicle ArduPilot SITL + Gazebo from one terminal.**

Hangar starts any number of ArduPilot vehicles (planes, copters, VTOLs, rovers,
boats) in Gazebo or with ArduPilot's built-in physics. It routes their MAVLink
to your ground station and shows the whole fleet in one terminal UI. A single
port plan covers every vehicle, and Hangar never opens a pile of terminal
windows.

```
 ● RUNNING   World: skywalker_x8_runway   3 vehicles   Uptime 00:04:12
 #  Name     Model            Status   Mode     Armed  Ready  Alt      Speed     Battery  GPS
 0  alpha    ✈ skywalker_x8   online   AUTO     ARMED  ✓      84.2 m   22.1 m/s  91%      3D (10)
 1  bravo    ✈ firtinam       online   FBWA     ARMED  ✓      61.0 m   19.4 m/s  88%      3D (10)
 2  scout    ✣ sitl:copter    online   GUIDED   no     ✓      0.0 m    0.0 m/s   100%     3D (10)
 ┌ Vehicle ─ Logs ─ Ports ─ Scenario ──────────────────────────────────────────────────┐
 │ telemetry · arm/disarm · takeoff · mode · RTL · land · camera · command line · logs │
```

[Türkçe README](README.tr.md)

## Why

The usual multi-vehicle setup runs `sim_vehicle.py` once per vehicle, adds a
MAVProxy per vehicle, and opens one more window for the map. That means hard-coded
paths, `sleep`s, and ports scattered across scripts. Hangar replaces all of it with:

- **One scenario file**: world, vehicles, poses, parameters and outputs in YAML.
- **One port plan**: every port (SITL, Gazebo FDM, cameras, GCS outputs) is
  computed in one place and checked for conflicts before anything starts.
  `hangar ports` shows the plan.
- **One process**: SITLs run in the background with their logs in files. A
  built-in MAVLink router replaces the per-vehicle MAVProxies. Stopping always
  cleans up, so no SITL keeps running after you quit.
- **One terminal UI** that stays responsive while everything starts.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Enginar140/hangar/main/install.sh | bash
```

The installer asks before each step:

1. installs [uv](https://docs.astral.sh/uv/) and Hangar for your user (no sudo, and the system Python is left alone),
2. downloads ArduPilot's **official prebuilt SITL** (arducopter, arduplane, ardurover 4.7.2, about 21 MB,
   seconds, no compiler); `--build-ardupilot` builds from source instead,
3. optionally installs Gazebo Harmonic, the ArduPilot Gazebo plugin and example models,
4. runs `hangar doctor`.

Already have ArduPilot? Point Hangar at it instead:

```bash
uv tool install git+https://github.com/Enginar140/hangar
hangar config set ardupilot.dir ~/ardupilot
hangar config add catalog.model_paths ~/SITL_Models/Gazebo/models
hangar config add catalog.world_paths ~/SITL_Models/Gazebo/worlds
hangar config add gazebo.plugin_paths ~/ardupilot_gazebo/build
hangar doctor
```

### Licenses of what gets downloaded

Hangar is MIT-licensed and contains only its own code. ArduPilot (GPL-3.0),
ardupilot_gazebo (LGPL-3.0) and SITL_Models (no license stated) are **not**
bundled. `hangar sources fetch` shows each project's license and asks before
downloading it at a pinned version from its own upstream server
(firmware.ardupilot.org for the prebuilt SITL, GitHub for the rest), so you
get it directly from upstream. `hangar sources` lists what is installed, and the exact commits
are recorded in `~/.local/share/hangar/sources/lock.json`.

## Use

```bash
hangar                         # terminal UI (restores your last scenario)
hangar init                    # write quickstart.yaml (two vehicles, no Gazebo needed)
hangar quickstart.yaml         # open it in the UI
hangar up quickstart.yaml      # run without the UI, Ctrl+C stops
hangar ports quickstart.yaml   # port plan + preflight checks
hangar models / hangar worlds  # what can be launched
hangar doctor                  # what is installed, what is missing, how to fix it
```

### Terminal UI keys

| Key | Action | Key | Action |
|---|---|---|---|
| `s` | start | `x` | stop |
| `a` | add vehicle | `e` / `d` | edit / remove vehicle |
| `1`–`4` | Vehicle / Logs / Ports / Scenario tab | `:` | command line for the selected vehicle |
| `ctrl+s` / `ctrl+o` | save / open scenario | `F2` | doctor |
| `ctrl+p` | command palette (themes too) | `q` | quit (stops the simulation) |

Vehicle commands (buttons or command line): `arm [force]`, `disarm`, `mode GUIDED`
(or just `GUIDED`), `takeoff 30`, `rtl`, `land`, `param set NAME VALUE`.

The UI is available in English and Turkish and follows `$LANG`. You can also
pick a language with `hangar config set language tr`.

## Scenario files

```yaml
name: two-planes
world: skywalker_x8_runway        # omit for built-in physics only
speedup: 1
outputs:                          # every vehicle gets these
  - udp:127.0.0.1:14550           # QGroundControl / Mission Planner
  - udp:127.0.0.1:{15000+i}       # one port per vehicle; {i} = index, {sysid} = MAVLink id
vehicles:
  - model: skywalker_x8
    pose: [0, 0, 0.3, 0, 0, 90]   # x y z roll pitch yaw (m, deg); [x, y, z, yaw] also works
    camera:
      v4l2_device: /dev/video2    # nose camera -> cv2.VideoCapture('/dev/video2')
  - model: sitl:copter            # built-in physics, no Gazebo
    name: scout
    pose: [40, 0, 0]
    params: {SIM_WIND_SPD: 3}
    outputs: ["tcpin:0.0.0.0:{6000+i}"]
```

Output kinds: `udp:host:port` sends to a listener (the GCS case),
`udpin:host:port` listens and replies to whoever talks to it, and
`tcpin:host:port` accepts TCP clients.

### Port layout

ArduPilot shifts every port by `10 × instance`, and Hangar follows that:

| | vehicle 0 | vehicle 1 | vehicle *i* |
|---|---|---|---|
| MAVLink (SITL serial0, Hangar connects) | tcp 5760 | tcp 5770 | 5760 + 10*i* |
| Gazebo ↔ SITL (JSON) | udp 9002 | udp 9012 | 9002 + 10*i* |
| Camera (RTP H.264) | udp 5600 | udp 5601 | 5600 + *i* |
| MAVLink system id | 1 | 2 | *i* + 1 |

## Adding your own vehicle

Any Gazebo model whose `model.sdf` contains an `ArduPilotPlugin` shows up in
`hangar models`. To tell Hangar what it is, put a `hangar.yaml` next to the
model:

```yaml
type: plane                 # copter | plane | rover | boat
defaults: [plane.parm]      # optional, from ArduPilot's default_params
params: [my_plane.param]    # optional, found next to the model or in ../config
```

Then add the folder: `hangar config add catalog.model_paths /path/to/models`.

## How it works

```
             ┌──────────── hangar (one process, asyncio) ────────────┐
 scenario ─▶ │ preflight ─▶ world builder ─▶ supervisor              │
             │                  │              │  ├─ gz sim           │
             │                  │              │  ├─ arduplane -I0 ◀─┼─ tcp 5760
             │                  ▼              │  └─ arducopter -I1 ◀┼─ tcp 5770
             │        per-instance models      ▼                      │
             │        (fdm/camera ports)   MAVLink router + telemetry ┼─▶ udp 14550 (GCS)
             │                                  │                     ┼─▶ udp 15000+i …
             │                                  ▼                     │
             │                           terminal UI / `hangar up`    │
             └───────────────────────────────────────────────────────┘
```

- `scenario.py`: the scenario schema (pydantic). Unknown keys are errors, not silently ignored.
- `ports.py`: the port plan, output templates and conflict checks.
- `world.py`: copies each Gazebo model into the run directory with its own ports.
- `process.py`: child processes in their own process group, with logs and a clean stop.
- `mavlink.py`: per-vehicle router, telemetry and commands.
- `supervisor.py`: puts everything together. `tui/` and `cli.py` are thin front-ends.

Every run gets its own directory under `~/.local/state/hangar/runs/` with the
generated world, each vehicle's eeprom and logs, and a copy of the scenario.

## Development

```bash
git clone https://github.com/Enginar140/hangar && cd hangar
uv sync
uv run pytest
uv run hangar
```

## License

MIT. See [LICENSE](LICENSE).
