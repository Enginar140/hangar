"""Scenario files: the single description of what to simulate.

Example::

    name: two-planes
    world: firtinam_runway
    outputs:
      - udp:127.0.0.1:14550          # QGroundControl / Mission Planner
    vehicles:
      - model: firtinam
        pose: [0, 0, 0.3, 0, 0, 90]
      - model: skywalker_x8
        pose: [0, 10, 0.3, 0, 0, 90]
        outputs: ["udp:127.0.0.1:{15000+i}"]
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
EARTH_RADIUS = 6378137.0


class Home(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = -35.363261
    lon: float = 149.165230
    alt: float = 584.0
    heading: float = 353.0

    def offset(self, north_m: float, east_m: float) -> Home:
        dlat = math.degrees(north_m / EARTH_RADIUS)
        dlon = math.degrees(east_m / (EARTH_RADIUS * math.cos(math.radians(self.lat))))
        return self.model_copy(update={"lat": self.lat + dlat, "lon": self.lon + dlon})

    def arg(self) -> str:
        return f"{self.lat:.7f},{self.lon:.7f},{self.alt:g},{self.heading:g}"


def _six(v: list[float]) -> list[float]:
    if len(v) == 3:
        return [*v, 0.0, 0.0, 0.0]
    if len(v) == 4:  # x y z yaw
        return [v[0], v[1], v[2], 0.0, 0.0, v[3]]
    if len(v) != 6:
        raise ValueError("pose must be [x, y, z], [x, y, z, yaw] or [x, y, z, roll, pitch, yaw]")
    return v


class CameraSpec(BaseModel):
    """A vehicle camera. Models without one get it added to their run copy."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    v4l2_device: str | None = Field(
        default=None, description="e.g. /dev/video2; frames become readable by OpenCV"
    )
    pose: list[float] | None = Field(
        default=None, description="x y z roll pitch yaw on the body (m, deg); pitch 90 looks down"
    )
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=720, gt=0)
    fov: float = Field(default=60.0, gt=0, lt=180, description="horizontal field of view, degrees")
    fps: int = Field(default=30, gt=0)
    stream: Literal["relay", "direct"] = Field(
        default="relay",
        description="relay: Hangar copies the stream to this computer and every output; "
                    "direct: the camera sends straight to its one output, this computer gets nothing",
    )
    outputs: list[str] = Field(
        default_factory=list,
        description="where else the stream goes, host:port; {i} = vehicle index, e.g. 10.0.0.5:{5600+i}",
    )

    @field_validator("pose")
    @classmethod
    def _pose(cls, v: list[float] | None) -> list[float] | None:
        return None if v is None else _six(v)

    @model_validator(mode="after")
    def _direct_has_one_target(self) -> CameraSpec:
        if self.stream == "direct" and len(self.outputs) != 1:
            raise ValueError("camera stream 'direct' sends to exactly one output; set outputs: [host:port]")
        return self


class VehicleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    name: str | None = None
    pose: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.3, 0.0, 0.0, 0.0])
    params: dict[str, float] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    camera: CameraSpec | None = None

    @field_validator("camera", mode="before")
    @classmethod
    def _camera_shorthand(cls, v):
        if v is True:  # `camera: true`
            return {}
        if v is False:
            return {"enabled": False}
        return v

    @field_validator("pose")
    @classmethod
    def _pose(cls, v: list[float]) -> list[float]:
        return _six(v)

    @property
    def pose_sdf(self) -> str:
        return " ".join(f"{x:g}" for x in self.pose)

    @property
    def placed(self) -> bool:
        """True when the scenario gives this vehicle a pose; otherwise Hangar picks one."""
        return "pose" in self.model_fields_set


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "hangar"
    world: str | None = None
    headless: bool = False
    speedup: float = Field(default=1.0, gt=0)
    # SITL instance of the first vehicle; each instance moves its ports by 10. Raise it to run
    # next to another SITL (which uses instance 0 and tcp:5760 unless told otherwise).
    first_instance: int = Field(default=0, ge=0, le=100)
    home: Home = Field(default_factory=Home)
    outputs: list[str] = Field(default_factory=lambda: ["udp:127.0.0.1:14550"])
    vehicles: list[VehicleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names(self) -> Scenario:
        self.assign_names()
        return self

    def placed_poses(self, spacing: float = 15.0) -> list[list[float]]:
        """Every vehicle's pose. Vehicles without one are lined up along -y, `spacing`
        apart and clear of the placed ones, instead of all starting on the same spot."""
        taken = [v.pose for v in self.vehicles if v.placed]
        out: list[list[float]] = []
        slot = 0
        for v in self.vehicles:
            if v.placed:
                out.append(list(v.pose))
                continue
            while True:
                x, y = 0.0, -slot * spacing
                slot += 1
                if all((p[0] - x) ** 2 + (p[1] - y) ** 2 >= (spacing * 0.6) ** 2 for p in taken):
                    break
            pose = [x, y, v.pose[2], v.pose[3], v.pose[4], v.pose[5]]
            taken.append(pose)
            out.append(pose)
        return out

    def assign_names(self) -> None:
        """Give every vehicle a unique, filesystem-safe name."""
        used: set[str] = set()
        for v in self.vehicles:
            base = _NAME_RE.sub("_", v.name or v.model.replace("sitl:", "")).strip("_") or "vehicle"
            name, n = base, 2
            while name in used:
                name, n = f"{base}_{n}", n + 1
            v.name = name
            used.add(name)

    # --- files -----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def dump(self) -> str:
        data = self.model_dump(exclude_defaults=True, mode="json")
        data.setdefault("name", self.name)
        data["vehicles"] = [
            {"model": v.model, **v.model_dump(exclude_defaults=True, mode="json"),
             **({"pose": list(v.pose)} if v.placed else {})}  # a given pose stays given, even at 0 0 0.3
            for v in self.vehicles
        ]
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dump(), encoding="utf-8")
