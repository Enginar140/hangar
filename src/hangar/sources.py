"""Third-party sources, fetched only with the user's consent.

Hangar's repository contains only Hangar. ArduPilot, its Gazebo plugin and
the example models are cloned on the user's machine from their upstream
repositories, after their licenses have been shown, so Hangar never
redistributes anyone else's code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths


@dataclass
class Source:
    id: str
    title: str
    url: str
    ref: str
    license: str
    purpose: str
    required: bool = False
    submodules: bool = False
    dir: str = ""
    build: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return paths.sources_dir() / (self.dir or self.id)

    @property
    def fetched(self) -> bool:
        return (self.path / ".git").exists() or (self.path / ".hangar-fetched").exists()


def load_manifest() -> dict[str, Source]:
    with (paths.package_data() / "sources.yaml").open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {k: Source(id=k, **v) for k, v in raw.items()}


def _lock_path() -> Path:
    return paths.sources_dir() / "lock.json"


def read_lock() -> dict[str, dict]:
    try:
        return json.loads(_lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_lock(source: Source, commit: str) -> None:
    lock = read_lock()
    lock[source.id] = {"url": source.url, "ref": source.ref, "commit": commit, "license": source.license}
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    _lock_path().write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _git(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def fetch(source: Source) -> str:
    """Clone (or update) ``source`` at its pinned ref. Returns the commit."""
    if not shutil.which("git"):
        raise RuntimeError("git is required: sudo apt install git")
    dest = source.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        if dest.exists():
            raise RuntimeError(f"{dest} exists and is not a git checkout; move it away first")
        _git(["init", "-q", str(dest)])
        _git(["remote", "add", "origin", source.url], cwd=dest)
    # Fetching a single ref works for tags, branches and (on GitHub) commits.
    _git(["fetch", "--depth", "1", "origin", source.ref], cwd=dest)
    _git(["checkout", "-q", "--force", "FETCH_HEAD"], cwd=dest)
    if source.submodules:
        _git(["submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "8"], cwd=dest)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write_lock(source, commit)
    return commit


def build_steps(source: Source) -> list[tuple[str, list[str] | str]]:
    """(description, command) pairs; a str command runs through the shell."""
    b = source.build or {}
    steps: list[tuple[str, list[str] | str]] = []
    if b.get("apt"):
        steps.append(("system packages", ["sudo", "apt-get", "install", "-y", *b["apt"]]))
    if b.get("prereqs"):
        steps.append(("ArduPilot prerequisites (uses sudo)", b["prereqs"]))
    for cmd in b.get("commands", []):
        steps.append((cmd, cmd))
    return steps


def run_step(source: Source, command: list[str] | str) -> None:
    if isinstance(command, str):
        subprocess.run(["bash", "-lc", command], cwd=source.path, check=True)
    else:
        subprocess.run(command, cwd=source.path, check=True)
