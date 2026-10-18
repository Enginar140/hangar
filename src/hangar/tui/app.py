"""The Hangar terminal UI.

Everything the old setup spread over a Qt window and a dozen terminals lives
in one screen: the fleet table on top, and tabs for the selected vehicle,
logs, ports and the scenario. All work runs on the asyncio loop or in
workers, so the interface never blocks while SITLs and Gazebo start.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .. import paths
from ..catalog import Catalog
from ..doctor import run_checks
from ..i18n import t
from ..mavlink import CommandError
from ..ports import PlanError, output_rows
from ..scenario import Scenario, VehicleSpec
from ..settings import Settings
from ..supervisor import LogLine, SimState, Supervisor, SupervisorError, VehicleRuntime, format_plain
from .screens import (
    TYPE_ICONS,
    CameraOutputDialog,
    CameraScreen,
    ConfirmDialog,
    DoctorScreen,
    HelpScreen,
    OutputDialog,
    PathDialog,
    VehicleEditor,
    WorldCreateDialog,
    copy_text,
    highlight,
)

LEVEL_STYLE = {"error": "red", "warning": "yellow", "event": "cyan", "info": ""}
STATUS_STYLE = {
    "online": "green", "booting": "yellow", "silent": "yellow",
    "crashed": "bold red", "stopped": "dim", "planned": "dim",
}
STATE_STYLE = {
    SimState.IDLE: "dim", SimState.STARTING: "yellow", SimState.RUNNING: "green",
    SimState.STOPPING: "yellow", SimState.ERROR: "red",
}
FLEET_COLUMNS = ["#", "Name", "Model", "Status", "Mode", "Armed", "Ready", "Alt", "Speed", "Battery", "GPS"]
LAST_SCENARIO = "last-scenario.yaml"
OUTPUT_HINT = {"udp": "sends", "udpin": "listens (udp)", "tcpin": "listens (tcp)"}


def _fmt(value: float | None, unit: str = "", digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}{unit}"


def _format_log(line: LogLine, show_source: bool = True) -> str:
    stamp = datetime.fromtimestamp(line.time).strftime("%H:%M:%S")
    style = LEVEL_STYLE.get(line.level, "")
    text = escape(line.text)
    if style:
        text = f"[{style}]{text}[/]"
    src = f"[b]{escape(line.source):>16}[/b] " if show_source else ""
    return f"[dim]{stamp}[/] {src}{text}"


class HangarApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Hangar"
    BINDINGS = [
        Binding("s", "start", t("Start")),
        Binding("x", "stop", t("Stop")),
        Binding("a", "add", t("Add")),
        Binding("e", "edit", t("Edit")),
        Binding("d,delete", "remove", t("Remove")),
        Binding("ctrl+s", "save", t("Save")),
        Binding("ctrl+o", "open", t("Open")),
        Binding("f2", "doctor", t("Doctor")),
        Binding("f1,question_mark", "help", t("Help")),
        Binding("m", "map", t("Map")),
        Binding("q", "quit", t("Quit")),
        Binding("colon", "command_line", show=False),
        Binding("escape", "focus_fleet", show=False),
        Binding("1", "tab('tab-vehicle')", show=False),
        Binding("2", "tab('tab-logs')", show=False),
        Binding("3", "tab('tab-ports')", show=False),
        Binding("4", "tab('tab-scenario')", show=False),
    ]

    def __init__(self, scenario: Scenario | None = None, scenario_path: Path | None = None,
                 settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.catalog = Catalog(self.settings)
        self.scenario_path = scenario_path
        self.scenario = scenario or self._restore_last() or Scenario(name="hangar")
        self._pending_logs: list[LogLine] = []
        self.supervisor = self._make_supervisor()
        self.selected: str | None = None
        self._fleet_signature: tuple = ()
        self._mode_signature: tuple = ()
        self._syncing_form = False
        self._exiting = False

    @property
    def _main(self):
        """The main screen; queries must not go to a dialog on top of it."""
        return self.screen_stack[0]

    def _ui_alive(self) -> bool:
        return bool(self.screen_stack) and self.is_running and not self._exiting

    def _make_supervisor(self) -> Supervisor:
        return Supervisor(self.scenario, self.settings, self.catalog,
                          on_log=self._pending_logs.append, on_state=self._on_state)

    # --- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status")
        yield DataTable(id="fleet", cursor_type="row", zebra_stripes=True)
        with TabbedContent(initial="tab-vehicle"):
            with TabPane(t("Vehicle"), id="tab-vehicle"):
                with Horizontal(id="vehicle-top"):
                    yield Static(id="telemetry")
                    with Vertical(id="controls"):
                        with Horizontal(classes="row"):
                            yield Button(t("Arm"), id="arm", variant="success")
                            yield Button(t("Disarm"), id="disarm", variant="error")
                        with Horizontal(classes="row"):
                            yield Input("30", id="takeoff-alt", type="number", compact=True)
                            yield Button(t("Takeoff"), id="takeoff", variant="primary")
                        with Horizontal(classes="row"):
                            yield Button(t("RTL"), id="rtl")
                            yield Button(t("Land"), id="land")
                        yield Select([], prompt=t("Set mode"), id="mode", compact=True)
                        with Horizontal(classes="row"):
                            yield Button(t("Camera"), id="camera")
                            yield Button(t("Map"), id="map", variant="primary")
                        with Horizontal(classes="row"):
                            yield Button(t("Restart"), id="restart", variant="warning")
                yield Input(placeholder=t("Command: arm, disarm, mode GUIDED, takeoff 30, rtl, land, "
                                          "param set NAME VALUE, !MAVPROXY COMMAND"), id="command")
                yield RichLog(id="vehicle-log", markup=True, wrap=True, max_lines=2000)
            with TabPane(t("Logs"), id="tab-logs"):
                with Horizontal(id="log-bar"):
                    yield Select([(t("All"), "*")], prompt=t("Source"), id="log-source", allow_blank=False,
                                 value="*", compact=True)
                    yield Label(t("Warnings only"))
                    yield Switch(id="log-warn")
                    yield Button(t("Save"), id="log-save", compact=True)
                    yield Button(t("Copy"), id="log-copy", compact=True)
                    yield Button(t("Open folder"), id="log-open", compact=True)
                yield RichLog(id="log", markup=True, wrap=True, max_lines=5000)
            with TabPane(t("Ports"), id="tab-ports"):
                with VerticalScroll(id="ports-pane"):
                    yield Static(id="preflight")
                    with Horizontal(classes="section-bar"):
                        yield Label(t("MAVLink outputs"), classes="section")
                        yield Button(t("Add output"), id="out-add", variant="primary", compact=True)
                        yield Button(t("Remove output"), id="out-remove", variant="error", compact=True)
                    yield DataTable(id="outputs", cursor_type="row", zebra_stripes=True)
                    yield Static(id="ports-help", classes="hint-text")
                    with Horizontal(classes="section-bar"):
                        yield Label(t("Camera outputs"), classes="section")
                        yield Button(t("Send to a device"), id="vid-add", variant="primary", compact=True)
                        yield Button(t("Remove output"), id="vid-remove", variant="error", compact=True)
                    yield DataTable(id="video", cursor_type="row", zebra_stripes=True)
                    yield Label(t("Ports of each vehicle"), classes="section")
                    yield DataTable(id="ports", cursor_type="row", zebra_stripes=True)
            with TabPane(t("Scenario"), id="tab-scenario"):
                with Horizontal(id="scenario-form"):
                    with Vertical(classes="form"):
                        yield Label(t("Name"))
                        yield Input(id="sc-name")
                        yield Label(t("World"))
                        yield Select([(t("none (built-in physics)"), "")], id="sc-world", allow_blank=False,
                                     value="", compact=True)
                        yield Button(t("Create a world from a real place"), id="world-create", compact=True)
                        yield Label(t("Speed-up"))
                        yield Input(id="sc-speedup", type="number")
                        with Horizontal(classes="row"):
                            yield Switch(id="sc-headless")
                            yield Label(t("Headless Gazebo"))
                    yield Static(id="sc-yaml")
        yield Footer()

    def on_mount(self) -> None:
        if "tokyo-night" in self.available_themes:
            self.theme = "tokyo-night"
        fleet = self._main.query_one("#fleet", DataTable)
        for col in FLEET_COLUMNS:
            fleet.add_column(t(col), key=col)
        ports = self._main.query_one("#ports", DataTable)
        for col in ("#", "Name", "SysID", "Hangar ↔ SITL", "Scripts (this vehicle only)", "Gazebo FDM",
                    "Camera"):
            ports.add_column(t(col), key=col)
        outputs = self._main.query_one("#outputs", DataTable)
        for col in ("Vehicle", "Output", "For", "Shared"):
            outputs.add_column(t(col), key=col)
        self._output_rows: list = []
        video = self._main.query_one("#video", DataTable)
        for col in ("Vehicle", "Stream", "Goes to"):
            video.add_column(t(col), key=col)
        self._video_rows: list[tuple[str, str | None]] = []
        self._sync_form()
        self._refresh_all()
        self.set_interval(0.5, self._tick)
        self.set_interval(0.15, self._flush_logs)
        for w in self.catalog.warnings:
            self.supervisor.log("hangar", w, "warning")

    # --- periodic refresh ------------------------------------------------

    def _tick(self) -> None:
        if not self._ui_alive():
            return
        self._refresh_fleet()
        self._refresh_status()
        self._refresh_vehicle_panel()
        self._refresh_map_button()

    def _refresh_all(self) -> None:
        self._refresh_fleet()
        self._refresh_status()
        self._refresh_vehicle_panel()
        self._refresh_log_sources()
        self._refresh_ports()
        self._refresh_yaml()
        self._update_enabled()

    def _runtime(self, name: str | None) -> VehicleRuntime | None:
        if name is None:
            return None
        for v in self.supervisor.vehicles:
            if v.name == name and v.process is not None:
                return v
        return None

    def _refresh_status(self) -> None:
        sup = self.supervisor
        st = sup.state
        parts = [f"[{STATE_STYLE[st]}]● {t(st.value).upper()}[/]"]
        parts.append(f"{t('World')}: [b]{escape(self.scenario.world or t('none (built-in physics)'))}[/b]")
        parts.append(f"{len(self.scenario.vehicles)} {t('vehicles')}")
        if sup.started_at:
            up = int(time.time() - sup.started_at)
            parts.append(f"{t('Uptime')} {up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}")
        if self.scenario_path:
            parts.append(f"[dim]{escape(str(self.scenario_path))}[/]")
        self._main.query_one("#status", Static).update("   ".join(parts))
        self.sub_title = self.scenario.name

    def _fleet_row(self, i: int, spec: VehicleSpec) -> list:
        model = self.catalog.get(spec.model)
        icon = TYPE_ICONS.get(model.type, "?") if model else "?"
        rt = self._runtime(spec.name)
        status = rt.status if rt else "planned"
        tel = rt.link.telemetry if rt and rt.link else None
        armed = Text("ARMED", "bold red") if tel and tel.armed else Text("—" if not tel else "no", "dim")
        ready = "—"
        if tel and tel.ready is not None:
            ready = Text("✓", "green") if tel.ready else Text("✗", "yellow")
        battery = "—"
        if tel and tel.battery is not None:
            battery = Text(f"{tel.battery}%", "red" if tel.battery < 25 else "")
        return [
            str(i),
            Text(spec.name or "", "bold"),
            f"{icon} {spec.model}",
            Text(t(status), STATUS_STYLE.get(status, "")),
            tel.mode if tel else "—",
            armed,
            ready,
            _fmt(tel.alt_rel if tel else None, " m"),
            _fmt(tel.groundspeed if tel else None, " m/s"),
            battery,
            tel.gps_text if tel and tel.connected else "—",
        ]

    def _refresh_fleet(self) -> None:
        table = self._main.query_one("#fleet", DataTable)
        names = tuple(v.name for v in self.scenario.vehicles)
        rows = [self._fleet_row(i, v) for i, v in enumerate(self.scenario.vehicles)]
        if names != self._fleet_signature:
            cursor = table.cursor_row
            table.clear()
            for name, row in zip(names, rows):
                table.add_row(*row, key=name)
            self._fleet_signature = names
            if names:
                table.move_cursor(row=min(max(cursor, 0), len(names) - 1))
                self.selected = names[table.cursor_row]
            else:
                self.selected = None
            table.styles.height = min(len(names), 10) + 2
            return
        for name, row in zip(names, rows):
            for col, value in zip(FLEET_COLUMNS, row):
                table.update_cell(name, col, value, update_width=True)

    def _refresh_vehicle_panel(self) -> None:
        panel = self._main.query_one("#telemetry", Static)
        name = self.selected
        spec = next((v for v in self.scenario.vehicles if v.name == name), None)
        if spec is None:
            panel.update(Text.from_markup(t("No vehicles yet. Press [b]a[/b] to add one.")))
            return
        rt = self._runtime(name)
        model = self.catalog.get(spec.model)
        status = rt.status if rt else "planned"
        tel = rt.link.telemetry if rt and rt.link else None

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim")
        grid.add_column(min_width=14)
        grid.add_column(style="dim")
        grid.add_column(min_width=14)
        if tel:
            arm_text = Text("ARMED", "bold red") if tel.armed else Text("disarmed", "green")
            ready_text = Text("—")
            if tel.ready is not None:
                ready_text = Text("✓ ready", "green") if tel.ready else Text("✗ pre-arm", "yellow")
            bat = "—" if tel.battery is None else f"{tel.battery}%"
            if tel.voltage:
                bat += f"  {tel.voltage:.1f} V"
            climb = "—" if tel.climb is None else f"{tel.climb:+.1f} m/s"
            grid.add_row(t("Mode"), Text(tel.mode, "bold"), t("Armed"), arm_text)
            grid.add_row(t("Alt"), _fmt(tel.alt_rel, " m"), t("Climb"), climb)
            grid.add_row(t("Speed"), _fmt(tel.groundspeed, " m/s"), "Airspeed", _fmt(tel.airspeed, " m/s"))
            grid.add_row(t("Heading"), _fmt(tel.heading, "°", 0), t("Throttle"),
                         "—" if tel.throttle is None else f"{tel.throttle}%")
            grid.add_row(t("Battery"), bat, t("GPS"), tel.gps_text)
            grid.add_row(t("Ready"), ready_text, "", "")
            pos = "—" if tel.lat is None else f"{tel.lat:.6f}, {tel.lon:.6f}"
            grid.add_row(t("Position"), pos, "", "")
        if rt:
            outs = ", ".join(str(e) for e in rt.ports.outputs) or "—"
            grid.add_row("MAVLink", f"tcp:{rt.ports.serial0} → {outs}", "", "")
            grid.add_row(t("Scripts"), f"tcp:127.0.0.1:{rt.ports.serial1}  ({t('only this vehicle')})", "", "")
            if rt.ports.has_camera:
                if rt.ports.camera_mode == "direct":
                    send = rt.ports.camera_send
                    cam = f"→ {send.host}:{send.port}  ({t('direct, not to this computer')})"
                else:
                    cam = f"udp:{rt.ports.camera}" + "".join(f" → {x.host}:{x.port}" for x in rt.ports.camera_targets)
                    if rt.spec.camera and rt.spec.camera.v4l2_device:
                        cam += f" → {rt.spec.camera.v4l2_device}"
                grid.add_row(t("Camera"), cam, "", "")

        header = Text.assemble(
            (f"{TYPE_ICONS.get(model.type, '?') if model else '?'} ", "bold"),
            (spec.name or "", "bold"),
            (f"   {spec.model}", "dim"),
            ("   ● " + t(status), STATUS_STYLE.get(status, "")),
        )
        outer = Table.grid()
        outer.add_row(header)
        outer.add_row("")
        outer.add_row(grid)
        panel.update(outer)

        mode_sig = (name, tel.mav_type if tel else None)
        if mode_sig != self._mode_signature and rt and rt.link:
            self._mode_signature = mode_sig
            self._main.query_one("#mode", Select).set_options([(m, m) for m in rt.link.mode_names()])

    def _refresh_ports(self) -> None:
        self._preflight_worker()

    @work(thread=True, exclusive=True, group="preflight")
    def _preflight_worker(self) -> None:
        running = self.supervisor.state != SimState.IDLE
        pf = self.supervisor.preflight(check_ports=not running)
        self.call_from_thread(self._show_preflight, pf, running)

    def _show_preflight(self, pf, running: bool) -> None:
        if not self._ui_alive():
            return
        box = self._main.query_one("#preflight", Static)
        lines = [t("Connect QGroundControl / Mission Planner to the udp outputs below.")]
        if running:
            pf.errors = [e for e in pf.errors if not e.startswith("port conflict")]
        else:
            lines.append(f"[dim]{t('Ports are checked again on start.')}[/]")
        if pf.errors or pf.warnings:
            lines.append(f"\n[b]{t('Problems')}[/b]")
            lines += [f"[red]✗ {escape(e)}[/]" for e in pf.errors]
            lines += [f"[yellow]! {escape(w)}[/]" for w in pf.warnings]
        else:
            lines.append(f"[green]✓ {t('All checks passed')}[/]")
        if self.supervisor.run_dir:
            lines.append(f"\n[dim]{t('Run directory')}: {escape(str(self.supervisor.run_dir))}\n"
                         f"{t('Log file')}: {escape(str(self.supervisor.log_file))}[/]")
        box.update("\n".join(lines))

        table = self._main.query_one("#ports", DataTable)
        table.clear()
        plan = self.supervisor.plan() if running else pf.plan
        if plan:
            for r in plan.rows():
                table.add_row(r["index"], r["name"], r["sysid"], r["mavlink"], r["scripts"], r["fdm"], r["camera"])
        self._refresh_outputs()

    def _refresh_video(self) -> None:
        table = self._main.query_one("#video", DataTable)
        cursor = table.cursor_row
        table.clear()
        self._video_rows = []
        try:
            plan = self.supervisor._plan_for(self.scenario)
        except ValueError:
            return
        for spec, vp in zip(self.scenario.vehicles, plan.vehicles):
            if not vp.has_camera:
                continue
            name = spec.name or ""
            templates = spec.camera.outputs if spec.camera else []
            if vp.camera_mode == "relay":
                table.add_row(Text(name, "bold"), "relay", f"127.0.0.1:{vp.camera}  [dim]({t('this computer')})[/]")
                self._video_rows.append((name, None))
                expanded = [f"{x.host}:{x.port}" for x in vp.camera_targets]
            else:
                expanded = [f"{vp.camera_send.host}:{vp.camera_send.port}"]
            for tpl, shown in zip(templates, expanded):
                mode = "relay" if vp.camera_mode == "relay" else Text("direct", "yellow")
                table.add_row(Text(name, "bold"), mode, f"{shown}  [dim]{escape(tpl)}[/]")
                self._video_rows.append((name, tpl))
        if self._video_rows:
            table.move_cursor(row=min(max(cursor, 0), len(self._video_rows) - 1))

    @on(Button.Pressed, "#vid-add")
    def _video_add(self) -> None:
        names = [n for n, tpl in self._video_rows if tpl is None]
        if not names:
            self.notify(t("No camera streams through Hangar. Edit a vehicle (e), switch its camera on."),
                        severity="warning", timeout=6)
            return

        def done(result) -> None:
            if result:
                name, target = result
                spec = next(v for v in self.scenario.vehicles if v.name == name)
                self._video_change(name, [*spec.camera.outputs, target], f"+ {name} → {target}")

        self.push_screen(CameraOutputDialog(self.supervisor, names), done)

    @on(Button.Pressed, "#vid-remove")
    def _video_remove(self) -> None:
        table = self._main.query_one("#video", DataTable)
        if not self._video_rows or table.cursor_row < 0:
            return
        name, tpl = self._video_rows[table.cursor_row]
        spec = next(v for v in self.scenario.vehicles if v.name == name)
        if tpl is None:
            self.notify(t("This computer always gets a relayed camera."), severity="warning")
            return
        if spec.camera.stream == "direct":
            self.notify(t("A direct camera needs its one address; edit the vehicle (e) to change it."),
                        severity="warning", timeout=6)
            return
        self._video_change(name, [o for o in spec.camera.outputs if o != tpl], f"− {name} → {tpl}")

    @work(group="video")
    async def _video_change(self, name: str, outputs: list[str], label: str) -> None:
        try:
            live = await self.supervisor.set_camera_outputs(name, outputs)
        except (SupervisorError, ValueError) as e:
            self.notify(str(e).splitlines()[0], severity="error", timeout=8)
            return
        running = self.supervisor.state == SimState.RUNNING
        note = t("applied live") if live else (t("on the next start") if running else "")
        self.notify(label + (f"  ({note})" if note else ""))
        self._scenario_changed(refresh_form=False)

    def _refresh_outputs(self) -> None:
        self._refresh_video()
        table = self._main.query_one("#outputs", DataTable)
        cursor = table.cursor_row
        table.clear()
        try:
            self._output_rows = output_rows(self.scenario)
        except PlanError as e:
            self._output_rows = []
            self._main.query_one("#ports-help", Static).update(f"[red]✗ {escape(str(e))}[/]")
            return
        for i, r in enumerate(self._output_rows):
            what = OUTPUT_HINT.get(r.endpoint.kind, "")
            scope = t("every vehicle") if r.scope == "*" else r.scope
            shared = Text(f"+{len(r.shared_with)} {t('vehicles')}", "yellow") if r.shared_with else Text("—", "dim")
            table.add_row(Text(r.vehicle, "bold"), Text.assemble(str(r.endpoint), (f"  {t(what)}", "dim")),
                          f"{scope}  [dim]{escape(r.template)}[/]", shared, key=str(i))
        if self._output_rows:
            table.move_cursor(row=min(max(cursor, 0), len(self._output_rows) - 1))
        first = self.scenario.vehicles[0].name if self.scenario.vehicles else "?"
        self._main.query_one("#ports-help", Static).update(
            t("PORTS_HELP").replace("{first}", escape(first or "?"))
        )

    def _refresh_yaml(self) -> None:
        self._main.query_one("#sc-yaml", Static).update(highlight(self.scenario.dump(), "yaml"))

    def _sync_form(self) -> None:
        self._syncing_form = True
        try:
            sc = self.scenario
            self._main.query_one("#sc-name", Input).value = sc.name
            worlds = [(t("none (built-in physics)"), "")] + [(w, w) for w in sorted(self.catalog.worlds)]
            if sc.world and sc.world not in self.catalog.worlds:
                worlds.append((f"{sc.world} (?)", sc.world))  # keep it; preflight reports it
            world = self._main.query_one("#sc-world", Select)
            world.set_options(worlds)
            world.value = sc.world or ""
            self._main.query_one("#sc-speedup", Input).value = f"{sc.speedup:g}"
            self._main.query_one("#sc-headless", Switch).value = sc.headless
        finally:
            self.call_after_refresh(self._end_sync)

    def _end_sync(self) -> None:
        self._syncing_form = False

    def _update_enabled(self) -> None:
        editable = self.supervisor.state in (SimState.IDLE, SimState.ERROR)
        for w in self._main.query("#scenario-form Input, #scenario-form Select, #scenario-form Switch"):
            w.disabled = not editable
        running = self.supervisor.state == SimState.RUNNING
        for bid in ("arm", "disarm", "takeoff", "rtl", "land", "camera", "map", "restart", "mode", "command"):
            self._main.query_one(f"#{bid}").disabled = not running
        self._refresh_map_button()

    def _refresh_map_button(self) -> None:
        self._main.query_one("#map", Button).label = t("Close map") if self.supervisor.map_open else t("Map")

    # --- logs ------------------------------------------------------------

    def _log_matches(self, line: LogLine) -> bool:
        source = self._main.query_one("#log-source", Select).value
        if self._main.query_one("#log-warn", Switch).value and line.level not in ("warning", "error"):
            return False
        return source in ("*", Select.NULL) or line.source == source or line.source.startswith(f"{source}/")

    def _vehicle_log_matches(self, line: LogLine) -> bool:
        return bool(self.selected) and (line.source == self.selected or line.source == "hangar"
                                        or line.source.startswith(f"{self.selected}/"))

    def _flush_logs(self) -> None:
        if not self._pending_logs or not self._ui_alive():
            return
        batch, self._pending_logs[:] = list(self._pending_logs), []
        main = self._main.query_one("#log", RichLog)
        vlog = self._main.query_one("#vehicle-log", RichLog)
        for line in batch:
            if self._log_matches(line):
                main.write(_format_log(line))
            if self._vehicle_log_matches(line) and not line.source.endswith("/sitl"):
                vlog.write(_format_log(line, show_source=line.source == "hangar"))

    def _rebuild_logs(self) -> None:
        self._flush_logs()
        main = self._main.query_one("#log", RichLog)
        main.clear()
        for line in list(self.supervisor.logs)[-3000:]:
            if self._log_matches(line):
                main.write(_format_log(line))

    def _rebuild_vehicle_log(self) -> None:
        vlog = self._main.query_one("#vehicle-log", RichLog)
        vlog.clear()
        for line in list(self.supervisor.logs)[-2000:]:
            if self._vehicle_log_matches(line) and not line.source.endswith("/sitl"):
                vlog.write(_format_log(line, show_source=line.source == "hangar"))

    def _refresh_log_sources(self) -> None:
        sel = self._main.query_one("#log-source", Select)
        current = sel.value
        options = [(t("All"), "*"), ("hangar", "hangar")]
        if self.scenario.world:
            options.append(("gazebo", "gazebo"))
        for v in self.scenario.vehicles:
            options.append((v.name, v.name))
        sel.set_options(options)
        values = [v for _, v in options]
        sel.value = current if current in values else "*"

    # --- events ----------------------------------------------------------

    def _on_state(self, state: SimState) -> None:
        if not self._ui_alive():
            return
        self._update_enabled()
        self._refresh_status()
        if state in (SimState.RUNNING, SimState.IDLE, SimState.ERROR):
            self._refresh_ports()

    @on(DataTable.RowHighlighted, "#fleet")
    def _fleet_highlight(self, event: DataTable.RowHighlighted) -> None:
        name = event.row_key.value if event.row_key else None
        if name != self.selected:
            self.selected = name
            self._mode_signature = ()
            self._refresh_vehicle_panel()
            self._rebuild_vehicle_log()

    @on(DataTable.RowSelected, "#fleet")
    def _fleet_select(self) -> None:
        self.action_tab("tab-vehicle")

    @on(Select.Changed, "#log-source")
    @on(Switch.Changed, "#log-warn")
    def _log_filter(self) -> None:
        self._rebuild_logs()

    def _filtered_logs(self) -> list[LogLine]:
        return [line for line in self.supervisor.logs if self._log_matches(line)]

    @on(Button.Pressed, "#log-save")
    def _log_save(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        default = str(Path.cwd() / f"hangar-log-{stamp}.txt")

        def done(path: str | None) -> None:
            if not path:
                return
            lines = self._filtered_logs()
            try:
                Path(path).write_text("".join(format_plain(x) + "\n" for x in lines), encoding="utf-8")
            except OSError as e:
                self.notify(str(e), severity="error")
                return
            self.notify(f"{t('Saved')}: {path} ({len(lines)} {t('lines')})", timeout=6)

        self.push_screen(PathDialog(t("Save the log shown (with the current filter)"), default), done)

    @on(Button.Pressed, "#log-copy")
    def _log_copy(self) -> None:
        text = "".join(format_plain(x) + "\n" for x in self._filtered_logs()[-2000:])
        if copy_text(text):
            self.notify(t("Copied to the clipboard"))
        else:
            self.copy_to_clipboard(text)
            self.notify(t("No clipboard tool found: sudo apt install wl-clipboard xclip"), severity="warning",
                        timeout=8)

    @on(Button.Pressed, "#log-open")
    def _log_open(self) -> None:
        import shutil
        import subprocess

        folder = self.supervisor.run_dir or paths.runs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.notify(str(folder), timeout=8)

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-ports":
            self._refresh_ports()

    @on(Input.Changed, "#sc-name")
    @on(Input.Changed, "#sc-speedup")
    @on(Select.Changed, "#sc-world")
    @on(Switch.Changed, "#sc-headless")
    def _scenario_form(self) -> None:
        if self._syncing_form:
            return
        sc = self.scenario
        sc.name = self._main.query_one("#sc-name", Input).value.strip() or "hangar"
        world = self._main.query_one("#sc-world", Select).value
        sc.world = str(world) if world not in ("", Select.NULL, Select.BLANK) else None
        try:
            sc.speedup = max(0.1, float(self._main.query_one("#sc-speedup", Input).value or 1))
        except ValueError:
            pass
        sc.headless = self._main.query_one("#sc-headless", Switch).value
        self._scenario_changed(refresh_form=False)

    def _scenario_changed(self, refresh_form: bool = True) -> None:
        self.scenario.assign_names()
        if refresh_form:
            self._sync_form()
        self._refresh_all()
        self._autosave()

    def _autosave(self) -> None:
        try:
            paths.state_dir().mkdir(parents=True, exist_ok=True)
            self.scenario.save(paths.state_dir() / LAST_SCENARIO)
        except OSError:
            pass

    def _restore_last(self) -> Scenario | None:
        last = paths.state_dir() / LAST_SCENARIO
        if last.exists():
            try:
                return Scenario.load(last)
            except Exception:
                return None
        return None

    # --- vehicle controls --------------------------------------------------

    def _selected_link(self):
        rt = self._runtime(self.selected)
        if not rt or not rt.link:
            self.notify(t("Select a vehicle in the fleet table."), severity="warning")
            return None
        return rt.link

    @work(group="command")
    async def _vehicle_command(self, label: str, coro) -> None:
        name = self.selected
        try:
            result = await coro
            self.supervisor.log(name or "hangar", f"{label}: {result}", "event")
            self.notify(f"{name}: {label} — {result}", timeout=3)
        except (CommandError, SupervisorError) as e:
            self.supervisor.log(name or "hangar", f"{label}: {e}", "error")
            self.notify(f"{name}: {label} — {e}", severity="error", timeout=6)

    @on(Button.Pressed, "#arm")
    def _arm(self) -> None:
        if link := self._selected_link():
            self._vehicle_command("arm", link.arm())

    @on(Button.Pressed, "#disarm")
    def _disarm(self) -> None:
        if link := self._selected_link():
            self._vehicle_command("disarm", link.disarm())

    @on(Button.Pressed, "#takeoff")
    def _takeoff(self) -> None:
        if link := self._selected_link():
            try:
                alt = float(self._main.query_one("#takeoff-alt", Input).value or 30)
            except ValueError:
                alt = 30.0
            self._vehicle_command("takeoff", link.takeoff(alt))

    @on(Button.Pressed, "#rtl")
    def _rtl(self) -> None:
        if link := self._selected_link():
            self._vehicle_command("rtl", link.rtl())

    @on(Button.Pressed, "#land")
    def _land(self) -> None:
        if link := self._selected_link():
            self._vehicle_command("land", link.land())

    @on(Select.Changed, "#mode")
    def _mode(self, event: Select.Changed) -> None:
        if event.value in (Select.NULL, Select.BLANK):
            return
        if link := self._selected_link():
            self._vehicle_command(f"mode {event.value}", link.set_mode(str(event.value)))
        event.select.clear()

    @on(Button.Pressed, "#camera")
    def _camera(self) -> None:
        rt = self._runtime(self.selected)
        if not rt or not rt.ports.has_camera:
            self.notify(t("This vehicle has no camera. Edit it (e) and switch the camera on."), severity="warning")
            return
        self.push_screen(CameraScreen(self.supervisor, rt.name))

    @on(Button.Pressed, "#map")
    def _map_button(self) -> None:
        self.action_map()

    def action_map(self) -> None:
        if self.supervisor.state != SimState.RUNNING and not self.supervisor.map_open:
            self.notify(t("Start the simulation first (s)."), severity="warning")
            return
        if self.settings.mavproxy() is None:
            self._offer_mavproxy()
            return
        self._map_worker()

    @work(exclusive=True, group="map")
    async def _map_worker(self) -> None:
        try:
            opened = await self.supervisor.toggle_map()
        except SupervisorError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self._refresh_map_button()
        self.notify(t("Map and console opened: every vehicle is on one map. '!command' in the command line "
                      "goes to MAVProxy.") if opened else t("Map closed"), timeout=6)

    # --- worlds from real places (hangar-earth) -------------------------------

    @on(Button.Pressed, "#world-create")
    def _world_create(self) -> None:
        def done(params) -> None:
            if params:
                self._world_worker(params)

        self.push_screen(WorldCreateDialog(set(self.catalog.worlds)), done)

    @work(exclusive=True, group="world")
    async def _world_worker(self, params: dict) -> None:
        from .. import sources

        exe = self.settings.hangar_earth()
        src = sources.load_manifest()["hangar_earth"]
        if exe is None or src.outdated:
            question = t("HANGAR_EARTH_OFFER" if exe is None else "HANGAR_EARTH_UPDATE").replace(
                "{license}", src.license).replace("{old}", src.installed_ref or "").replace("{new}", src.ref)
            if not await self.push_screen_wait(ConfirmDialog(question, ok=t("Install"), variant="primary")):
                if exe is None:
                    return
                src = None  # keep the installed version
        if src is not None and (exe is None or src.outdated):
            self.supervisor.log("hangar-earth", f"installing {src.title} {src.ref}", "event")
            try:
                await asyncio.to_thread(sources._python_env, src, lambda line: self.call_from_thread(
                    self.supervisor.log, "hangar-earth", line, "info"))
            except Exception as e:  # noqa: BLE001
                self.notify(f"hangar-earth: {e}", severity="error", timeout=10)
                return
            exe = self.settings.hangar_earth()
            assert exe
        name = params["name"]
        out = paths.data_dir() / "worlds" / name
        self.action_tab("tab-logs")
        self.notify(t("Building the world, this takes about 15-60 seconds..."), timeout=6)
        argv = [str(exe), "create", "--lat", str(params["lat"]), "--lon", str(params["lon"]),
                "--size", str(params["size"]), "--name", name, "--out", str(out)]
        if not params.get("buildings", True):
            argv.append("--no-buildings")
        for key in ("dem", "imagery"):
            if params.get(key):
                argv += [f"--{key}", params[key]]
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout
        async for raw in proc.stdout:
            self.supervisor.log("hangar-earth", raw.decode(errors="replace").rstrip(), "info")
        if await proc.wait() != 0:
            self.notify(t("The world could not be built; see the Logs tab."), severity="error", timeout=10)
            return
        self.catalog.scan()
        if self.supervisor.state in (SimState.IDLE, SimState.ERROR):
            self.scenario.world = name
            self._scenario_changed()
        else:
            self._sync_form()
        self.supervisor.log("hangar-earth", f"world {name} is ready", "event")
        self.notify(f"{t('World ready')}: {name}", timeout=6)

    @work(exclusive=True, group="map")
    async def _offer_mavproxy(self) -> None:
        from .. import sources

        src = sources.load_manifest()["mavproxy"]
        question = t("MAVPROXY_OFFER").replace("{license}", src.license)
        if not await self.push_screen_wait(ConfirmDialog(question, ok=t("Install"), variant="primary")):
            return
        self.notify(t("Installing MAVProxy, this takes about a minute..."), timeout=8)
        self.supervisor.log("hangar", f"installing {src.title} into {src.path}", "event")

        def install() -> None:
            sources._python_env(src, progress=lambda line: self.call_from_thread(
                self.supervisor.log, "hangar", line, "info"))

        try:
            await asyncio.to_thread(install)
        except Exception as e:  # noqa: BLE001 - show any failure to the user
            self.supervisor.log("hangar", f"MAVProxy install failed: {e}", "error")
            self.notify(f"{t('MAVProxy install failed')}: {e}", severity="error", timeout=10)
            return
        self.supervisor.log("hangar", "MAVProxy installed", "event")
        if self.supervisor.state == SimState.RUNNING:
            self._map_worker()
        else:
            self.notify(t("MAVProxy installed."))

    # --- outputs (Ports tab) ---------------------------------------------------

    @on(Button.Pressed, "#out-add")
    def _out_add(self) -> None:
        if not self.scenario.vehicles:
            self.notify(t("No vehicles yet. Press [b]a[/b] to add one."), severity="warning")
            return

        def done(result) -> None:
            if result:
                self._output_change(self.supervisor.add_output(*result), f"+ {result[0]}")

        self.push_screen(OutputDialog(self.supervisor, [v.name or "" for v in self.scenario.vehicles]), done)

    @on(Button.Pressed, "#out-remove")
    def _out_remove(self) -> None:
        table = self._main.query_one("#outputs", DataTable)
        if not self._output_rows or table.cursor_row < 0:
            return
        row = self._output_rows[table.cursor_row]
        self._output_change(self.supervisor.remove_output(row.template, row.scope), f"− {row.template}")

    @work(group="outputs")
    async def _output_change(self, coro, label: str) -> None:
        try:
            await coro
        except SupervisorError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        live = self.supervisor.state == SimState.RUNNING
        self.notify(f"{label}" + (f"  ({t('applied live')})" if live else ""))
        self._scenario_changed(refresh_form=False)

    @on(Button.Pressed, "#restart")
    def _restart(self) -> None:
        if self.selected:
            self._vehicle_command("restart", self._restart_vehicle(self.selected))

    async def _restart_vehicle(self, name: str) -> str:
        await self.supervisor.restart_vehicle(name)
        self._rebuild_vehicle_log()
        return "SITL restarted"

    @on(Input.Submitted, "#command")
    def _command(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if line.startswith("!"):
            rt = self._runtime(self.selected)
            if not rt:
                self.notify(t("Select a vehicle in the fleet table."), severity="warning")
                return
            try:
                self.supervisor.mavproxy_command(rt.ports.sysid, line[1:].strip())
                self.supervisor.log("mavproxy", f"{rt.name}> {line[1:].strip()}", "event")
            except SupervisorError as e:
                self.notify(t("Open the map first (m): MAVProxy runs with it."), severity="warning")
                self.supervisor.log("mavproxy", str(e), "warning")
            return
        if line and (link := self._selected_link()):
            self.supervisor.log(self.selected or "hangar", f"> {line}", "event")
            self._vehicle_command(line.split()[0], link.run(line))

    # --- actions -----------------------------------------------------------

    def action_tab(self, tab: str) -> None:
        self._main.query_one(TabbedContent).active = tab

    def action_focus_fleet(self) -> None:
        self._main.query_one("#fleet", DataTable).focus()

    def action_command_line(self) -> None:
        self.action_tab("tab-vehicle")
        cmd = self._main.query_one("#command", Input)
        if not cmd.disabled:
            cmd.focus()

    def _editable(self) -> bool:
        if self.supervisor.state in (SimState.IDLE, SimState.ERROR):
            return True
        self.notify(t("Stop the simulation to edit the scenario."), severity="warning")
        return False

    def action_start(self) -> None:
        if self.supervisor.state in (SimState.IDLE, SimState.ERROR):
            self.action_tab("tab-logs")
            self._start_worker()

    @work(exclusive=True, group="sim")
    async def _start_worker(self) -> None:
        try:
            await self.supervisor.start()
        except SupervisorError as e:
            self.notify(f"{t('Start failed')}: {e}", severity="error", timeout=8)
            self.action_tab("tab-ports")
        else:
            self.action_tab("tab-vehicle")

    def action_stop(self) -> None:
        if self.supervisor.state in (SimState.STARTING, SimState.RUNNING, SimState.ERROR):
            self._stop_worker()

    @work(exclusive=True, group="stop")
    async def _stop_worker(self) -> None:
        await self.supervisor.stop()

    def action_add(self) -> None:
        if not self._editable():
            return

        def done(spec: VehicleSpec | None) -> None:
            if spec:
                self.scenario.vehicles.append(spec)
                self._scenario_changed()

        self.push_screen(VehicleEditor(self.catalog), done)

    def action_edit(self) -> None:
        if not self._editable() or not self.selected:
            return
        idx = next((i for i, v in enumerate(self.scenario.vehicles) if v.name == self.selected), None)
        if idx is None:
            return

        def done(spec: VehicleSpec | None) -> None:
            if spec:
                self.scenario.vehicles[idx] = spec
                self._fleet_signature = ()
                self._scenario_changed()

        self.push_screen(VehicleEditor(self.catalog, self.scenario.vehicles[idx]), done)

    def action_remove(self) -> None:
        if not self._editable() or not self.selected:
            return
        self.scenario.vehicles = [v for v in self.scenario.vehicles if v.name != self.selected]
        self._scenario_changed()

    def action_save(self) -> None:
        default = str(self.scenario_path or Path.cwd() / f"{self.scenario.name}.yaml")

        def done(path: str | None) -> None:
            if path:
                try:
                    self.scenario.save(path)
                except OSError as e:
                    self.notify(str(e), severity="error")
                    return
                self.scenario_path = Path(path)
                self.notify(f"{t('Saved')}: {path}")
                self._refresh_status()

        self.push_screen(PathDialog(t("Scenario file"), default), done)

    def action_open(self) -> None:
        if not self._editable():
            return
        default = str(self.scenario_path or Path.cwd())

        def done(path: str | None) -> None:
            if not path:
                return
            try:
                scenario = Scenario.load(path)
            except Exception as e:  # noqa: BLE001 - show any parse error to the user
                self.notify(str(e).splitlines()[0], severity="error", timeout=8)
                return
            self.scenario = scenario
            self.scenario_path = Path(path)
            self.supervisor = self._make_supervisor()
            self._fleet_signature = ()
            self._scenario_changed()
            self._rebuild_logs()
            self.notify(f"{t('Loaded')}: {path}")

        self.push_screen(PathDialog(t("Scenario file"), default), done)

    @work(exclusive=True, group="doctor")
    async def action_doctor(self) -> None:
        import asyncio

        checks = await asyncio.to_thread(run_checks, self.settings)
        await self.push_screen_wait(DoctorScreen(checks))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @work(exclusive=True, group="quit")
    async def action_quit(self) -> None:
        if self.supervisor.state not in (SimState.IDLE, SimState.ERROR):
            if not await self.push_screen_wait(ConfirmDialog(t("Simulation is running. Stop it and quit?"))):
                return
            await self.supervisor.stop()
        self.exit()

    async def on_unmount(self) -> None:
        self._exiting = True
        await self.supervisor.stop()

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand(f"Hangar: {t('Start')}", "Start the simulation", self.action_start)
        yield SystemCommand(f"Hangar: {t('Stop')}", "Stop the simulation", self.action_stop)
        yield SystemCommand(f"Hangar: {t('Add vehicle')}", "Add a vehicle to the scenario", self.action_add)
        yield SystemCommand(f"Hangar: {t('Save')}", "Save the scenario to a file", self.action_save)
        yield SystemCommand(f"Hangar: {t('Open')}", "Open a scenario file", self.action_open)
        yield SystemCommand(f"Hangar: {t('Doctor')}", "Check the installation", self.action_doctor)
        yield SystemCommand(f"Hangar: {t('Map')}", "Open or close the shared MAVProxy map", self.action_map)
