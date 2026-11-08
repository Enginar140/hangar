"""The runs that are going on right now, from any Hangar process (TUI, `hangar up`, Hangar Cloud).

Each running simulation keeps a small JSON file in ``<state dir>/active/``, removed when it stops.
A file whose process is gone (a crash, a kill -9) is ignored and deleted by the next reader.
The same information is kept as ``<run dir>/state.json``, which stays after the run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths

FORMAT = 1


def active_dir() -> Path:
    return paths.state_dir() / "active"


def process_start(pid: int) -> int | None:
    """When the process started (clock ticks since boot), so a reused pid is not mistaken for it."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        return int(stat.rpartition(")")[2].split()[19])
    except (IndexError, ValueError):
        return None


def _alive(entry: dict) -> bool:
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # someone else's process, but it exists
    started = process_start(pid)
    return started is None or entry.get("proc_start") in (None, started)


def active_runs(include_own: bool = True) -> list[dict]:
    """Every run going on now, oldest first. Stale entries are removed."""
    out = []
    folder = active_dir()
    if not folder.is_dir():
        return out
    for f in folder.glob("*.json"):
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _alive(entry):
            f.unlink(missing_ok=True)
            continue
        if not include_own and entry.get("pid") == os.getpid():
            continue
        out.append(entry)
    return sorted(out, key=lambda e: e.get("started", 0))


def describe(entry: dict) -> str:
    since = time.strftime("%H:%M", time.localtime(entry.get("started", 0)))
    owner = f", {entry['owner']}" if entry.get("owner") else ""
    ports = [v["mavlink"] for v in entry.get("vehicles", [])]
    first = f", MAVLink {ports[0]}" if ports else ""
    return f"'{entry.get('scenario')}' (pid {entry.get('pid')}{owner}, since {since}{first})"


class Registration:
    """One run's entry: written at start, updated as it goes, removed at the end."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = active_dir() / f"{os.getpid()}-{run_dir.name}.json"
        self.data: dict = {}

    def write(self, data: dict) -> None:
        self.data = {"format": FORMAT, "pid": os.getpid(), "proc_start": process_start(os.getpid()),
                     "run_dir": str(self.run_dir), "run": self.run_dir.name, **data}
        self._dump(self.run_dir / "state.json")
        self._dump(self.path)

    def update(self, **changes) -> None:
        if self.data:
            self.write({**self.data, **changes})

    def close(self, state: str) -> None:
        self.path.unlink(missing_ok=True)
        if self.data:
            self.data.update(state=state, ended=time.time())
            self._dump(self.run_dir / "state.json")

    def _dump(self, f: Path) -> None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f".{f.name}.tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass
