"""Slow-simulation causes, and the simulation speed measured from the autopilot's clock."""

import json

import pytest

from hangar import perf
from hangar.mavlink import VehicleLink


def fake_drm(tmp_path, vendors):
    for i, v in enumerate(vendors):
        d = tmp_path / f"card{i}" / "device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(v + "\n")
    return tmp_path


def test_gpu_detection(tmp_path):
    assert perf.integrated_gpu_only(fake_drm(tmp_path / "a", ["0x8086"]))
    assert not perf.integrated_gpu_only(fake_drm(tmp_path / "b", ["0x8086", "0x10de"]))  # laptop with NVIDIA
    assert not perf.integrated_gpu_only(tmp_path / "none")  # unknown: no claim


def test_warnings_name_the_heavy_parts(tmp_path):
    world = tmp_path / "w" / "w.sdf"
    world.parent.mkdir()
    world.write_text("<sdf/>")
    (world.parent / "hangar-earth.json").write_text(json.dumps({"buildings": {"count": 7049, "triangles": 95414}}))
    cams = [perf.Camera("swan", 1920, 1080, 30), perf.Camera("sky", 1280, 720, 60), perf.Camera("cat", 1280, 720, 30)]
    out = perf.warnings(cams, world, headless=False, integrated=True)
    text = " | ".join(out)
    assert "integrated GPU" in text and "headless" in text
    assert "swan: 1920x1080 @ 30" in text and "sky: 1280x720 @ 60" in text and "cat:" not in text
    assert "3 cameras" in text and "7049 buildings" in text
    assert perf.warnings([perf.Camera("cat", 1280, 720, 30)], None, headless=True, integrated=False) == []


def test_sim_speed_from_the_autopilot_clock(monkeypatch):
    link = VehicleLink("v", 1, 0, [])
    now = [100.0]
    monkeypatch.setattr("hangar.mavlink.time.monotonic", lambda: now[0])
    for i in range(9):  # 4 real seconds, the autopilot's clock advances 0.2 s
        link._clock(10_000 + i * 25)
        now[0] += 0.5
    assert link.telemetry.sim_speed == pytest.approx(0.05, rel=0.01)
    link._clock(500)  # rebooted: start over instead of reporting nonsense
    assert len(link._clock_samples) == 1


def test_preflight_warns_about_a_heavy_camera(settings, monkeypatch):
    from hangar.catalog import Catalog
    from hangar.scenario import Scenario
    from hangar.supervisor import Supervisor

    monkeypatch.setattr(perf, "integrated_gpu_only", lambda drm=None: False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)  # pretend gz exists
    sc = Scenario.model_validate({"world": "runway", "vehicles": [
        {"model": "testplane", "camera": {"width": 1920, "height": 1080, "fps": 60}}]})
    pf = Supervisor(sc, settings, Catalog(settings)).preflight(check_ports=False)
    assert any("1920x1080 @ 60" in w for w in pf.warnings)


def test_visual_only_buildings_do_not_count(tmp_path):
    world = tmp_path / "w.sdf"
    world.write_text("<sdf/>")
    (tmp_path / "hangar-earth.json").write_text(json.dumps({"buildings": {"count": 9000, "collision": False}}))
    assert perf.world_weight(world) == (0, 0)


def test_busy_instance_zero_moves_to_a_free_one(settings, monkeypatch):
    import socket

    from hangar.catalog import Catalog
    from hangar.ports import SERIAL0_BASE
    from hangar.scenario import Scenario
    from hangar.supervisor import Supervisor

    sc = Scenario.model_validate({"vehicles": [{"model": "sitl:copter"}]})
    with socket.socket() as other_sitl:
        other_sitl.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            other_sitl.bind(("0.0.0.0", SERIAL0_BASE))
        except OSError:
            pass  # something already holds it: just as good
        other_sitl.listen()
        sup = Supervisor(sc, settings, Catalog(settings))
        pf = sup.preflight()
        assert sup._auto_instance >= 1
        assert not [e for e in pf.errors if "port conflict" in e]
        assert any("using instance" in w for w in pf.warnings)
        assert pf.plan.vehicles[0].instance == sup._auto_instance
        assert "first_instance" not in sc.dump()  # the scenario as written is unchanged
        explicit = Scenario.model_validate({"first_instance": 0, "vehicles": [{"model": "sitl:copter"}]})
        pf = Supervisor(explicit, settings, Catalog(settings)).preflight()
        assert any("port conflict" in e for e in pf.errors)  # chosen by the user: kept, and reported
