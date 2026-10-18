"""Builds the Gazebo world for a run.

Gazebo has no per-include way to change a plugin's port, so each vehicle gets
a small copy of its model: a rewritten model.sdf/model.config (unique name,
its FDM port, its camera port, a camera if the scenario asks for one the model
lacks) next to symlinks to the original meshes and materials. Nothing outside
the run directory is touched.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .catalog import VehicleModel
from .ports import VehiclePorts
from .scenario import CameraSpec

DEFAULT_CAMERA_POSE = [0.3, 0.0, 0.05, 0.0, 0.0, 0.0]  # a little ahead of the body origin
CAMERA_LINK = "hangar_camera_link"


@dataclass
class WorldVehicle:
    model: VehicleModel
    ports: VehiclePorts
    pose: str  # "x y z roll pitch yaw" in degrees
    camera: CameraSpec | None = None

    @property
    def instance_name(self) -> str:
        return self.ports.name

    @property
    def adds_camera(self) -> bool:
        return bool(self.ports.camera) and not self.model.has_camera


def parse_sdf(path: Path) -> ET.ElementTree:
    """ET.parse, tolerating custom elements whose prefix (e.g. ``gz:``) is never
    declared. The prefix is declared and kept, so Gazebo reads the same file."""
    try:
        return ET.parse(path)
    except ET.ParseError as e:
        if "unbound prefix" not in str(e):
            raise
    text = path.read_text(encoding="utf-8")
    prefixes = set(re.findall(r"</?([A-Za-z_][\w.-]*):[A-Za-z_]", text)) - {"xml"}
    decls = "".join(f' xmlns:{p}="http://gazebosim.org/schema/{p}"'
                    for p in sorted(prefixes) if f"xmlns:{p}=" not in text)
    for p in prefixes:
        ET.register_namespace(p, f"http://gazebosim.org/schema/{p}")
    text = re.sub(r"<sdf\b", "<sdf" + decls, text, count=1)
    return ET.ElementTree(ET.fromstring(text))


def build_world(world_sdf: Path, vehicles: list[WorldVehicle], out_dir: Path,
                ardupilot_models: set[str], model_paths: Sequence[Path] = ()) -> Path:
    models_dir = out_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    tree = parse_sdf(world_sdf)
    world = tree.getroot().find("world")
    if world is None:
        raise ValueError(f"{world_sdf} has no <world> element")

    _remove_vehicle_includes(world, ardupilot_models)
    if any(v.ports.camera for v in vehicles):
        _ensure_sensors_system(world)

    for v in vehicles:
        _make_instance_model(v, models_dir, model_paths)
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


def _make_instance_model(v: WorldVehicle, models_dir: Path, model_paths: Sequence[Path] = ()) -> Path:
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

    tree = parse_sdf(src / "model.sdf")
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
    if v.adds_camera:
        parent = v.model.camera_parent or _body_link(model, [src.parent, *model_paths])
        pose = (v.camera.pose if v.camera and v.camera.pose else None) or v.model.camera_pose or DEFAULT_CAMERA_POSE
        _add_camera(model, parent, pose, v.camera or CameraSpec(), v.ports.camera)
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


# --- camera injection ------------------------------------------------------


def _body_link(model: ET.Element, search: Sequence[Path]) -> str:
    """The link a camera should be fixed to: base_link, or the base link of the
    model this one includes (iris/zephyr style), else the first link."""
    links = [link.get("name") for link in model.findall("link")]
    if "base_link" in links:
        return "base_link"
    for inc in model.findall("include"):
        uri = (inc.findtext("uri") or "").strip()
        if not uri.startswith("model://"):
            continue
        sub = uri.removeprefix("model://").strip("/")
        for root in search:
            sdf = root / sub / "model.sdf"
            if sdf.is_file():
                inner = parse_sdf(sdf).getroot().find("model")
                if inner is None:
                    break
                inner_links = [link.get("name") for link in inner.findall("link")]
                if not inner_links:
                    break
                scope = (inc.findtext("name") or "").strip() or inner.get("name") or sub
                link = "base_link" if "base_link" in inner_links else inner_links[0]
                return f"{scope}::{link}"
    if links:
        return links[0]
    raise ValueError(f"{model.get('name')}: no link to attach a camera to; set camera_parent in hangar.yaml")


def _add_camera(model: ET.Element, parent: str, pose: list[float], cam: CameraSpec, port: int) -> None:
    """The camera block known to work with ArduPilot's GstCameraPlugin."""
    x, y, z, roll, pitch, yaw = pose
    link = ET.SubElement(model, "link", {"name": CAMERA_LINK})
    ET.SubElement(link, "pose").text = (
        f"{x:g} {y:g} {z:g} {math.radians(roll):.6g} {math.radians(pitch):.6g} {math.radians(yaw):.6g}"
    )
    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "mass").text = "0.01"
    inertia = ET.SubElement(inertial, "inertia")
    for k, val in (("ixx", "1e-6"), ("ixy", "0"), ("ixz", "0"), ("iyy", "1e-6"), ("iyz", "0"), ("izz", "1e-6")):
        ET.SubElement(inertia, k).text = val
    visual = ET.SubElement(link, "visual", {"name": "hangar_camera_visual"})
    box = ET.SubElement(ET.SubElement(visual, "geometry"), "box")
    ET.SubElement(box, "size").text = "0.04 0.04 0.03"
    material = ET.SubElement(visual, "material")
    ET.SubElement(material, "ambient").text = "0.1 0.1 0.1 1"
    ET.SubElement(material, "diffuse").text = "0.1 0.1 0.1 1"

    sensor = ET.SubElement(link, "sensor", {"name": "camera", "type": "camera"})
    camera = ET.SubElement(sensor, "camera")
    ET.SubElement(camera, "horizontal_fov").text = f"{math.radians(cam.fov):.6g}"
    image = ET.SubElement(camera, "image")
    ET.SubElement(image, "width").text = str(cam.width)
    ET.SubElement(image, "height").text = str(cam.height)
    ET.SubElement(image, "format").text = "R8G8B8"
    clip = ET.SubElement(camera, "clip")
    ET.SubElement(clip, "near").text = "0.1"
    ET.SubElement(clip, "far").text = "1000"
    plugin = ET.SubElement(sensor, "plugin", {"name": "GstCameraPlugin", "filename": "GstCameraPlugin"})
    ET.SubElement(plugin, "udp_host").text = "127.0.0.1"
    ET.SubElement(plugin, "udp_port").text = str(port)
    ET.SubElement(plugin, "use_basic_pipeline").text = "true"
    ET.SubElement(plugin, "use_cuda").text = "false"
    ET.SubElement(sensor, "always_on").text = "1"
    ET.SubElement(sensor, "update_rate").text = str(cam.fps)
    ET.SubElement(sensor, "visualize").text = "1"

    joint = ET.SubElement(model, "joint", {"name": "hangar_camera_joint", "type": "fixed"})
    ET.SubElement(joint, "parent").text = parent
    ET.SubElement(joint, "child").text = CAMERA_LINK
