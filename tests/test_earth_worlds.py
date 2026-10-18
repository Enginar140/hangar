"""Worlds made by hangar-earth: discovery, file paths, vehicles on the ground."""

import json
import struct
import sys
import xml.etree.ElementTree as ET

from hangar import paths
from hangar.catalog import Catalog
from hangar.cli import main
from hangar.ports import PortPlan
from hangar.scenario import Scenario
from hangar.world import TerrainHeights, WorldVehicle, build_world

SDF = """<?xml version="1.0"?><sdf version="1.9"><world name="{name}">
<spherical_coordinates><latitude_deg>39.88</latitude_deg><longitude_deg>33.23</longitude_deg>
<elevation>944.5</elevation></spherical_coordinates>
<model name="terrain"><static>true</static><link name="link"><visual name="v"><geometry><heightmap>
<texture><diffuse>satellite.jpg</diffuse><normal>normal.png</normal><size>1000</size></texture>
<uri>terrain.png</uri><size>1000 1000 50</size></heightmap></geometry></visual></link></model>
</world></sdf>"""


def fake_earth_world(folder, name="hills", n=3):
    """A 1 km world whose ground rises from 0 m (west) to 40 m (east)."""
    folder.mkdir(parents=True)
    (folder / f"{name}.sdf").write_text(SDF.format(name=name))
    for f in ("terrain.png", "satellite.jpg", "normal.png"):
        (folder / f).write_bytes(b"x")
    heights = [0, 200, 400] * n  # decimetres, per row: west, middle, east
    (folder / "heights.u16").write_bytes(struct.pack(f"<{n * n}H", *heights))
    (folder / "hangar-earth.json").write_text(json.dumps(
        {"size_m": 1000, "heights": {"file": "heights.u16", "n": n}}))
    return folder


def test_generated_worlds_are_found_with_their_location(settings):
    fake_earth_world(paths.data_dir() / "worlds" / "hills")
    w = Catalog(settings).worlds["hills"]
    assert w.location == (39.88, 33.23, 944.5)


def test_terrain_heights_and_lift(tmp_path):
    t = TerrainHeights.load(fake_earth_world(tmp_path / "w"))
    assert t.height_at(-500, 0) == 0 and t.height_at(0, 0) == 20 and t.height_at(500, 0) == 40
    assert t.height_at(5000, 0) == 40  # outside the area: clamped to the edge
    assert t.lift("0 0 0.3 0 0 90") == "0 0 20.3 0 0 90"
    assert TerrainHeights.load(tmp_path) is None  # not a hangar-earth world


def test_build_world_uses_absolute_files_and_puts_vehicles_on_the_ground(settings, tmp_path):
    folder = fake_earth_world(tmp_path / "hills")
    cat = Catalog(settings)
    sc = Scenario.model_validate({"vehicles": [{"model": "testplane", "pose": [500, 0, 0.3, 0, 0, 90]}]})
    plan = PortPlan.build(sc, gazebo={"testplane": True}, cameras={})
    wv = WorldVehicle(cat.get("testplane"), plan.vehicles[0], sc.vehicles[0].pose_sdf)
    out = build_world(folder / "hills.sdf", [wv], tmp_path / "run", set(), settings.resolved_model_paths())
    world = ET.parse(out).getroot().find("world")
    hm = world.find(".//heightmap")
    assert hm.findtext("uri") == str((folder / "terrain.png").resolve())
    assert hm.findtext("texture/diffuse") == str((folder / "satellite.jpg").resolve())
    inc = [i for i in world.findall("include") if i.findtext("name") == "testplane"][0]
    assert inc.findtext("pose") == "500 0 40.3 0 0 90"


def test_cli_world_create_with_a_generator(settings, tmp_path, monkeypatch):
    """`hangar world create` runs hangar-earth and the world is there afterwards."""
    gen = tmp_path / "bin" / "hangar-earth"
    gen.parent.mkdir()
    gen.write_text(f"""#!{sys.executable}
import sys, pathlib
a = sys.argv
out = pathlib.Path(a[a.index("--out") + 1]); name = a[a.index("--name") + 1]
out.mkdir(parents=True)
(out / (name + ".sdf")).write_text('<sdf version="1.9"><world name="' + name + '"/></sdf>')
print("generated", name)
""")
    gen.chmod(0o755)
    monkeypatch.setenv("PATH", f"{gen.parent}:/usr/bin:/bin")
    settings.save()
    assert main(["world", "create", "--lat", "39.88", "--lon", "33.23", "--name", "made_here"]) == 0
    assert (paths.data_dir() / "worlds" / "made_here" / "made_here.sdf").exists()
    assert "made_here" in Catalog(settings).worlds
    assert main(["world", "create", "--lat", "39.88", "--lon", "33.23", "--name", "made_here"]) == 1  # exists


def test_cli_passes_options_to_hangar_earth(settings, tmp_path, monkeypatch):
    gen = tmp_path / "bin" / "hangar-earth"
    gen.parent.mkdir()
    record = tmp_path / "argv.txt"
    gen.write_text(f"""#!{sys.executable}
import sys, pathlib
pathlib.Path({str(record)!r}).write_text(" ".join(sys.argv[1:]))
a = sys.argv; out = pathlib.Path(a[a.index("--out") + 1]); out.mkdir(parents=True)
(out / (a[a.index("--name") + 1] + ".sdf")).write_text("<sdf/>")
""")
    gen.chmod(0o755)
    monkeypatch.setenv("PATH", f"{gen.parent}:/usr/bin:/bin")
    dem = tmp_path / "dsm.tif"
    dem.write_bytes(b"x")
    assert main(["world", "create", "--lat", "39.92", "--lon", "32.85", "--size", "800", "--name", "opts",
                 "--no-buildings", "--dem", str(dem), "--texture", "8192"]) == 0
    argv = record.read_text()
    assert "--no-buildings" in argv and f"--dem {dem.resolve()}" in argv and "--texture 8192" in argv
    assert "--imagery" not in argv


def test_outdated_install_is_noticed():
    from hangar import sources

    src = sources.load_manifest()["hangar_earth"]
    assert src.installed_ref is None and not src.outdated
    src.path.mkdir(parents=True)
    (src.path / ".hangar-fetched").write_text("0.1.0")
    assert src.installed_ref == "0.1.0" and src.outdated
    (src.path / ".hangar-fetched").write_text(src.ref)
    assert not src.outdated
