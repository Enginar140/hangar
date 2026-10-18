"""What makes a simulation slow, found before it starts.

ArduPilot only becomes ready after some seconds of *simulated* time, so a
Gazebo that runs at 5% of real time turns a 20 s warm-up into minutes. The
usual causes: rendering on an integrated GPU, big or fast cameras (each frame
is rendered and H.264-encoded on the CPU) and very heavy worlds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
HD = 1280 * 720 * 30  # pixels per second of a 720p camera at 30 fps


def gpu_vendors(drm: Path = Path("/sys/class/drm")) -> list[str]:
    found = []
    for vendor in sorted(drm.glob("card*/device/vendor")):
        try:
            name = VENDORS.get(vendor.read_text().strip(), "other")
        except OSError:
            continue
        if name not in found:
            found.append(name)
    return found


def integrated_gpu_only(drm: Path = Path("/sys/class/drm")) -> bool:
    vendors = gpu_vendors(drm)
    return bool(vendors) and set(vendors) == {"Intel"}


@dataclass
class Camera:
    vehicle: str
    width: int
    height: int
    fps: int

    @property
    def load(self) -> float:
        return self.width * self.height * self.fps / HD  # in 720p30 units


def world_weight(world_sdf: Path) -> tuple[int, int]:
    """(buildings, triangles) of a hangar-earth world that vehicles collide with, else (0, 0)."""
    meta = world_sdf.parent / "hangar-earth.json"
    try:
        b = json.loads(meta.read_text(encoding="utf-8")).get("buildings") or {}
    except (OSError, ValueError):
        return 0, 0
    if not b.get("collision", True):  # visual only: costs rendering, not physics
        return 0, 0
    return int(b.get("count", 0)), int(b.get("triangles", 0))


def warnings(cameras: list[Camera], world_sdf: Path | None, headless: bool,
             integrated: bool | None = None) -> list[str]:
    integrated = integrated_gpu_only() if integrated is None else integrated
    out: list[str] = []
    if integrated:
        out.append("only an integrated GPU (Intel) was found: Gazebo will render slowly"
                   + ("" if headless else "; 'headless: true' (no Gazebo window) helps a lot"))
    limit = 1.0 if integrated else 2.0
    for c in cameras:
        if c.load > limit:
            out.append(f"camera of {c.vehicle}: {c.width}x{c.height} @ {c.fps} fps is heavy "
                       f"(rendered and H.264-encoded every frame); 1280x720 @ 30 fps is usually enough")
    total = sum(c.load for c in cameras)
    if len(cameras) > 1 and total > 2 * limit:
        out.append(f"{len(cameras)} cameras render {total * HD / 1e6:.0f} Mpixel/s together; "
                   "switch off the ones you do not use")
    if world_sdf is not None:
        buildings, triangles = world_weight(world_sdf)
        if buildings > (2000 if integrated else 5000):
            out.append(f"the world has {buildings} buildings ({triangles} triangles) that vehicles collide with; "
                       "a smaller area, or a world made with --no-building-collision or --no-buildings, runs faster")
    return out
