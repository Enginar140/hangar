"""Runs a scenario: preflight, Gazebo, one SITL + link per vehicle, cameras.

Everything is asyncio, so the TUI stays responsive while the simulation
starts, and stopping (even half-way through a start) always tears down every
process that was launched.
"""

from __future__ import annotations

import asyncio
import atexit
import enum
import os
import re
import shutil
import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import camera, paths, perf
from .catalog import Catalog, VehicleModel
from .mavlink import VehicleLink
from .ports import Endpoint, PortPlan, VehiclePorts, port_in_use
from .process import ManagedProcess, ProcState
from .scenario import CameraSpec, Home, Scenario, VehicleSpec
from .settings import Settings
from .world import WorldVehicle, build_world

GAZEBO_READY_TIMEOUT = 120.0
LINK_TIMEOUT = 60.0
LOG_LINES = 3000


class SimState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class SupervisorError(Exception):
    pass


@dataclass
class LogLine:
    time: float
    source: str
    level: str  # info | warning | error | event
    text: str


@dataclass
class Preflight:
    plan: PortPlan | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class VehicleRuntime:
    spec: VehicleSpec
    model: VehicleModel
    ports: VehiclePorts
    run_dir: Path
    process: ManagedProcess | None = None
    link: VehicleLink | None = None
    pose: list[float] = field(default_factory=list)  # where it starts, see Scenario.placed_poses
    camera_relay: camera.VideoRelay | None = None
    camera_bridge: ManagedProcess | None = None
    camera_viewer: ManagedProcess | None = None
    camera_topic: str | None = None

    @property
    def name(self) -> str:
        return self.ports.name

    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        if self.process.state in (ProcState.EXITED, ProcState.FAILED):
            return "crashed"
        if self.process.state in (ProcState.STOPPING, ProcState.STOPPED):
            return "stopped"
        if self.link and self.link.telemetry.alive:
            return "online"
        if self.link and self.link.telemetry.connected:
            return "silent"
        return "booting"


