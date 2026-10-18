import xml.etree.ElementTree as ET
from pathlib import Path

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
    # relay mode: the plugin sends to Hangar's relay, readers use 5600 + index
    assert sdf.findtext(".//plugin[@name='GstCameraPlugin']/udp_port") == "5701"
    assert (second / "meshes").is_symlink()
    assert ET.parse(second / "model.config").getroot().findtext("name") == "testplane_2"
    # originals untouched
    assert "9002" in (settings.resolved_model_paths()[0] / "testplane" / "model.sdf").read_text()


def test_gazebo_finds_the_generated_models(settings, tmp_path):
    """Resolve every model:// include the way gz does: through GZ_SIM_RESOURCE_PATH."""
    from hangar.supervisor import Supervisor

    cat = Catalog(settings)
    sc = Scenario.model_validate({"world": "runway", "vehicles": [{"model": "testplane", "name": "sky"}]})
    sup = Supervisor(sc, settings, cat)
    sup.run_dir = tmp_path / "run"
    plan = sup.preflight(check_ports=False).plan
    vehicles = [WorldVehicle(cat.get("testplane"), plan.vehicles[0], sc.vehicles[0].pose_sdf)]
    out = build_world(cat.worlds["runway"].path, vehicles, sup.world_dir(),
                      {n for n, m in cat.vehicles.items() if m.needs_gazebo})

    search = [p for p in sup.gazebo_env()["GZ_SIM_RESOURCE_PATH"].split(":") if p]
    for inc in ET.parse(out).getroot().iter("include"):
        name = inc.findtext("uri").removeprefix("model://")
        assert any((Path(d) / name / "model.sdf").exists() for d in search), f"gz cannot find {name}"


def _build(settings, tmp_path, spec: dict):
    cat = Catalog(settings)
    sc = Scenario.model_validate({"world": "runway", "vehicles": [spec]})
    from hangar.supervisor import _wants_camera

    m = cat.get(spec["model"])
    names = [v.name for v in sc.vehicles]
    plan = PortPlan.build(sc, gazebo=dict.fromkeys(names, True),
                          cameras={v.name: _wants_camera(m, v) for v in sc.vehicles})
    wv = WorldVehicle(m, plan.vehicles[0], sc.vehicles[0].pose_sdf, sc.vehicles[0].camera)
    build_world(cat.worlds["runway"].path, [wv], tmp_path / "run", set(cat.vehicles), settings.resolved_model_paths())
    return wv, ET.parse(tmp_path / "run" / "models" / wv.instance_name / "model.sdf").getroot().find("model")


def test_camera_is_added_to_a_model_without_one(settings, tmp_path):
    wv, model = _build(settings, tmp_path, {
        "model": "testcopter", "camera": {"pose": [0.1, 0, -0.05, 0, 90, 0], "width": 640, "height": 480, "fov": 90},
    })
    assert wv.adds_camera and wv.ports.camera == 5600
    link = model.find("link[@name='hangar_camera_link']")
    assert link.findtext("pose").startswith("0.1 0 -0.05 0 1.5708")  # pitch 90 deg -> looks down
    assert link.findtext(".//image/width") == "640" and link.findtext(".//horizontal_fov") == "1.5708"
    assert link.findtext(".//plugin[@name='GstCameraPlugin']/udp_port") == "5700"
    joint = model.find("joint[@name='hangar_camera_joint']")
    assert (joint.findtext("parent"), joint.findtext("child")) == ("base_link", "hangar_camera_link")


def test_model_camera_is_kept_not_duplicated(settings, tmp_path):
    wv, model = _build(settings, tmp_path, {"model": "testplane", "camera": True})
    assert not wv.adds_camera
    assert model.find("link[@name='hangar_camera_link']") is None
    assert model.findtext(".//plugin[@name='GstCameraPlugin']/udp_port") == "5700"


def test_no_camera_unless_asked(settings, tmp_path):
    wv, model = _build(settings, tmp_path, {"model": "testcopter"})
    assert wv.ports.camera is None and model.find("link[@name='hangar_camera_link']") is None


def test_camera_attaches_to_included_body(settings, library, tmp_path):
    """iris/zephyr style: the body comes from an <include>d model."""
    body = library / "models" / "frame_only"
    body.mkdir()
    (body / "model.sdf").write_text("<sdf version='1.9'><model name='frame_only'>"
                                    "<link name='base_link'/></model></sdf>")
    wrapper = library / "models" / "wrapped_copter"
    wrapper.mkdir()
    (wrapper / "hangar.yaml").write_text("type: copter\n")
    (wrapper / "model.sdf").write_text(
        "<sdf version='1.9'><model name='wrapped_copter'><include><uri>model://frame_only</uri></include>"
        "<plugin name='ArduPilotPlugin' filename='ArduPilotPlugin'><fdm_port_in>9002</fdm_port_in></plugin>"
        "</model></sdf>")
    _, model = _build(settings, tmp_path, {"model": "wrapped_copter", "camera": True})
    assert model.findtext("joint[@name='hangar_camera_joint']/parent") == "frame_only::base_link"


