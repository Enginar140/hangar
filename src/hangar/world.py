"""Builds the Gazebo world for a run.

Gazebo has no per-include way to change a plugin's port, so each vehicle gets
a small copy of its model: a rewritten model.sdf/model.config (unique name,
its FDM port, its camera port) next to symlinks to the original meshes and
materials. Nothing outside the run directory is touched.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .catalog import VehicleModel
from .ports import VehiclePorts


@dataclass
class WorldVehicle:
    model: VehicleModel
    ports: VehiclePorts
    pose: str  # "x y z roll pitch yaw" in degrees

    @property
    def instance_name(self) -> str:
        return f"{self.ports.name}"


def build_world(world_sdf: Path, vehicles: list[WorldVehicle], out_dir: Path,
                ardupilot_models: set[str]) -> Path:
    models_dir = out_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(world_sdf)
    world = tree.getroot().find("world")
    if world is None:
        raise ValueError(f"{world_sdf} has no <world> element")

    _remove_vehicle_includes(world, ardupilot_models)
    if any(v.ports.camera for v in vehicles):
        _ensure_sensors_system(world)

    for v in vehicles:
        _make_instance_model(v, models_dir)
        inc = ET.SubElement(world, "include")
        ET.SubElement(inc, "uri").text = f"model://{v.instance_name}"
        ET.SubElement(inc, "name").text = v.instance_name
        pose = ET.SubElement(inc, "pose", {"degrees": "true"})
        pose.text = v.pose

    out = out_dir / f"{world_sdf.stem}.sdf"
    ET.indent(tree)
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out


def _remove_vehicle_includes(world: ET.Element, ardupilot_models: set[str]) -> None:
    """Drop vehicles the world ships with; the scenario decides what flies."""
    for inc in list(world.findall("include")):
        uri = (inc.findtext("uri") or "").strip()
        if uri.startswith("model://") and uri[len("model://"):].strip("/") in ardupilot_models:
            world.remove(inc)


def _ensure_sensors_system(world: ET.Element) -> None:
    for p in world.findall("plugin"):
        if "sensors" in (p.get("filename", "") + p.get("name", "")).lower():
            return
    plugin = ET.SubElement(
        world, "plugin", {"filename": "gz-sim-sensors-system", "name": "gz::sim::systems::Sensors"}
    )
    ET.SubElement(plugin, "render_engine").text = "ogre2"


def _make_instance_model(v: WorldVehicle, models_dir: Path) -> Path:
    src = v.model.model_dir
    assert src is not None
    dst = models_dir / v.instance_name
    dst.mkdir(exist_ok=True)

    for item in src.iterdir():
        if item.name in ("model.sdf", "model.config"):
            continue
        link = dst / item.name
        if not link.exists():
            link.symlink_to(item.resolve())

    tree = ET.parse(src / "model.sdf")
    model = tree.getroot().find("model")
    if model is None:
        raise ValueError(f"{src / 'model.sdf'} has no <model> element")
    model.set("name", v.instance_name)

    found_fdm = False
    for plugin in tree.getroot().iter("plugin"):
        name = plugin.get("name", "") + plugin.get("filename", "")
        if "ArduPilotPlugin" in name:
            _set_child(plugin, "fdm_addr", "127.0.0.1")
            _set_child(plugin, "fdm_port_in", str(v.ports.fdm))
            found_fdm = True
        elif "GstCameraPlugin" in name and v.ports.camera:
            _set_child(plugin, "udp_host", "127.0.0.1")
            _set_child(plugin, "udp_port", str(v.ports.camera))
    if not found_fdm:
        raise ValueError(f"{v.model.name}: no ArduPilotPlugin in model.sdf")
    tree.write(dst / "model.sdf", encoding="utf-8", xml_declaration=True)

    cfg_src = src / "model.config"
    if cfg_src.exists():
        cfg = ET.parse(cfg_src)
        name_el = cfg.getroot().find("name")
        if name_el is not None:
            name_el.text = v.instance_name
        cfg.write(dst / "model.config", encoding="utf-8", xml_declaration=True)
    return dst


def _set_child(parent: ET.Element, tag: str, text: str) -> None:
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = text
