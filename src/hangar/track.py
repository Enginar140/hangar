"""What each vehicle did, kept with the run so it can be played back.

While a run goes on, ``<run>/track/<vehicle>.csv`` gets the vehicle's position and attitude a few
times a second, and ``<run>/events.jsonl`` its messages, mode changes and arming. ``build()``
turns that into a replay file (the format Hangar Cloud plays back, version 1).

Times are seconds since the run started, on the wall clock: the same for every vehicle.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import time
from pathlib import Path

FORMAT = 1
COLUMNS = ["t", "lat", "lon", "alt", "roll", "pitch", "yaw", "speed", "mode", "armed"]
EARTH = 6378137.0


class TrackRecorder:
    """Samples the vehicles' telemetry; call ``sample()`` a few times a second."""

    def __init__(self, run_dir: Path, started: float):
        self.dir = run_dir / "track"
        self.started = started
        self._files: dict[str, tuple] = {}
        self._last: dict[str, tuple] = {}
        run_dir.mkdir(parents=True, exist_ok=True)
        self._events = (run_dir / "events.jsonl").open("a", encoding="utf-8", buffering=1)
        self._flushed = time.monotonic()

    def now(self) -> float:
        return round(time.time() - self.started, 2)

    def event(self, kind: str, who: str, text: str = "", **extra) -> None:
        if self._events.closed:
            return
        self._events.write(json.dumps({"t": self.now(), "kind": kind, "who": who, "text": text, **extra},
                                      ensure_ascii=False) + "\n")

    def sample(self, name: str, tel) -> None:
        if tel.lat is None or tel.lon is None or (tel.lat == 0 and tel.lon == 0):
            return
        state = (tel.mode, tel.armed)
        last = self._last.get(name)
        if last != state:
            if last is None or last[0] != tel.mode:
                self.event("mode", name, tel.mode)
            if last is not None and last[1] != tel.armed:
                self.event("arm" if tel.armed else "disarm", name)
            self._last[name] = state
        if name not in self._files:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = (self.dir / f"{name}.csv").open("w", encoding="utf-8", newline="")
            w = csv.writer(f)
            w.writerow(COLUMNS)
            self._files[name] = (f, w)
        _, w = self._files[name]
        r = lambda x, n=2: None if x is None else round(x, n)  # noqa: E731
        yaw = tel.yaw if tel.yaw is not None else tel.heading
        w.writerow([self.now(), round(tel.lat, 7), round(tel.lon, 7), r(tel.alt_rel), r(tel.roll), r(tel.pitch),
                    r(yaw % 360 if yaw is not None else None), r(tel.groundspeed), tel.mode, int(tel.armed)])
        if time.monotonic() - self._flushed > 2:
            self.flush()

    def flush(self) -> None:
        for f, _ in self._files.values():
            f.flush()
        self._flushed = time.monotonic()

    def close(self) -> None:
        for f, _ in self._files.values():
            f.close()
        self._files.clear()
        self._events.close()


# --- replay files ------------------------------------------------------------


def has_track(run_dir: Path) -> bool:
    return any((run_dir / "track").glob("*.csv"))


def _num(x: str):
    return None if x in ("", "None") else float(x)


def build(run_dir: Path) -> dict:
    """The run as a replay (version 1): tracks in metres north/east of the origin, height up."""
    state = {}
    try:
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    models = {v["name"]: v.get("model") for v in state.get("vehicles", [])}
    rows: dict[str, list[dict]] = {}
    for f in sorted((run_dir / "track").glob("*.csv")):
        with f.open(encoding="utf-8", newline="") as fh:
            rows[f.stem] = list(csv.DictReader(fh))
    first = next((r[0] for r in rows.values() if r), None)
    if first is None:
        raise ValueError(f"{run_dir.name} has no recorded track")
    lat0, lon0 = float(first["lat"]), float(first["lon"])
    origin = state.get("home") or {"lat": lat0, "lon": lon0, "alt": 0.0}
    lat0, lon0 = origin["lat"], origin["lon"]
    k = math.cos(math.radians(lat0))

    tracks = {}
    for name, rs in rows.items():
        col = {c: [] for c in ("t", "n", "e", "h", "yaw", "roll", "pitch")}
        mode, armed, speed = [], [], []
        for r in rs:
            lat, lon = float(r["lat"]), float(r["lon"])
            col["t"].append(float(r["t"]))
            col["n"].append(round(math.radians(lat - lat0) * EARTH, 2))
            col["e"].append(round(math.radians(lon - lon0) * EARTH * k, 2))
            col["h"].append(_num(r["alt"]))
            for c in ("yaw", "roll", "pitch"):
                col[c].append(_num(r[c]))
            mode.append(r["mode"])
            armed.append("armed" if r["armed"] == "1" else "")
            speed.append(_num(r["speed"]))
        tracks[name] = {"team": None, "model": models.get(name), **col,
                        "fields": {"mode": mode, "armed": armed, "speed": speed}}

    events = []
    try:
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            events.append({"t": e.get("t", 0), "kind": e.get("kind", "text"), "who": e.get("who", ""),
                           "target": e.get("target", ""), "text": e.get("text", ""), "video": e.get("video")})
    except OSError:
        pass
    return {"version": FORMAT, "name": run_dir.name, "origin": origin, "world": state.get("world"),
            "tracks": tracks, "events": sorted(events, key=lambda e: e["t"]), "zones": []}


def write(data: dict, path: Path) -> Path:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(text.encode("utf-8")))
    else:
        path.write_text(text, encoding="utf-8")
    return path
