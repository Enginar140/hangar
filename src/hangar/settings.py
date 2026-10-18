"""User settings stored in ~/.config/hangar/config.toml.

Every path is optional. Anything left empty is discovered from the sources
directory that ``hangar sources fetch`` populates, so a fresh install works
without editing this file, and an existing setup can be pointed to with
``hangar config set ardupilot.dir /path/to/ardupilot``.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

LIST_KEYS = {
    "gazebo.plugin_paths",
    "gazebo.resource_paths",
    "catalog.model_paths",
    "catalog.world_paths",
}
PREBUILT_DIR = "ardupilot-sitl"  # `hangar sources fetch ardupilot_sitl` lands here
SCALAR_KEYS = {
    "language",
    "ardupilot.dir",
    "ardupilot.bin_dir",
    "gazebo.render_engine",
}


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def _existing(candidates: list[Path]) -> list[Path]:
    seen: list[Path] = []
    for c in candidates:
        if c.is_dir() and c not in seen:
            seen.append(c)
    return seen


@dataclass
class Settings:
    language: str = "auto"
    ardupilot_dir: str = ""
    ardupilot_bin_dir: str = ""
    render_engine: str = "ogre2"
    plugin_paths: list[str] = field(default_factory=list)
    resource_paths: list[str] = field(default_factory=list)
    model_paths: list[str] = field(default_factory=list)
    world_paths: list[str] = field(default_factory=list)

    # --- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or paths.config_file()
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            raw = tomllib.load(f)
        ap = raw.get("ardupilot", {})
        gz = raw.get("gazebo", {})
        cat = raw.get("catalog", {})
        return cls(
            language=raw.get("language", "auto"),
            ardupilot_dir=ap.get("dir", ""),
            ardupilot_bin_dir=ap.get("bin_dir", ""),
            render_engine=gz.get("render_engine", "ogre2"),
            plugin_paths=list(gz.get("plugin_paths", [])),
            resource_paths=list(gz.get("resource_paths", [])),
            model_paths=list(cat.get("model_paths", [])),
            world_paths=list(cat.get("world_paths", [])),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or paths.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        q = _toml_str
        ql = lambda xs: "[" + ", ".join(q(x) for x in xs) + "]"  # noqa: E731
        text = (
            "# Hangar settings. Empty values are auto-discovered.\n"
            f"language = {q(self.language)}\n\n"
            "[ardupilot]\n"
            f"dir = {q(self.ardupilot_dir)}\n"
            f"bin_dir = {q(self.ardupilot_bin_dir)}\n\n"
            "[gazebo]\n"
            f"render_engine = {q(self.render_engine)}\n"
            f"plugin_paths = {ql(self.plugin_paths)}\n"
            f"resource_paths = {ql(self.resource_paths)}\n\n"
            "[catalog]\n"
            f"model_paths = {ql(self.model_paths)}\n"
            f"world_paths = {ql(self.world_paths)}\n"
        )
        path.write_text(text, encoding="utf-8")
        return path

    # --- dotted-key access used by `hangar config` -------------------------

    _ATTR = {
        "language": "language",
        "ardupilot.dir": "ardupilot_dir",
        "ardupilot.bin_dir": "ardupilot_bin_dir",
        "gazebo.render_engine": "render_engine",
        "gazebo.plugin_paths": "plugin_paths",
        "gazebo.resource_paths": "resource_paths",
        "catalog.model_paths": "model_paths",
        "catalog.world_paths": "world_paths",
    }

    def set_key(self, key: str, value: str) -> None:
        if key in SCALAR_KEYS:
            setattr(self, self._ATTR[key], value)
        elif key in LIST_KEYS:
            setattr(self, self._ATTR[key], [v for v in value.split(":") if v])
        else:
            raise KeyError(key)

    def add_key(self, key: str, value: str) -> None:
        if key not in LIST_KEYS:
            raise KeyError(key)
        items: list[str] = getattr(self, self._ATTR[key])
        if value not in items:
            items.append(value)

    def as_dict(self) -> dict[str, object]:
        return {k: getattr(self, a) for k, a in self._ATTR.items()}

    # --- resolved locations ----------------------------------------------

    def resolved_ardupilot_dir(self) -> Path | None:
        if self.ardupilot_dir:
            return _expand(self.ardupilot_dir)
        auto = paths.sources_dir() / "ardupilot"
        return auto if auto.is_dir() else None

    def resolved_bin_dir(self) -> Path | None:
        """Explicit bin_dir, else a built ArduPilot tree, else the prebuilt download."""
        if self.ardupilot_bin_dir:
            return _expand(self.ardupilot_bin_dir)
        ap = self.resolved_ardupilot_dir()
        built = ap / "build" / "sitl" / "bin" if ap else None
        if built and built.is_dir():
            return built
        prebuilt = paths.sources_dir() / PREBUILT_DIR / "bin"
        if prebuilt.is_dir():
            return prebuilt
        return built

    def autotest_dir(self) -> Path | None:
        """ArduPilot's Tools/autotest (default_params/, models/), from the source
        tree or the files downloaded with the prebuilt binaries."""
        ap = self.resolved_ardupilot_dir()
        for d in (ap / "Tools" / "autotest" if ap else None,
                  paths.sources_dir() / PREBUILT_DIR / "autotest"):
            if d and d.is_dir():
                return d
        return None

    def mavproxy(self) -> Path | None:
        """mavproxy.py from `hangar sources fetch mavproxy`, else from PATH."""
        own = paths.sources_dir() / "tools" / "mavproxy"
        if (own / ".hangar-fetched").exists() and (own / "bin" / "mavproxy.py").exists():
            return own / "bin" / "mavproxy.py"
        found = shutil.which("mavproxy.py")
        return Path(found) if found else None

    def resolved_model_paths(self) -> list[Path]:
        src = paths.sources_dir()
        return _existing(
            [_expand(p) for p in self.model_paths]
            + [src / "SITL_Models" / "Gazebo" / "models", src / "ardupilot_gazebo" / "models"]
        )

    def resolved_world_paths(self) -> list[Path]:
        src = paths.sources_dir()
        generated = paths.data_dir() / "worlds"  # `hangar world create`, one folder per world
        made = sorted(p for p in generated.iterdir() if p.is_dir()) if generated.is_dir() else []
        return _existing(
            [_expand(p) for p in self.world_paths]
            + [src / "SITL_Models" / "Gazebo" / "worlds", src / "ardupilot_gazebo" / "worlds"]
            + made
        )

    def hangar_earth(self) -> Path | None:
        """The hangar-earth generator from `hangar sources fetch hangar_earth`, else PATH."""
        own = paths.sources_dir() / "tools" / "hangar-earth"
        if (own / ".hangar-fetched").exists() and (own / "bin" / "hangar-earth").exists():
            return own / "bin" / "hangar-earth"
        found = shutil.which("hangar-earth")
        return Path(found) if found else None

    def resolved_plugin_paths(self) -> list[Path]:
        return _existing(
            [_expand(p) for p in self.plugin_paths]
            + [paths.sources_dir() / "ardupilot_gazebo" / "build"]
        )

    def resolved_resource_paths(self) -> list[Path]:
        return _existing([_expand(p) for p in self.resource_paths])

    def param_dirs(self) -> list[Path]:
        """Directories that may hold <model>.param files (SITL_Models layout)."""
        cands: list[Path] = []
        for mp in self.resolved_model_paths():
            cands += [mp.parent / "config", mp / "config"]
        return _existing(cands)


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

