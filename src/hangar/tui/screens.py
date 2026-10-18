"""Modal dialogs of the TUI."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Select, Static, Switch

from ..catalog import Catalog
from ..doctor import Check
from ..i18n import t
from ..scenario import CameraSpec, VehicleSpec

TYPE_ICONS = {"copter": "✣", "plane": "✈", "rover": "⛟", "boat": "⛴"}


QUALITY_SIZES = {"low": (640, 360), "medium": (1280, 720), "high": (1920, 1080)}
QUALITY_OPTIONS = [
    (t("Low 640×360"), "low"), (t("Medium 1280×720"), "medium"), (t("High 1920×1080"), "high"),
    (t("Custom"), "custom"),
]
DIRECTION_POSES: dict[str, list[float] | None] = {
    "forward": None,  # the model's nose position (vehicles.yaml) or the default
    "down": [0.0, 0.0, -0.05, 0.0, 90.0, 0.0],
}
STREAM_OPTIONS = [
    (t("via Hangar: this computer + every address below"), "relay"),
    (t("direct: the camera sends to one address only"), "direct"),
]
DIRECTION_OPTIONS = [(t("ahead"), "forward"), (t("down"), "down"), (t("as in YAML"), "keep")]


def _quality_of(w: int, h: int) -> str:
    return next((k for k, size in QUALITY_SIZES.items() if size == (w, h)), "custom")


def _direction_of(pose: list[float] | None) -> str:
    if pose is None:
        return "forward"
    return "down" if abs(pose[4] - 90) < 1e-6 and not any(pose[:2]) else "keep"


class _Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", t("Cancel"), show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)


class VehicleEditor(_Dialog):
    """Add a vehicle, or edit one when ``spec`` is given. Returns a VehicleSpec."""

    def __init__(self, catalog: Catalog, spec: VehicleSpec | None = None):
        super().__init__()
        self.catalog = catalog
        self.spec = spec

    def compose(self) -> ComposeResult:
        s = self.spec
        options = []
        for name, m in sorted(self.catalog.vehicles.items(), key=lambda kv: (kv[1].kind, kv[0])):
            label = Text.assemble(
                (f"{TYPE_ICONS.get(m.type, '?')} ", "bold"),
                name,
                (f"  {m.type} · {'Gazebo' if m.needs_gazebo else 'SITL'}", "dim"),
                ("  📷" if m.has_camera else "", ""),
            )
            options.append((label, name))
        pose_text = " ".join(f"{v:g}" for v in (s.pose[0], s.pose[1], s.pose[2], s.pose[5])) \
            if s and s.placed else ""
        with VerticalScroll(classes="dialog compact"):
            yield Label(t("Edit vehicle") if s else t("Add vehicle"), classes="dialog-title")
            yield Label(t("Model"))
            yield Select(options, prompt=t("Pick a model"), id="model",
                         value=s.model if s and s.model in self.catalog.vehicles else Select.NULL)
            yield Label(t("Name"))
            yield Input(s.name if s else "", placeholder="auto", id="name")
            yield Label(t("Pose x y z yaw (m, deg)"))
            yield Input(pose_text, placeholder=t("auto: lined up 15 m apart"), id="pose")
            yield Label(t("Parameters NAME=VALUE (comma separated)"))
            yield Input(", ".join(f"{k}={v:g}" for k, v in (s.params.items() if s else [])),
                        placeholder="SIM_WIND_SPD=5", id="params")
            cam = s.camera if s and s.camera else CameraSpec()
            with Horizontal(classes="switch-row"):
                yield Switch(self._camera_default(), id="camera")
                yield Label(t("Camera (added to the model if it has none)"))
            with Horizontal(classes="field-row cam-field"):
                yield Label(t("Quality"))
                yield Select(QUALITY_OPTIONS, id="cam-quality", allow_blank=False, compact=True,
                             value=_quality_of(cam.width, cam.height))
                yield Input(str(cam.width), id="cam-w", type="integer", compact=True)
                yield Label("×")
                yield Input(str(cam.height), id="cam-h", type="integer", compact=True)
            with Horizontal(classes="field-row cam-field"):
                yield Label("FPS")
                yield Input(str(cam.fps), id="cam-fps", type="integer", compact=True)
                yield Label(t("FOV°"))
                yield Input(f"{cam.fov:g}", id="cam-fov", type="number", compact=True)
                yield Label(t("Looks"))
                yield Select(DIRECTION_OPTIONS, id="cam-dir", allow_blank=False, compact=True,
                             value=_direction_of(cam.pose))
            with Horizontal(classes="field-row cam-field"):
                yield Label(t("Stream"))
                yield Select(STREAM_OPTIONS, id="cam-stream", allow_blank=False, compact=True, value=cam.stream)
            yield Label(t("Also send to (host:port, comma separated)"), classes="cam-field")
            yield Input(", ".join(cam.outputs), placeholder="10.0.0.5:5600, 192.168.1.20:{5600+i}",
                        id="cam-outputs", classes="cam-field")
            yield Label(t("Camera to v4l2 device (optional)"))
            yield Input(cam.v4l2_device or "", placeholder="/dev/video2", id="v4l2")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

    def _camera_default(self) -> bool:
        if self.spec and self.spec.camera:
            return self.spec.camera.enabled
        m = self.catalog.get(self.spec.model) if self.spec else None
        return bool(m and m.has_camera)

    def on_mount(self) -> None:
        self._sync_camera_fields()

    @on(Select.Changed, "#model")
    def _model_changed(self, event: Select.Changed) -> None:
        m = self.catalog.get(str(event.value)) if isinstance(event.value, str) else None
        cam = self.query_one("#camera", Switch)
        cam.disabled = not (m and m.needs_gazebo)  # built-in physics has nothing to film
        if m and not self.spec:
            cam.value = m.has_camera
            if m.camera_size:
                self.query_one("#cam-w", Input).value = str(m.camera_size[0])
                self.query_one("#cam-h", Input).value = str(m.camera_size[1])
        self._sync_camera_fields()

    @on(Switch.Changed, "#camera")
    def _sync_camera_fields(self) -> None:
        on = self.query_one("#camera", Switch).value and not self.query_one("#camera", Switch).disabled
        for w in self.query(".cam-field Input, .cam-field Select, Input.cam-field, #v4l2"):
            w.disabled = not on
        m = self._model()
        if on and m and m.has_camera:  # the model's own camera stays where it is
            self.query_one("#cam-dir", Select).disabled = True

    @on(Select.Changed, "#cam-quality")
    def _quality(self, event: Select.Changed) -> None:
        size = QUALITY_SIZES.get(str(event.value))
        if size:
            self.query_one("#cam-w", Input).value = str(size[0])
            self.query_one("#cam-h", Input).value = str(size[1])

    @on(Input.Changed, "#cam-w")
    @on(Input.Changed, "#cam-h")
    def _size_typed(self) -> None:
        try:
            q = _quality_of(int(self.query_one("#cam-w", Input).value), int(self.query_one("#cam-h", Input).value))
        except ValueError:
            return
        sel = self.query_one("#cam-quality", Select)
        if sel.value != q:
            sel.value = q

    def _model(self):
        v = self.query_one("#model", Select).value
        return self.catalog.get(v) if isinstance(v, str) else None

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        model = self.query_one("#model", Select).value
        if model is Select.NULL or model is Select.BLANK:
            self.notify(t("Pick a model"), severity="warning")
            return
        try:
            pose = [float(x) for x in self.query_one("#pose", Input).value.replace(",", " ").split()]
            params = {}
            for item in self.query_one("#params", Input).value.split(","):
                if item.strip():
                    k, v = item.split("=", 1)
                    params[k.strip().upper()] = float(v)
            outputs = list(self.spec.outputs) if self.spec else []  # edited in the Ports tab
            device = self.query_one("#v4l2", Input).value.strip() or None
            name = self.query_one("#name", Input).value.strip() or None
            camera = self._camera_spec(str(model), self.query_one("#camera", Switch).value, device)
            spec = VehicleSpec(
                model=str(model),
                name=name,
                **({"pose": pose} if pose else {}),
                params=params,
                outputs=outputs,
                camera=camera,
            )
        except ValueError as e:
            self.notify(str(e).splitlines()[0], severity="error")
            return
        self.dismiss(spec)


    def _camera_spec(self, model: str, on: bool, device: str | None) -> CameraSpec | None:
        m = self.catalog.get(model)
        base = self.spec.camera if self.spec and self.spec.camera else CameraSpec()
        if on and m and m.needs_gazebo:
            direction = self.query_one("#cam-dir", Select).value
            pose = base.pose if direction == "keep" else DIRECTION_POSES.get(str(direction))
            outputs = [o.strip() for o in self.query_one("#cam-outputs", Input).value.split(",") if o.strip()]
            return CameraSpec.model_validate({
                **base.model_dump(exclude={"pose"}),
                "enabled": True,
                "stream": str(self.query_one("#cam-stream", Select).value),
                "outputs": outputs,
                "v4l2_device": device,
                "width": int(self.query_one("#cam-w", Input).value),
                "height": int(self.query_one("#cam-h", Input).value),
                "fps": int(self.query_one("#cam-fps", Input).value),
                "fov": float(self.query_one("#cam-fov", Input).value),
                **({"pose": pose} if pose else {}),
            })
        if m and m.has_camera:
            return base.model_copy(update={"enabled": False})  # switch off the model's own camera
        return None


class PathDialog(_Dialog):
    def __init__(self, title: str, path: str):
        super().__init__()
        self.title_text = title
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Input(self.path, id="path")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        value = self.query_one("#path", Input).value.strip()
        self.dismiss(str(Path(value).expanduser()) if value else None)


class ConfirmDialog(_Dialog):
    def __init__(self, question: str, ok: str | None = None, variant: str = "error"):
        super().__init__()
        self.question = question
        self.ok = ok or t("OK")
        self.variant = variant  # "error" for destructive questions, "primary" otherwise

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog narrow"):
            yield Static(self.question, classes="dialog-title")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.ok, variant=self.variant, id="yes")
                yield Button(t("Cancel"), id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def _answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class DoctorScreen(_Dialog):
    BINDINGS = [Binding("escape,q", "cancel", t("Cancel"))]

    def __init__(self, checks: list[Check]):
        super().__init__()
        self.checks = checks

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(t("Doctor"), classes="dialog-title")
            table = DataTable(cursor_type="row", zebra_stripes=True, id="doctor")
            yield table
            yield Static(id="doctor-hint", classes="hint")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="primary", id="close")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Check", "Result")
        for c in self.checks:
            icon = {"ok": Text("✓", "green"), "warn": Text("!", "yellow"), "fail": Text("✗", "red")}[c.status]
            table.add_row(icon, c.name, c.detail)
        self._show_hint(0)

    @on(DataTable.RowHighlighted)
    def _row(self, event: DataTable.RowHighlighted) -> None:
        self._show_hint(event.cursor_row)

    def _show_hint(self, row: int) -> None:
        if 0 <= row < len(self.checks):
            self.query_one("#doctor-hint", Static).update(self.checks[row].hint or "")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


HELP_TEXT = """\
[b]Hangar[/b]

