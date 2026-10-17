"""Filesystem locations, following the XDG base directory spec."""

from __future__ import annotations

import os
from pathlib import Path

APP = "hangar"


def _xdg(var: str, fallback: str) -> Path:
    base = os.environ.get(var)
    return (Path(base) if base else Path.home() / fallback) / APP


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def config_file() -> Path:
    override = os.environ.get("HANGAR_CONFIG")
    return Path(override) if override else config_dir() / "config.toml"


def sources_dir() -> Path:
    """Where third-party repositories fetched with the user's consent live."""
    return data_dir() / "sources"


def runs_dir() -> Path:
    """Per-run working directories (eeprom, logs, generated worlds)."""
    return state_dir() / "runs"


def package_data() -> Path:
    return Path(__file__).parent / "data"
