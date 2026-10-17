import xml.etree.ElementTree as ET

from hangar.catalog import Catalog
from hangar.ports import PortPlan
from hangar.scenario import Scenario
from hangar.settings import Settings
from hangar.world import WorldVehicle, build_world


def test_catalog_discovers_vehicles_and_worlds(settings):
    cat = Catalog(settings)
    plane = cat.get("testplane")
    assert plane and plane.type == "plane" and plane.needs_gazebo and plane.has_camera
    assert cat.get("testcopter").type == "copter"  # from hangar.yaml
    assert cat.get("runway_ground") is None  # scenery, no ArduPilotPlugin
    assert cat.get("mystery_thing") is None
    assert any("mystery_thing" in w for w in cat.warnings)
    assert "sitl:plane" in cat.vehicles and not cat.get("sitl:plane").needs_gazebo
    assert list(cat.worlds) == ["runway"]


def test_params_resolve_defaults_then_model(settings):
    cat = Catalog(settings)
    res = cat.resolve_params(cat.get("testplane"))
    assert [p.name for p in res.files] == ["plane.parm", "testplane.param"]
    assert res.missing == []


def test_settings_roundtrip(tmp_path):
    s = Settings(ardupilot_dir="~/ap", model_paths=["/a", "/b"], language="tr")
    path = tmp_path / "c.toml"
    s.save(path)
    assert Settings.load(path) == s
    s.set_key("gazebo.plugin_paths", "/x:/y")
    s.add_key("catalog.world_paths", "/w")
    assert s.plugin_paths == ["/x", "/y"] and s.world_paths == ["/w"]


def test_world_builder(settings, tmp_path):
    cat = Catalog(settings)
    sc = Scenario.model_validate({"world": "runway", "vehicles": [
        {"model": "testplane", "pose": [0, 0, 0.3, 90]},
        {"model": "testplane", "pose": [0, 10, 0.3]},
    ]})
    names = [v.name for v in sc.vehicles]
    plan = PortPlan.build(sc, gazebo=dict.fromkeys(names, True), cameras=dict.fromkeys(names, True))
    vehicles = [WorldVehicle(cat.get("testplane"), p, s.pose_sdf) for s, p in zip(sc.vehicles, plan.vehicles)]
    out = build_world(cat.worlds["runway"].path, vehicles, tmp_path / "run",
                      {n for n, m in cat.vehicles.items() if m.needs_gazebo})

    world = ET.parse(out).getroot().find("world")
    uris = [i.findtext("uri") for i in world.findall("include")]
    # the world's own testplane is replaced by the scenario's two instances
    assert uris == ["model://runway_ground", "model://testplane", "model://testplane_2"]
    assert world.find("plugin[@name='gz::sim::systems::Sensors']") is not None

    second = tmp_path / "run" / "models" / "testplane_2"
    sdf = ET.parse(second / "model.sdf").getroot()
    assert sdf.find("model").get("name") == "testplane_2"
    assert sdf.findtext(".//plugin[@name='ArduPilotPlugin']/fdm_port_in") == "9012"
    assert sdf.findtext(".//plugin[@name='GstCameraPlugin']/udp_port") == "5601"
    assert (second / "meshes").is_symlink()
    assert ET.parse(second / "model.config").getroot().findtext("name") == "testplane_2"
    # originals untouched
    assert "9002" in (settings.resolved_model_paths()[0] / "testplane" / "model.sdf").read_text()