[b]s[/b] start      [b]x[/b] stop       [b]a[/b] add vehicle   [b]e[/b] edit   [b]d[/b] remove
[b]ctrl+s[/b] save  [b]ctrl+o[/b] open  [b]F2[/b] doctor       [b]ctrl+p[/b] command palette
[b]1-4[/b] tabs     [b]:[/b] command line for the selected vehicle   [b]m[/b] shared map   [b]q[/b] quit

Vehicle commands:
  arm [force] · disarm [force] · mode GUIDED (or just GUIDED) · takeoff 30 · rtl · land
  param set NAME VALUE
  !COMMAND sends a MAVProxy command for the selected vehicle (map open), e.g. !wp list

Every MAVLink output is in the Ports tab. Point QGroundControl or Mission Planner
at udp 14550 (the default) and all vehicles appear with their own system id.
"""


class HelpScreen(_Dialog):
    BINDINGS = [Binding("escape,q,f1,question_mark", "cancel", t("Cancel"))]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide"):
            yield Static(HELP_TEXT)
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="primary", id="close")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


OUTPUT_KINDS = [
    (t("udp — send to a program that listens (QGroundControl, Mission Planner)"), "udp"),
    (t("udpin — listen; a program sends to us first (pymavlink 'udpout:')"), "udpin"),
    (t("tcpin — TCP server; programs connect to us (pymavlink 'tcp:')"), "tcpin"),
]


class OutputDialog(_Dialog):
    """Add a MAVLink output for every vehicle or one of them. Returns (url, scope)."""

    def __init__(self, supervisor, vehicles: list[str]):
        super().__init__()
        self.supervisor = supervisor
        self.vehicles = vehicles

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog compact"):
            yield Label(t("Add output"), classes="dialog-title")
            yield Label(t("For"))
            yield Select([(t("every vehicle"), "*")] + [(n, n) for n in self.vehicles], id="scope",
                         allow_blank=False, value="*", compact=True)
            yield Label(t("Kind"))
            yield Select(OUTPUT_KINDS, id="kind", allow_blank=False, value="udp", compact=True)
            with Horizontal(classes="field-row"):
                yield Label(t("Address"))
                yield Input("127.0.0.1", id="host", compact=True)
                yield Label(t("Port"))
                yield Input("{14560+i}", id="port", compact=True)
            yield Static(t("{i} is the vehicle number (0, 1, 2...), so {14560+i} gives every vehicle its own port."),
                         classes="hint-text")
            yield Static(id="preview")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("Add"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

    def on_mount(self) -> None:
        self._validate()

    def _url(self) -> str:
        kind = self.query_one("#kind", Select).value
        host = self.query_one("#host", Input).value.strip() or "127.0.0.1"
        return f"{kind}:{host}:{self.query_one('#port', Input).value.strip()}"

    def _scope(self) -> str:
        return str(self.query_one("#scope", Select).value)

    @on(Select.Changed)
    @on(Input.Changed)
    def _validate(self) -> bool:
        from ..ports import Endpoint, PlanError, expand_template

        url, scope = self._url(), self._scope()
        targets = [(i, n) for i, n in enumerate(self.vehicles) if scope in ("*", n)]
        lines, ok = [], True
        try:
            eps = [(n, Endpoint.parse(expand_template(url, i, i + 1))) for i, n in targets]
            lines += [f"  {escape(n)} → [b]{escape(str(ep))}[/b]" for n, ep in eps]
            problems = self.supervisor.check_output(url, scope)
            lines += [f"[red]✗ {escape(p)}[/]" for p in problems]
            ok = not problems
            sends = {str(ep) for _, ep in eps if ep.kind == "udp"}
            if len(eps) > 1 and len(sends) == 1:
                shared = t("All these vehicles share one port; fine for QGroundControl and "
                           "Mission Planner, use {i} for scripts.")
                lines.append(f"[yellow]! {escape(shared)}[/]")
        except PlanError as e:
            lines.append(f"[red]✗ {escape(str(e))}[/]")
            ok = False
        self.query_one("#preview", Static).update("\n".join(lines))
        self.query_one("#ok", Button).disabled = not ok
        return ok

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        if self._validate():
            self.dismiss((self._url(), self._scope()))


CAMERA_SNIPPETS = [
    (t("Python (numpy + OpenCV)"), "python"),
    (t("OpenCV with GStreamer"), "opencv-gstreamer"),
    (t("v4l2 device (webcam)"), "v4l2"),
    (t("gst-launch (terminal)"), "gst-launch"),
]


class CameraScreen(_Dialog):
    """How to use a vehicle's camera, with code filled in for its port."""

    def __init__(self, supervisor, name: str):
        super().__init__()
        self.supervisor = supervisor
        self.vehicle = name

    def compose(self) -> ComposeResult:
        info = self.supervisor.camera_info(self.vehicle)
        options = [(label, key) for label, key in CAMERA_SNIPPETS if key in info["usage"]]
        with Vertical(classes="dialog wide camera-dialog"):
            yield Label(f"{t('Camera')}: {self.vehicle}", classes="dialog-title")
            yield Static(id="cam-info")
            with Horizontal(classes="field-row"):
                yield Label(t("Use it from"))
                yield Select(options, id="snippet", allow_blank=False, compact=True,
                             value="v4l2" if info["device"] else "python")
            with VerticalScroll(id="snippet-box"):
                yield Static(id="snippet-code")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("Save as .py"), id="save")
                yield Button(t("Copy"), id="copy")
                yield Button(t("Viewer"), id="viewer", variant="primary")
                yield Button(t("Close"), id="close")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        info = self.supervisor.camera_info(self.vehicle)
        state = "[green]● streaming[/]" if info["streaming"] else "[yellow]● starting[/]"
        dev = f"   v4l2: [b]{escape(info['device'])}[/b]" if info["device"] else ""
        if info["mode"] == "direct":
            where = f"{t('direct to')} [b]{escape(info['targets'][0])}[/b]"
            note = t("Direct mode: the camera sends only to that address. Run the code below on that device.")
        else:
            where = f"udp:[b]{info['port']}[/b]" + "".join(f" → [b]{escape(x)}[/b]" for x in info["targets"])
            note = t("One program at a time can read the UDP port. With a v4l2 device the bridge reads it, "
                     "so read the device instead; many programs can.") if info["device"] else \
                t("One program at a time can read the UDP port. Need more? Set a v4l2 device for this camera.")
            if info["targets"]:
                note += " " + t("The other addresses get the same stream; the code works there with their port.")
        self.query_one("#cam-info", Static).update(
            f"{state}   {where}   {info['width']}×{info['height']} @ {info['fps']} fps   "
            f"H.264 / RTP{dev}\n[dim]{escape(note)}[/]"
        )
        self.query_one("#viewer", Button).disabled = info["mode"] == "direct"
        key = str(self.query_one("#snippet", Select).value)
        lang = "python" if key in ("python", "opencv-gstreamer", "v4l2") else "bash"
        self.query_one("#snippet-code", Static).update(highlight(info["usage"][key], lang))
        self.query_one("#viewer", Button).label = t("Close viewer") if info["viewer"] else t("Viewer")

    def _code(self) -> str:
        return self.supervisor.camera_info(self.vehicle)["usage"][str(self.query_one("#snippet", Select).value)]

    @on(Select.Changed, "#snippet")
    def _snippet(self) -> None:
        self._refresh()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        path = Path.cwd() / f"camera_{self.vehicle}.py"
        try:
            path.write_text(self.supervisor.camera_info(self.vehicle)["usage"]["python"], encoding="utf-8")
        except OSError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"{t('Saved')}: {path}  →  python3 {path.name}", timeout=8)

    @on(Button.Pressed, "#copy")
    def _copy(self) -> None:
        if copy_text(self._code()):
            self.notify(t("Copied to the clipboard"))
        else:
            self.app.copy_to_clipboard(self._code())
            self.notify(t("Sent to the terminal clipboard; if nothing was copied use 'Save as .py'"), timeout=6)

    @on(Button.Pressed, "#viewer")
    async def _viewer(self) -> None:
        try:
            await self.supervisor.toggle_camera_view(self.vehicle)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e), severity="error")
        self._refresh()

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