def test_undeclared_gz_prefix_is_tolerated(settings, library, tmp_path):
    """SITL_Models' catamaran uses <gz:...> elements without declaring the prefix."""
    boat = library / "models" / "prefixed_boat"
    boat.mkdir()
    (boat / "hangar.yaml").write_text("type: boat\n")
    (boat / "model.sdf").write_text(
        "<sdf version='1.9'><model name='prefixed_boat'><link name='base_link'>"
        "<gz:hydro><gz:volume>1</gz:volume></gz:hydro></link>"
        "<plugin name='ArduPilotPlugin' filename='ArduPilotPlugin'><fdm_port_in>9002</fdm_port_in></plugin>"
        "</model></sdf>")
    _, model = _build(settings, tmp_path, {"model": "prefixed_boat"})
    out = (tmp_path / "run" / "models" / "prefixed_boat" / "model.sdf").read_text()
    assert "<gz:hydro>" in out and "xmlns:gz=" in out
    assert model.findtext(".//plugin[@name='ArduPilotPlugin']/fdm_port_in") == "9002"


def test_camera_shorthand():
    sc = Scenario.model_validate({"vehicles": [{"model": "a", "camera": True}, {"model": "b", "camera": False}]})
    assert sc.vehicles[0].camera.enabled and sc.vehicles[0].camera.width == 1280
    assert sc.vehicles[1].camera.enabled is False


def test_direct_camera_sends_straight_to_its_target(settings, tmp_path):
    wv, model = _build(settings, tmp_path, {
        "model": "testcopter", "camera": {"stream": "direct", "outputs": ["10.205.218.120:{5600+i}"]},
    })
    assert wv.ports.camera_mode == "direct" and wv.ports.camera is None  # this computer gets nothing
    plugin = model.find(".//plugin[@name='GstCameraPlugin']")
    assert (plugin.findtext("udp_host"), plugin.findtext("udp_port")) == ("10.205.218.120", "5600")


def test_relay_camera_targets_and_bindings(settings):
    from hangar.ports import PortPlan

    sc = Scenario.model_validate({"vehicles": [
        {"model": "testplane", "camera": {"outputs": ["10.0.0.5:5600", "127.0.0.1:{5650+i}", "127.0.0.1:5600"]}},
        {"model": "testplane", "camera": {"stream": "direct", "outputs": ["239.0.0.1:5601"]}},
    ]})
    plan = PortPlan.build(sc, gazebo={"testplane": True, "testplane_2": True},
                          cameras={"testplane": True, "testplane_2": True})
    relay, direct = plan.vehicles
    assert relay.camera == 5600 and relay.camera_send.port == 5700
    # the local delivery port is not repeated as a target
    assert [f"{t.host}:{t.port}" for t in relay.camera_targets] == ["10.0.0.5:5600", "127.0.0.1:5650"]
    owners = {(b.port, b.owner) for b in plan.bindings()}
    assert (5700, "testplane camera relay") in owners and (5650, "testplane camera output") in owners
    assert not any(b.port == 5600 and "output" in b.owner for b in plan.bindings())  # remote host binds nothing
    assert direct.camera_send.host == "239.0.0.1" and direct.camera is None


def test_direct_needs_exactly_one_output():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="exactly one"):
        Scenario.model_validate({"vehicles": [{"model": "a", "camera": {"stream": "direct"}}]})


def test_home_follows_the_world_unless_the_scenario_sets_it(settings, library):
    from hangar.supervisor import Supervisor

    world = library / "worlds" / "runway.sdf"
    world.write_text(world.read_text().replace(
        '<world name="runway">',
        '<world name="runway"><spherical_coordinates><latitude_deg>39.9208</latitude_deg>'
        '<longitude_deg>32.8541</longitude_deg><elevation>938</elevation></spherical_coordinates>'))
    cat = Catalog(settings)
    assert cat.worlds["runway"].location == (39.9208, 32.8541, 938.0)

    sc = Scenario.model_validate({"world": "runway", "vehicles": [{"model": "testplane"}]})
    h = Supervisor(sc, settings, cat).home()
    assert (h.lat, h.lon, h.alt) == (39.9208, 32.8541, 938.0)

    own = Scenario.model_validate({"world": "runway", "home": {"lat": 41.0, "lon": 29.0, "alt": 50},
                                   "vehicles": [{"model": "testplane"}]})
    assert Supervisor(own, settings, cat).home().lat == 41.0  # an explicit home wins


def test_world_without_location_gets_the_home(settings, tmp_path):
    from hangar.scenario import Home

    cat = Catalog(settings)
    out = build_world(cat.worlds["runway"].path, [], tmp_path / "run", set(), (), Home(lat=40.5, lon=30.5, alt=100))
    sc = ET.parse(out).getroot().find("world/spherical_coordinates")
    assert float(sc.findtext("latitude_deg")) == 40.5 and sc.findtext("surface_model") == "EARTH_WGS84"
