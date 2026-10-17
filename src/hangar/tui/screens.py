"""Modal dialogs of the TUI."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from ..catalog import Catalog
from ..doctor import Check
from ..i18n import t
from ..scenario import CameraSpec, VehicleSpec

TYPE_ICONS = {"copter": "✣", "plane": "✈", "rover": "⛟", "boat": "⛴"}


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
        pose = s.pose if s else [0, 0, 0.3, 0, 0, 0]
        pose_text = " ".join(f"{v:g}" for v in (pose[0], pose[1], pose[2], pose[5]))
        with Vertical(classes="dialog"):
            yield Label(t("Edit vehicle") if s else t("Add vehicle"), classes="dialog-title")
            yield Label(t("Model"))
            yield Select(options, prompt=t("Pick a model"), id="model",
                         value=s.model if s and s.model in self.catalog.vehicles else Select.NULL)
            yield Label(t("Name"))
            yield Input(s.name if s else "", placeholder="auto", id="name")
            yield Label(t("Pose x y z yaw (m, deg)"))
            yield Input(pose_text, id="pose")
            yield Label(t("Extra outputs (comma separated)"))
            yield Input(", ".join(s.outputs) if s else "", placeholder="udp:127.0.0.1:{15000+i}", id="outputs")
            yield Label(t("Parameters NAME=VALUE (comma separated)"))
            yield Input(", ".join(f"{k}={v:g}" for k, v in (s.params.items() if s else [])),
                        placeholder="SIM_WIND_SPD=5", id="params")
            yield Label(t("Camera to v4l2 device (optional)"))
            yield Input(s.camera.v4l2_device or "" if s and s.camera else "", placeholder="/dev/video2", id="v4l2")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="primary", id="ok")
                yield Button(t("Cancel"), id="cancel")

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
            outputs = [o.strip() for o in self.query_one("#outputs", Input).value.split(",") if o.strip()]
            device = self.query_one("#v4l2", Input).value.strip()
            name = self.query_one("#name", Input).value.strip() or None
            spec = VehicleSpec(
                model=str(model),
                name=name,
                pose=pose or [0, 0, 0.3],
                params=params,
                outputs=outputs,
                camera=CameraSpec(v4l2_device=device) if device else None,
            )
        except ValueError as e:
            self.notify(str(e).splitlines()[0], severity="error")
            return
        self.dismiss(spec)


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
    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog narrow"):
            yield Static(self.question, classes="dialog-title")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("OK"), variant="error", id="yes")
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
[b]1-4[/b] tabs     [b]:[/b] command line for the selected vehicle   [b]q[/b] quit

Vehicle commands:
  arm [force] · disarm [force] · mode GUIDED (or just GUIDED) · takeoff 30 · rtl · land
  param set NAME VALUE

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