def highlight(code: str, lang: str) -> Text:
    """Syntax colours without a background, so code blends into any panel."""
    from rich.style import Style
    from rich.syntax import Syntax

    colored = Syntax(code, lang, theme="monokai").highlight(code)
    out = Text(colored.plain)
    for span in colored.spans:
        st = Style.parse(span.style) if isinstance(span.style, str) else span.style
        out.stylize(Style(color=st.color, bold=st.bold, italic=st.italic), span.start, span.end)
    out.rstrip()
    return out


def copy_text(text: str) -> bool:
    """Put text on the desktop clipboard with whatever tool is installed."""
    import shutil
    import subprocess

    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(argv[0]):
            try:
                subprocess.run(argv, input=text.encode(), check=True, timeout=3)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


class CameraOutputDialog(_Dialog):
    """Add an address a camera stream is copied to. Returns (vehicle, target)."""

    def __init__(self, supervisor, vehicles: list[str]):
        super().__init__()
        self.supervisor = supervisor
        self.vehicles = vehicles  # vehicles whose camera streams through Hangar

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog compact"):
            yield Label(t("Send a camera to another device"), classes="dialog-title")
            yield Label(t("Camera of"))
            yield Select([(n, n) for n in self.vehicles], id="cam-vehicle", allow_blank=False,
                         value=self.vehicles[0], compact=True)
            with Horizontal(classes="field-row"):
                yield Label(t("Address"))
                yield Input("", placeholder="10.205.218.120", id="host", compact=True)
                yield Label(t("Port"))
                yield Input("5600", id="port", compact=True)
            yield Static(t("CAMERA_OUTPUT_HELP"), classes="hint-text")
            yield Static(id="preview")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("Add"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

    def on_mount(self) -> None:
        self._validate()
        self.query_one("#host", Input).focus()

    def _target(self) -> str:
        return f"{self.query_one('#host', Input).value.strip()}:{self.query_one('#port', Input).value.strip()}"

    @on(Select.Changed)
    @on(Input.Changed)
    def _validate(self) -> bool:
        from ..ports import Endpoint, PlanError, expand_template

        name = str(self.query_one("#cam-vehicle", Select).value)
        host = self.query_one("#host", Input).value.strip()
        lines: list[str] = []
        ok = bool(host)
        if host:
            index = next(i for i, v in enumerate(self.supervisor.scenario.vehicles) if v.name == name)
            try:
                ep = Endpoint.parse("udp:" + expand_template(self._target(), index, index + 1))
                lines.append(f"  {escape(name)} → [b]{escape(ep.host)}:{ep.port}[/b]")
                problems = self.supervisor.check_camera_output(name, self._target())
                lines += [f"[red]✗ {escape(p)}[/]" for p in problems]
                ok = not problems
            except PlanError as e:
                lines.append(f"[red]✗ {escape(str(e))}[/]")
                ok = False
        self.query_one("#preview", Static).update("\n".join(lines))
        self.query_one("#ok", Button).disabled = not ok
        return ok

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        if self._validate():
            self.dismiss((str(self.query_one("#cam-vehicle", Select).value), self._target()))


class WorldCreateDialog(_Dialog):
    """A world from a real place: centre, size and name. Returns a dict."""

    SIZES = [("1 km × 1 km", 1000), ("2 km × 2 km", 2000), ("3 km × 3 km", 3000), ("5 km × 5 km", 5000),
             ("10 km × 10 km", 10000)]

    def __init__(self, existing: set[str]):
        super().__init__()
        self.existing = existing

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog compact"):
            yield Label(t("Create a world from a real place"), classes="dialog-title")
            with Horizontal(classes="field-row"):
                yield Label(t("Latitude"))
                yield Input(placeholder="39.88", id="lat", type="number", compact=True)
                yield Label(t("Longitude"))
                yield Input(placeholder="33.23", id="lon", type="number", compact=True)
            with Horizontal(classes="field-row"):
                yield Label(t("Size"))
                yield Select(self.SIZES, id="size", allow_blank=False, value=3000, compact=True)
            yield Label(t("Name"))
            yield Input(placeholder="ankara_hills", id="name")
            with Horizontal(classes="switch-row"):
                yield Switch(True, id="buildings")
                yield Label(t("OpenStreetMap buildings"))
            with Horizontal(classes="switch-row"):
                yield Switch(True, id="solid")
                yield Label(t("Buildings are solid (off: visual only, faster)"))
            yield Label(t("Your own elevation GeoTIFF (optional, sharper terrain)"))
            yield Input(placeholder="~/survey/dsm.tif", id="dem")
            yield Label(t("Your own orthophoto GeoTIFF (optional, sharper ground)"))
            yield Input(placeholder="~/survey/orthophoto.tif", id="imagery")
            yield Static(t("WORLD_CREATE_HELP"), classes="hint-text")
            yield Static(id="world-problem")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("Create"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

    def on_mount(self) -> None:
        self._check()
        self.query_one("#lat", Input).focus()

    def _values(self) -> dict | None:
        try:
            lat = float(self.query_one("#lat", Input).value)
            lon = float(self.query_one("#lon", Input).value)
        except ValueError:
            return None
        default = f"place_{lat:.3f}_{lon:.3f}".replace(".", "_").replace("-", "m")
        name = self.query_one("#name", Input).value.strip() or default
        values = {"lat": lat, "lon": lon, "size": int(self.query_one("#size", Select).value), "name": name,
                  "buildings": self.query_one("#buildings", Switch).value,
                  "solid": self.query_one("#solid", Switch).value}
        for key in ("dem", "imagery"):
            path = self.query_one(f"#{key}", Input).value.strip()
            if path:
                values[key] = str(Path(path).expanduser())
        return values

    @on(Input.Changed)
    @on(Select.Changed)
    def _check(self) -> bool:
        import re

        v = self._values()
        problem = ""
        if v is None:
            problem = t("Enter a latitude and a longitude (decimal degrees, e.g. 39.88 and 33.23).")
        elif not -85 < v["lat"] < 85 or not -180 <= v["lon"] <= 180:
            problem = t("Latitude must be between -85 and 85, longitude between -180 and 180.")
        elif not re.fullmatch(r"[A-Za-z0-9_-]+", v["name"]):
            problem = t("The name may only contain letters, digits, '_' and '-'.")
        elif v["name"] in self.existing:
            problem = t("A world with this name already exists.")
        elif any(k in v and not Path(v[k]).is_file() for k in ("dem", "imagery")):
            problem = t("A GeoTIFF file you gave does not exist.")
        self.query_one("#world-problem", Static).update(f"[yellow]{escape(problem)}[/]" if problem else "")
        self.query_one("#ok", Button).disabled = bool(problem)
        return not problem

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        if self._check():
            self.dismiss(self._values())