class Supervisor:
    def __init__(
        self,
        scenario: Scenario,
        settings: Settings,
        catalog: Catalog,
        on_log: Callable[[LogLine], None] | None = None,
        on_state: Callable[[SimState], None] | None = None,
    ):
        self.scenario = scenario
        self.settings = settings
        self.catalog = catalog
        self.on_log = on_log
        self.on_state = on_state
        self.state = SimState.IDLE
        self.vehicles: list[VehicleRuntime] = []
        self.gazebo: ManagedProcess | None = None
        self.run_dir: Path | None = None
        self.logs: deque[LogLine] = deque(maxlen=LOG_LINES * 4)
        self.started_at: float | None = None
        self._env: dict[str, str] = dict(os.environ)
        self._start_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self._logfile = None
        self._attempt_started = 0.0
        self.mavproxy: ManagedProcess | None = None
        self._map_output: Endpoint | None = None
        atexit.register(self._kill_all)

    # --- logging / state -------------------------------------------------

    def log(self, source: str, text: str, level: str = "info") -> None:
        line = LogLine(time.time(), source, level, text)
        self.logs.append(line)
        if self._logfile:
            self._logfile.write(format_plain(line) + "\n")
        if self.on_log:
            self.on_log(line)

    def _open_logfile(self) -> None:
        """Everything shown in the UI also goes to <run>/logs/hangar.log, from the
        preflight messages of this start on."""
        assert self.run_dir
        path = self.run_dir / "logs" / "hangar.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._logfile = path.open("a", encoding="utf-8", buffering=1)
        for line in self.logs:
            if line.time >= self._attempt_started:
                self._logfile.write(format_plain(line) + "\n")
        latest = paths.runs_dir() / "latest"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(self.run_dir.name)
        except OSError:
            pass

    def _close_logfile(self) -> None:
        if self._logfile:
            self._logfile.close()
            self._logfile = None

    @property
    def log_file(self) -> Path | None:
        return self.run_dir / "logs" / "hangar.log" if self.run_dir else None

    def _line(self, source: str, text: str) -> None:
        level = "event" if text.startswith("[hangar]") else "info"
        self.log(source, text.removeprefix("[hangar] "), level)

    def _set_state(self, state: SimState) -> None:
        self.state = state
        if self.on_state:
            self.on_state(state)

    @property
    def uses_gazebo(self) -> bool:
        return any(m.needs_gazebo for m in self._models().values())

    def _models(self) -> dict[str, VehicleModel]:
        out = {}
        for v in self.scenario.vehicles:
            m = self.catalog.get(v.model)
            if m:
                out[v.name or v.model] = m
        return out

    # --- preflight -------------------------------------------------------

    def preflight(self, check_ports: bool = True) -> Preflight:
        errors: list[str] = []
        warnings: list[str] = []
        sc = self.scenario
        if not sc.vehicles:
            errors.append("the scenario has no vehicles")

        models = {}
        for v in sc.vehicles:
            m = self.catalog.get(v.model)
            if m is None:
                errors.append(f"{v.name}: unknown model '{v.model}' (see `hangar models`)")
                continue
            models[v.name] = m
            exe = self.catalog.binary_path(m)
            if exe is None or not exe.is_file():
                errors.append(f"{v.name}: {m.binary} not found; run `hangar sources fetch ardupilot_sitl` "
                              "or `hangar config set ardupilot.dir PATH`")
            elif not os.access(exe, os.X_OK):
                errors.append(f"{v.name}: {exe} is not executable (chmod +x {exe})")
            res = self.catalog.resolve_params(m)
            if res.missing:
                warnings.append(f"{v.name}: parameter files not found: {', '.join(res.missing)}")
            if v.camera and v.camera.enabled and not m.needs_gazebo:
                warnings.append(f"{v.name}: cameras need a Gazebo model; {m.name} uses built-in physics")
            if v.camera and v.camera.stream == "direct" and v.camera.v4l2_device:
                warnings.append(f"{v.name}: v4l2_device needs camera stream 'relay'; "
                                "with 'direct' this computer gets no frames")

        if any(m.needs_gazebo for m in models.values()):
            if not sc.world:
                errors.append("Gazebo vehicles need a world: set `world:` in the scenario")
            elif sc.world not in self.catalog.worlds:
                errors.append(f"unknown world '{sc.world}' (see `hangar worlds`)")
            if not shutil.which("gz"):
                errors.append("Gazebo (`gz`) is not installed or not on PATH")
            if not any((p / "libArduPilotPlugin.so").exists() for p in self.settings.resolved_plugin_paths()):
                warnings.append("libArduPilotPlugin.so not found in gazebo.plugin_paths")

        if any(m.needs_gazebo for m in models.values()):
            warnings += self._performance_warnings(models)

        plan = None
        try:
            plan = PortPlan.build(
                sc,
                gazebo={n: m.needs_gazebo for n, m in models.items()},
                cameras={v.name: _wants_camera(models[v.name], v) for v in sc.vehicles if v.name in models},
            )
            errors += [f"port conflict: {c}" for c in plan.conflicts(check_system=check_ports)]
        except ValueError as e:
            errors.append(str(e))
        return Preflight(plan, errors, warnings)

    def _performance_warnings(self, models: dict[str, VehicleModel]) -> list[str]:
        cams = []
        for v in self.scenario.vehicles:
            m = models.get(v.name or "")
            if m is None or not _wants_camera(m, v):
                continue
            c = v.camera
            own = m.camera_size or (1280, 720)
            explicit = c.model_fields_set if c else set()
            w = c.width if c and (not m.has_camera or "width" in explicit) else own[0]
            h = c.height if c and (not m.has_camera or "height" in explicit) else own[1]
            cams.append(perf.Camera(v.name or v.model, w, h, c.fps if c else 30))
        world = self.catalog.worlds.get(self.scenario.world or "")
        return perf.warnings(cams, world.path if world else None, self.scenario.headless)

    def sim_speed(self) -> float | None:
        """Simulated seconds per real second, the slowest vehicle's (1.0 = real time)."""
        speeds = [v.link.telemetry.sim_speed for v in self.vehicles
                  if v.link and v.link.telemetry.sim_speed is not None]
        return min(speeds) if speeds else None

    async def _watch_speed(self) -> None:
        """Say once, with the likely causes, when the simulation crawls."""
        slow = 0
        while self.state == SimState.RUNNING:
            await asyncio.sleep(5)
            speed = self.sim_speed()
            slow = slow + 1 if speed is not None and speed < 0.5 else 0
            if slow == 3:
                tips = self._performance_warnings({v.name: v.model for v in self.vehicles}) or [
                    "the computer is busy with other work"]
                self.log("hangar", f"the simulation runs at {speed * 100:.0f}% of real time, so ArduPilot "
                         "takes much longer to become ready. Likely causes: " + "; ".join(tips), "warning")
                return

    # --- start / stop ----------------------------------------------------

    async def start(self) -> None:
        if self.state in (SimState.STARTING, SimState.RUNNING, SimState.STOPPING):
            raise SupervisorError(f"simulation is {self.state.value}")
        self._start_task = asyncio.current_task()
        self._attempt_started = time.time()
        self._set_state(SimState.STARTING)
        try:
            await self._start()
        except asyncio.CancelledError:
            self.log("hangar", "start cancelled", "warning")
            raise
        except Exception as e:
            self.log("hangar", f"start failed: {e}", "error")
            await self._teardown()
            self._set_state(SimState.ERROR)
            self._close_logfile()
            raise SupervisorError(str(e)) from e
        finally:
            self._start_task = None

    async def _start(self) -> None:
        pf = await asyncio.to_thread(self.preflight)
        for w in pf.warnings:
            self.log("hangar", w, "warning")
        if not pf.ok:
            for e in pf.errors:
                self.log("hangar", e, "error")
            raise SupervisorError(f"{len(pf.errors)} preflight error(s)")
        assert pf.plan

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = paths.runs_dir() / f"{stamp}-{self.scenario.name}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "scenario.yaml").write_text(self.scenario.dump(), encoding="utf-8")
        self._open_logfile()
        self.log("hangar", f"run directory: {self.run_dir}", "event")

        poses = self.scenario.placed_poses()
        self.vehicles = [
            VehicleRuntime(spec, self.catalog.get(spec.model), ports, self.run_dir / ports.name,  # type: ignore[arg-type]
                           pose=pose)
            for spec, ports, pose in zip(self.scenario.vehicles, pf.plan.vehicles, poses)
        ]
        for v in self.vehicles:
            if not v.spec.placed and len(self.vehicles) > 1:
                self.log(v.name, f"no pose given: placed at x={v.pose[0]:g}, y={v.pose[1]:g}", "event")

        if self.uses_gazebo:
            await self._start_gazebo()

        await asyncio.gather(*(self._start_vehicle(v) for v in self.vehicles))
        self.started_at = time.time()
        self._set_state(SimState.RUNNING)
        self.log("hangar", f"{len(self.vehicles)} vehicle(s) running", "event")
        watcher = asyncio.create_task(self._watch_speed())
        self._background.add(watcher)
        watcher.add_done_callback(self._background.discard)

        if self.uses_gazebo:  # cameras only need Gazebo, not the autopilots
            for v in self.vehicles:
                if v.ports.has_camera:
                    task = asyncio.create_task(self._start_camera(v))
                    self._background.add(task)
                    task.add_done_callback(self._background.discard)

        results = await asyncio.gather(*(v.link.wait_connected(LINK_TIMEOUT) for v in self.vehicles if v.link))
        if not all(results):
            self.log("hangar", "some vehicles did not send a heartbeat yet", "warning")

    def world_dir(self) -> Path:
        """Where the generated world and its per-vehicle models (``models/``) go."""
        assert self.run_dir
        return self.run_dir / "world"

    def gazebo_env(self) -> dict[str, str]:
        env = dict(os.environ)
        res = [self.world_dir() / "models"] if self.run_dir else []
        res += self.settings.resolved_model_paths() + self.settings.resolved_world_paths()
        res += self.settings.resolved_resource_paths()
        env["GZ_SIM_RESOURCE_PATH"] = _join_paths(res, env.get("GZ_SIM_RESOURCE_PATH"))
        env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = _join_paths(
            self.settings.resolved_plugin_paths(), env.get("GZ_SIM_SYSTEM_PLUGIN_PATH")
        )
        return env

    async def _start_gazebo(self) -> None:
        assert self.run_dir and self.scenario.world
        world = self.catalog.worlds[self.scenario.world]
        gz_vehicles = [
            WorldVehicle(v.model, v.ports, " ".join(f"{x:g}" for x in v.pose), v.spec.camera)
            for v in self.vehicles if v.model.needs_gazebo
        ]
        ardupilot_models = {n for n, m in self.catalog.vehicles.items() if m.needs_gazebo}
        world_file = await asyncio.to_thread(
            build_world, world.path, gz_vehicles, self.world_dir(), ardupilot_models,
            self.settings.resolved_model_paths(), self.home(),
        )
        h = self.home()
        source = "scenario" if "home" in self.scenario.model_fields_set else \
            "world" if world.location else "default"
        self.log("hangar", f"home {h.lat:.6f}, {h.lon:.6f}, {h.alt:g} m ({source})", "event")
        for v in gz_vehicles:
            if v.adds_camera:
                self.log(v.instance_name, "camera added to the model", "event")
        self.log("hangar", f"world generated: {world_file}", "event")

        self._env = self.gazebo_env()
        argv = ["gz", "sim", "-v", "3", "-r", "--render-engine", self.settings.render_engine]
        if self.scenario.headless:
            argv += ["-s", "--headless-rendering"]
        argv.append(str(world_file))
        self.gazebo = ManagedProcess(
            "gazebo", argv, cwd=self.run_dir, env=self._env,
            log_path=self.run_dir / "logs" / "gazebo.log", on_line=self._line,
            on_exit=self._gazebo_exited,
        )
        await self.gazebo.start()

        pending = {v.ports.fdm for v in gz_vehicles}
        deadline = time.monotonic() + GAZEBO_READY_TIMEOUT
        self.log("hangar", "waiting for Gazebo to load the vehicles", "event")
        while pending:
            if not self.gazebo.alive:
                raise SupervisorError("Gazebo exited during start-up (see the gazebo log)")
            if time.monotonic() > deadline:
                raise SupervisorError(f"Gazebo did not open FDM ports {sorted(pending)} in time")
            pending = {p for p in pending if p and not port_in_use("udp", p)}
            await asyncio.sleep(0.5)
        self.log("hangar", "Gazebo ready", "event")

    def _gazebo_exited(self, p: ManagedProcess) -> None:
        if p.state == ProcState.EXITED and self.state == SimState.RUNNING:
            self.log("gazebo", "Gazebo closed: Gazebo vehicles have no physics now; "
                     "stop and start the simulation again", "error")

    def home(self) -> Home:
        """Where the vehicles start: the scenario's home if it sets one, else the
        Gazebo world's own location, so maps show the vehicles where the world is."""
        sc = self.scenario
        if "home" not in sc.model_fields_set and self.uses_gazebo and sc.world in self.catalog.worlds:
            loc = self.catalog.worlds[sc.world].location
            if loc:
                return sc.home.model_copy(update={"lat": loc[0], "lon": loc[1], "alt": loc[2]})
        return sc.home

    def _sitl_argv(self, v: VehicleRuntime) -> list[str]:
        m = v.model
        exe = self.catalog.binary_path(m)
        assert exe
        params = [str(p) for p in self.catalog.resolve_params(m).files]
        overrides = v.run_dir / "hangar.parm"
        overrides.write_text(
            "".join(f"{k.upper()} {val:g}\n" for k, val in v.spec.params.items()), encoding="utf-8"
        )
        params.append(str(overrides))

        home = self.home()
        if not m.needs_gazebo:  # built-in physics: spread vehicles out by their pose
            x, y, yaw = v.pose[0], v.pose[1], v.pose[5]
            home = home.offset(north_m=y, east_m=x)
            if yaw:
                home = home.model_copy(update={"heading": (90.0 - yaw) % 360})

        argv = [
            str(exe),
            "-I", str(v.ports.instance),
            "--model", m.sitl_model,
            "--speedup", f"{self.scenario.speedup:g}",
            "--sysid", str(v.ports.sysid),
            "--home", home.arg(),
            "--defaults", ",".join(params),
        ]
        if m.needs_gazebo:
            argv += ["--sim-address", "127.0.0.1"]
        return argv

    async def _start_vehicle(self, v: VehicleRuntime) -> None:
        v.run_dir.mkdir(parents=True, exist_ok=True)
        v.process = ManagedProcess(
            f"{v.name}/sitl", self._sitl_argv(v), cwd=v.run_dir,
            log_path=v.run_dir / "sitl.log", on_line=self._line,
            on_exit=lambda p, v=v: self._vehicle_exited(v, p),
        )
        await v.process.start()
        v.link = VehicleLink(
            v.name, v.ports.sysid, v.ports.serial0, self._link_outputs(v.ports.outputs),
            on_text=lambda name, level, text: self.log(name, text, level),
        )
        await v.link.start()

    def _vehicle_exited(self, v: VehicleRuntime, p: ManagedProcess) -> None:
        if p.state == ProcState.EXITED:
            self.log(v.name, f"SITL exited unexpectedly (code {p.returncode}), see {p.log_path}", "error")

    def _camera_destinations(self, v: VehicleRuntime) -> list[tuple[str, int]]:
        assert v.ports.camera
        return [("127.0.0.1", v.ports.camera)] + [(t.host, t.port) for t in v.ports.camera_targets]

    async def _start_camera(self, v: VehicleRuntime) -> None:
        if v.ports.camera_mode == "relay":
            v.camera_relay = camera.VideoRelay(
                v.ports.camera_send.port, self._camera_destinations(v),
                on_error=lambda text, name=v.name: self.log(name, text, "warning"),
            )
            await v.camera_relay.start()
        self.log(v.name, "camera: enabling stream", "event")
        topic = await camera.enable_streaming(v.name, self._env)
        if not topic:
            self.log(v.name, "camera: enable topic not found", "warning")
            return
        v.camera_topic = topic
        if v.ports.camera_mode == "direct":
            t = v.ports.camera_send
            self.log(v.name, f"camera streaming directly to {t.host}:{t.port} (not to this computer)", "event")
            return
        sent = ", ".join(f"{h}:{p}" for h, p in self._camera_destinations(v))
        self.log(v.name, f"camera streaming to {sent}", "event")
        device = v.spec.camera.v4l2_device if v.spec.camera else None
        if device:
            if not Path(device).exists():
                self.log(v.name, f"camera: {device} missing, load v4l2loopback (see `hangar doctor`)", "warning")
                return
            v.camera_bridge = ManagedProcess(
                f"{v.name}/camera", camera.v4l2_bridge_argv(v.ports.camera, device),
                log_path=v.run_dir / "camera.log", on_line=self._line,
            )
            await v.camera_bridge.start()
            self.log(v.name, f"camera -> {device} (OpenCV: cv2.VideoCapture('{device}'))", "event")

    def camera_info(self, name: str) -> dict:
        """Port, size and ready-to-paste usage for a vehicle's camera stream."""
        v = self.vehicle(name)
        if not v.ports.has_camera:
            raise SupervisorError(f"{name} has no camera")
        cam = v.spec.camera
        own = v.model.camera_size or (1280, 720)
        explicit = cam.model_fields_set if cam else set()
        width = cam.width if cam and (not v.model.has_camera or "width" in explicit) else own[0]
        height = cam.height if cam and (not v.model.has_camera or "height" in explicit) else own[1]
        fps = cam.fps if cam else 30
        device = cam.v4l2_device if cam else None
        local = v.ports.camera
        targets = [f"{t.host}:{t.port}" for t in v.ports.camera_targets]
        # the code is for whoever receives: this computer in relay mode, the target otherwise
        port = local if local else v.ports.camera_send.port
        return {
            "mode": v.ports.camera_mode, "port": local, "targets": targets,
            "width": width, "height": height, "fps": fps, "device": device,
            "streaming": bool(v.camera_topic),
            "viewer": bool(v.camera_viewer and v.camera_viewer.alive),
            "packets": v.camera_relay.packets if v.camera_relay else None,
            "usage": camera.usage(port, width, height, device),
        }

    def check_camera_output(self, name: str, target: str) -> list[str]:
        """Problems adding ``target`` (host:port template) to a camera would cause."""
        trial = self.scenario.model_copy(deep=True)
        spec = next(v for v in trial.vehicles if v.name == name)
        if not spec.camera or not spec.camera.enabled:
            return [f"{name} has no camera"]
        if spec.camera.stream == "direct":
            return [f"{name}'s camera is 'direct' (one address); edit the vehicle to change it"]
        try:
            spec.camera = CameraSpec.model_validate(
                {**spec.camera.model_dump(), "outputs": [*spec.camera.outputs, target]})
            before = set(self._plan_for(self.scenario).conflicts(check_system=False))
            plan = self._plan_for(trial)
        except ValueError as e:
            return [str(e).splitlines()[0]]
        return [c for c in plan.conflicts(check_system=False) if c not in before]

    async def set_camera_outputs(self, name: str, outputs: list[str]) -> bool:
        """Change where a camera streams. Returns True when it took effect now; a
        'direct' camera is set in Gazebo's model, so it changes on the next start."""
        spec = next(v for v in self.scenario.vehicles if v.name == name)
        cam = spec.camera or CameraSpec()
        spec.camera = CameraSpec.model_validate({**cam.model_dump(), "outputs": outputs})
        if self.state != SimState.RUNNING:
            return False
        v = self.vehicle(name)
        new_ports = self._plan_for(self.scenario).vehicles[v.ports.index]
        if v.ports.camera_mode != "relay" or new_ports.camera_mode != "relay":
            return False
        v.ports.camera_targets = new_ports.camera_targets
        if v.camera_relay:
            v.camera_relay.set_destinations(self._camera_destinations(v))
        return True

    async def toggle_camera_view(self, name: str) -> bool:
        """Open or close a viewer window. Returns True when it is now open."""
        v = self.vehicle(name)
        if not v.ports.has_camera:
            raise SupervisorError(f"{name} has no camera")
        if not v.ports.camera:
            raise SupervisorError(f"{name}'s camera streams directly to another address; "
                                  "switch it to 'relay' to watch it here")
        if v.camera_viewer and v.camera_viewer.alive:
            await v.camera_viewer.stop()
            v.camera_viewer = None
            return False
        bridge = v.camera_bridge and v.camera_bridge.alive
        device = v.spec.camera.v4l2_device if (bridge and v.spec.camera) else None
        v.camera_viewer = ManagedProcess(
            f"{v.name}/viewer", camera.viewer_argv(v.ports.camera, device),
            log_path=v.run_dir / "viewer.log", on_line=self._line,
        )
        await v.camera_viewer.start()
        return True

    # --- outputs (Ports tab) ------------------------------------------------

    def _plan_for(self, scenario: Scenario) -> PortPlan:
        models = {v.name: self.catalog.get(v.model) for v in scenario.vehicles}
        return PortPlan.build(
            scenario,
            gazebo={n: bool(m and m.needs_gazebo) for n, m in models.items()},
            cameras={v.name: bool(models[v.name]) and _wants_camera(models[v.name], v) for v in scenario.vehicles},
        )

    def check_output(self, url: str, scope: str) -> list[str]:
        """Problems adding ``url`` for ``scope`` ("*" or a vehicle) would cause."""
        trial = self.scenario.model_copy(deep=True)
        if scope == "*":
            trial.outputs.append(url)
        else:
            next(v for v in trial.vehicles if v.name == scope).outputs.append(url)
        try:
            before = set(self._plan_for(self.scenario).conflicts(check_system=False))
            plan = self._plan_for(trial)
        except ValueError as e:
            return [str(e)]
        problems = [c for c in plan.conflicts(check_system=False) if c not in before]
        old = {(b.proto, b.port) for b in self._plan_for(self.scenario).bindings()}
        for b in plan.bindings():
            if (b.proto, b.port) not in old and port_in_use(b.proto, b.port):
                problems.append(f"{b.proto}/{b.port} is already in use by another program")
        return problems

    async def add_output(self, url: str, scope: str) -> None:
        problems = self.check_output(url, scope)
        if problems:
            raise SupervisorError("; ".join(problems))
        if scope == "*":
            self.scenario.outputs.append(url)
        else:
            next(v for v in self.scenario.vehicles if v.name == scope).outputs.append(url)
        await self._apply_outputs()

    async def remove_output(self, template: str, scope: str) -> None:
        holder = self.scenario.outputs if scope == "*" else \
            next(v for v in self.scenario.vehicles if v.name == scope).outputs
        if template in holder:
            holder.remove(template)
        await self._apply_outputs()

    async def _apply_outputs(self) -> None:
        """Running links pick up the new outputs at once; no restart needed."""
        if self.state != SimState.RUNNING:
            return  # used on the next start
        plan = self._plan_for(self.scenario)
        for v, ports in zip(self.vehicles, plan.vehicles):
            v.ports.outputs = ports.outputs
            if v.link:
                await v.link.set_outputs(self._link_outputs(ports.outputs))

    def _link_outputs(self, outputs: list[Endpoint]) -> list[Endpoint]:
        """The planned outputs plus the map's, which only lives while the map is open."""
        return outputs + ([self._map_output] if self._map_output else [])

    # --- shared map (MAVProxy) ---------------------------------------------

    @property
    def map_open(self) -> bool:
        return bool(self.mavproxy and self.mavproxy.alive)

    async def toggle_map(self) -> bool:
        """Open or close the shared MAVProxy map + console. Returns True when open."""
        if self.map_open:
            await self._close_map()
            return False
        if self.state != SimState.RUNNING:
            raise SupervisorError("start the simulation first")
        exe = self.settings.mavproxy()
        if exe is None:
            raise SupervisorError("MAVProxy is not installed: hangar sources fetch mavproxy")
        assert self.run_dir
        port = _free_udp_port()
        self._map_output = Endpoint("udp", "127.0.0.1", port)
        state = self.run_dir / "mavproxy"
        state.mkdir(exist_ok=True)
        self.mavproxy = ManagedProcess(
            "mavproxy",
            [str(exe), f"--master=udpin:127.0.0.1:{port}", "--map", "--console",
             "--aircraft=hangar", f"--state-basedir={state}"],
            cwd=state, log_path=self.run_dir / "logs" / "mavproxy.log",
            on_line=self._mavproxy_line, on_exit=self._map_exited, stdin=True, quit_line="exit",
        )
        await self.mavproxy.start()
        for v in self.vehicles:
            if v.link:
                await v.link.set_outputs(self._link_outputs(v.ports.outputs))
        self.log("mavproxy", f"map and console open, MAVProxy on udp:{port}", "event")
        return True

    def mavproxy_command(self, sysid: int, command: str) -> None:
        """Run a MAVProxy command for one vehicle (Hangar's '!command')."""
        if not self.mavproxy or not self.map_open:
            raise SupervisorError("open the map first (MAVProxy runs with it)")
        self.mavproxy.write(f"vehicle {sysid}")
        self.mavproxy.write(command)

    def _mavproxy_line(self, source: str, text: str) -> None:
        text = _PROMPTS.sub("", text).strip()
        if text:
            self._line(source, text)

    def _map_exited(self, p: ManagedProcess) -> None:
        if p.state == ProcState.EXITED:
            self.log("mavproxy", "MAVProxy closed", "event")
        task = asyncio.create_task(self._drop_map_output())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _close_map(self) -> None:
        if self.mavproxy:
            await self.mavproxy.stop()
            self.mavproxy = None
        await self._drop_map_output()

    async def _drop_map_output(self) -> None:
        if not self._map_output:
            return
        self._map_output = None
        for v in self.vehicles:
            if v.link:
                await v.link.set_outputs(self._link_outputs(v.ports.outputs))

    async def restart_vehicle(self, name: str) -> None:
        if self.state != SimState.RUNNING:
            raise SupervisorError("simulation is not running")
        v = self.vehicle(name)
        self.log(v.name, "restarting SITL", "event")
        if v.link:
            await v.link.stop()
        if v.process:
            await v.process.stop()
        await self._start_vehicle(v)

    async def stop(self) -> None:
        if self.state in (SimState.IDLE, SimState.STOPPING):
            return
        if self._start_task and not self._start_task.done():
            self._start_task.cancel()
            try:
                await self._start_task
            except (asyncio.CancelledError, Exception):
                pass
        self._set_state(SimState.STOPPING)
        self.log("hangar", "stopping", "event")
        await self._teardown()
        self.started_at = None
        self._set_state(SimState.IDLE)
        self.log("hangar", "stopped", "event")
        self._close_logfile()

    async def _teardown(self) -> None:
        if self.mavproxy:
            await self.mavproxy.stop()
            self.mavproxy = None
            self._map_output = None
        for task in list(self._background):
            task.cancel()
        procs: list[ManagedProcess] = []
        for v in self.vehicles:
            procs += [p for p in (v.camera_viewer, v.camera_bridge) if p]
        await asyncio.gather(*(p.stop() for p in procs), return_exceptions=True)
        for v in self.vehicles:
            if v.camera_relay:
                v.camera_relay.close()
                v.camera_relay = None
        await asyncio.gather(*(v.link.stop() for v in self.vehicles if v.link), return_exceptions=True)
        await asyncio.gather(*(v.process.stop() for v in self.vehicles if v.process), return_exceptions=True)
        if self.gazebo:
            await self.gazebo.stop(timeout=8.0)
            self.gazebo = None

    def _kill_all(self) -> None:
        if self.mavproxy:
            self.mavproxy.kill_now()
        for v in self.vehicles:
            for p in (v.camera_viewer, v.camera_bridge, v.process):
                if p:
                    p.kill_now()
        if self.gazebo:
            self.gazebo.kill_now()

    # --- queries ---------------------------------------------------------

    def vehicle(self, name: str) -> VehicleRuntime:
        for v in self.vehicles:
            if v.name == name:
                return v
        raise SupervisorError(f"no vehicle named {name}")

    def plan(self) -> PortPlan | None:
        if self.vehicles:
            return PortPlan([v.ports for v in self.vehicles])
        return self.preflight(check_ports=False).plan


_PROMPTS = re.compile(r"^(?:[A-Z][A-Z0-9_]*> ?)+")  # MAVProxy prompts ("GUIDED> ") glued to its output


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def format_plain(line: LogLine) -> str:
    stamp = datetime.fromtimestamp(line.time).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"{stamp}  {line.level.upper():7}  {line.source:<20}  {line.text}"


def _wants_camera(model: VehicleModel, spec: VehicleSpec) -> bool:
    """A model's own camera streams unless switched off; any Gazebo model gets
    one added when the scenario asks for it."""
    if not model.needs_gazebo:
        return False
    if spec.camera is None:
        return model.has_camera
    return spec.camera.enabled


def _join_paths(items: list[Path], existing: str | None) -> str:
    parts = [str(p) for p in items]
    if existing:
        parts.append(existing)
    return ":".join(parts)
