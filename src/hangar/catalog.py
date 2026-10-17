"""Discovers launchable vehicles and Gazebo worlds."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from . import paths
from .settings import Settings

VehicleType = Literal["copter", "plane", "rover", "boat"]
VEHICLE_TYPES = ("copter", "plane", "rover", "boat")

BINARIES: dict[str, str] = {
    "copter": "arducopter",
    "plane": "arduplane",
    "rover": "ardurover",
    "boat": "ardurover",
}

_NAME_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("copter", ("copter", "quad", "hexa", "octo", "iris")),
    ("plane", ("plane", "wing", "aircraft", "zephyr", "talon", "skywalker")),
    ("boat", ("boat", "catamaran", "ship")),
    ("rover", ("rover", "truck", "car", "tractor")),
]


@dataclass
class VehicleModel:
    name: str
    type: str
    kind: Literal["builtin", "gazebo"]
    sitl_model: str = "JSON"
    defaults: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    model_dir: Path | None = None
    has_camera: bool = False
    description: str = ""

    @property
    def binary(self) -> str:
        return BINARIES[self.type]

    @property
    def needs_gazebo(self) -> bool:
        return self.kind == "gazebo"


@dataclass
class World:
    name: str
    path: Path


@dataclass
class ParamResolution:
    files: list[Path]
    missing: list[str]


def _load_metadata() -> dict:
    with (paths.package_data() / "vehicles.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _guess_type(name: str) -> str | None:
    lower = name.lower()
    for vtype, words in _NAME_HINTS:
        if any(w in lower for w in words):
            return vtype
    return None


class Catalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.vehicles: dict[str, VehicleModel] = {}
        self.worlds: dict[str, World] = {}
        self.warnings: list[str] = []
        self._meta = _load_metadata()
        self.scan()

    def scan(self) -> None:
        self.vehicles.clear()
        self.worlds.clear()
        self.warnings.clear()
        for name, m in self._meta.get("builtin", {}).items():
            self.vehicles[name] = VehicleModel(
                name=name,
                type=m["type"],
                kind="builtin",
                sitl_model=m["sitl_model"],
                defaults=list(m.get("defaults", [])),
                description=m.get("description", ""),
            )
        for root in self.settings.resolved_model_paths():
            for model_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                if model_dir.name in self.vehicles:
                    continue
                model = self._load_gazebo_model(model_dir)
                if model:
                    self.vehicles[model.name] = model
        for root in self.settings.resolved_world_paths():
            for sdf in sorted(root.glob("*.sdf")):
                self.worlds.setdefault(sdf.stem, World(sdf.stem, sdf))

    def _load_gazebo_model(self, model_dir: Path) -> VehicleModel | None:
        sdf = model_dir / "model.sdf"
        if not sdf.is_file():
            return None
        try:
            text = sdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if "ArduPilotPlugin" not in text:
            return None  # scenery, not a vehicle

        meta = dict(self._meta.get("gazebo", {}).get(model_dir.name, {}))
        override = model_dir / "hangar.yaml"
        if override.is_file():
            meta.update(yaml.safe_load(override.read_text(encoding="utf-8")) or {})
        if meta.get("skip"):
            return None

        vtype = meta.get("type") or _guess_type(model_dir.name)
        if vtype not in VEHICLE_TYPES:
            self.warnings.append(
                f"{model_dir.name}: vehicle type unknown, add a hangar.yaml with 'type:'"
            )
            return None

        defaults = meta.get("defaults") or self._meta["type_defaults"][vtype]
        params = list(meta.get("params", []))
        own = f"{model_dir.name}.param"
        if own not in params and self._find_param(own, model_dir):
            params.append(own)

        return VehicleModel(
            name=model_dir.name,
            type=vtype,
            kind="gazebo",
            defaults=list(defaults),
            params=params,
            model_dir=model_dir,
            has_camera="GstCameraPlugin" in text,
            description=meta.get("description", ""),
        )

    # --- parameter files -------------------------------------------------

    def _param_search_dirs(self, model_dir: Path | None) -> list[Path]:
        dirs: list[Path] = []
        if model_dir:
            dirs += [model_dir, model_dir.parent.parent / "config", model_dir.parent / "config"]
        dirs += self.settings.param_dirs()
        for plugin_root in self.settings.resolved_plugin_paths():
            dirs.append(plugin_root.parent / "config")  # ardupilot_gazebo/config
        dp = self.settings.default_params_dir()
        if dp:
            dirs.append(dp)
        return [d for d in dirs if d.is_dir()]

    def _find_param(self, name: str, model_dir: Path | None) -> Path | None:
        p = Path(name).expanduser()
        if p.is_absolute():
            return p if p.is_file() else None
        for d in self._param_search_dirs(model_dir):
            if (d / p).is_file():
                return d / p
        return None

    def resolve_params(self, model: VehicleModel) -> ParamResolution:
        """Base defaults first, then model-specific files, so the latter win."""
        files: list[Path] = []
        missing: list[str] = []
        dp = self.settings.default_params_dir()
        for name in model.defaults:
            f = dp / name if dp else None
            if f and f.is_file():
                files.append(f)
            else:
                missing.append(name)
        for name in model.params:
            f = self._find_param(name, model.model_dir)
            if f:
                files.append(f)
            else:
                missing.append(name)
        return ParamResolution(files, missing)

    # --- queries ---------------------------------------------------------

    def get(self, name: str) -> VehicleModel | None:
        return self.vehicles.get(name)

    def binary_path(self, model: VehicleModel) -> Path | None:
        bin_dir = self.settings.resolved_bin_dir()
        return bin_dir / model.binary if bin_dir else None
