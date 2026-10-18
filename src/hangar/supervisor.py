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
import shutil
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import camera, paths
from .catalog import Catalog, VehicleModel
from .mavlink import VehicleLink
from .ports import PortPlan, VehiclePorts, port_in_use
from .process import ManagedProcess, ProcState
from .scenario import Scenario, VehicleSpec
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
        atexit.register(self._kill_all)

    # --- logging / state -------------------------------------------------

    def log(self, source: str, text: str, level: str = "info") -> None:
        line = LogLine(time.time(), source, level, text)
        self.logs.append(line)
        if self.on_log:
            self.on_log(line)

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

        if any(m.needs_gazebo for m in models.values()):
            if not sc.world:
                errors.append("Gazebo vehicles need a world: set `world:` in the scenario")
            elif sc.world not in self.catalog.worlds:
                errors.append(f"unknown world '{sc.world}' (see `hangar worlds`)")
            if not shutil.which("gz"):
                errors.append("Gazebo (`gz`) is not installed or not on PATH")
            if not any((p / "libArduPilotPlugin.so").exists() for p in self.settings.resolved_plugin_paths()):
                warnings.append("libArduPilotPlugin.so not found in gazebo.plugin_paths")

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

    # --- start / stop ----------------------------------------------------

    async def start(self) -> None:
        if self.state in (SimState.STARTING, SimState.RUNNING, SimState.STOPPING):
            raise SupervisorError(f"simulation is {self.state.value}")
        self._start_task = asyncio.current_task()
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
        self.log("hangar", f"run directory: {self.run_dir}", "event")

        self.vehicles = [
            VehicleRuntime(spec, self.catalog.get(spec.model), ports, self.run_dir / ports.name)  # type: ignore[arg-type]
            for spec, ports in zip(self.scenario.vehicles, pf.plan.vehicles)
        ]

        if self.uses_gazebo:
            await self._start_gazebo()

        await asyncio.gather(*(self._start_vehicle(v) for v in self.vehicles))
        self.started_at = time.time()
        self._set_state(SimState.RUNNING)
        self.log("hangar", f"{len(self.vehicles)} vehicle(s) running", "event")

        if self.uses_gazebo:  # cameras only need Gazebo, not the autopilots
            for v in self.vehicles:
                if v.ports.camera:
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
            WorldVehicle(v.model, v.ports, v.spec.pose_sdf, v.spec.camera)
            for v in self.vehicles if v.model.needs_gazebo
        ]
        ardupilot_models = {n for n, m in self.catalog.vehicles.items() if m.needs_gazebo}
        world_file = await asyncio.to_thread(
            build_world, world.path, gz_vehicles, self.world_dir(), ardupilot_models,
            self.settings.resolved_model_paths(),
        )
        for v in gz_vehicles:
            if v.adds_camera:
                self.log(v.instance_name, f"camera added to the model (udp:{v.ports.camera})", "event")
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

        home = self.scenario.home
        if not m.needs_gazebo:  # built-in physics: spread vehicles out by their pose
            x, y, yaw = v.spec.pose[0], v.spec.pose[1], v.spec.pose[5]
            home = home.offset(north_m=y, east_m=x)
            if yaw:
                home = home.model_copy(update={"heading": (90.0 - yaw) % 360})

        argv = [
            str(exe),
            "-I", str(v.ports.index),
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
            v.name, v.ports.sysid, v.ports.serial0, v.ports.outputs,
            on_text=lambda name, level, text: self.log(name, text, level),
        )
        await v.link.start()

    def _vehicle_exited(self, v: VehicleRuntime, p: ManagedProcess) -> None:
        if p.state == ProcState.EXITED:
            self.log(v.name, f"SITL exited unexpectedly (code {p.returncode}), see {p.log_path}", "error")

    async def _start_camera(self, v: VehicleRuntime) -> None:
        self.log(v.name, "camera: enabling stream", "event")
        topic = await camera.enable_streaming(v.name, self._env)
        if not topic:
            self.log(v.name, "camera: enable topic not found", "warning")
            return
        v.camera_topic = topic
        self.log(v.name, f"camera streaming on udp:{v.ports.camera}", "event")
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
        if not v.ports.camera:
            raise SupervisorError(f"{name} has no camera")
        cam = v.spec.camera
        own = v.model.camera_size or (1280, 720)
        explicit = cam.model_fields_set if cam else set()
        width = cam.width if cam and (not v.model.has_camera or "width" in explicit) else own[0]
        height = cam.height if cam and (not v.model.has_camera or "height" in explicit) else own[1]
        fps = cam.fps if cam else 30
        device = cam.v4l2_device if cam else None
        return {
            "port": v.ports.camera, "width": width, "height": height, "fps": fps, "device": device,
            "streaming": bool(v.camera_topic),
            "viewer": bool(v.camera_viewer and v.camera_viewer.alive),
            "usage": camera.usage(v.ports.camera, width, height, device),
        }

    async def toggle_camera_view(self, name: str) -> bool:
        """Open or close a viewer window. Returns True when it is now open."""
        v = self.vehicle(name)
        if not v.ports.camera:
            raise SupervisorError(f"{name} has no camera")
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
                await v.link.set_outputs(ports.outputs)

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

    async def _teardown(self) -> None:
        for task in list(self._background):
            task.cancel()
        procs: list[ManagedProcess] = []
        for v in self.vehicles:
            procs += [p for p in (v.camera_viewer, v.camera_bridge) if p]
        await asyncio.gather(*(p.stop() for p in procs), return_exceptions=True)
        await asyncio.gather(*(v.link.stop() for v in self.vehicles if v.link), return_exceptions=True)
        await asyncio.gather(*(v.process.stop() for v in self.vehicles if v.process), return_exceptions=True)
        if self.gazebo:
            await self.gazebo.stop(timeout=8.0)
            self.gazebo = None

    def _kill_all(self) -> None:
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
