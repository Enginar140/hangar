import pytest
from pydantic import ValidationError

from hangar.scenario import Home, Scenario


def test_unique_safe_names():
    sc = Scenario.model_validate({"vehicles": [
        {"model": "sitl:plane"}, {"model": "sitl:plane"}, {"model": "firtinam", "name": "my plane!"},
        {"model": "x", "name": "plane"},
    ]})
    assert [v.name for v in sc.vehicles] == ["plane", "plane_2", "my_plane", "plane_3"]


def test_pose_shorthands():
    sc = Scenario.model_validate({"vehicles": [
        {"model": "a", "pose": [1, 2, 3]}, {"model": "b", "pose": [1, 2, 3, 90]},
    ]})
    assert sc.vehicles[0].pose == [1, 2, 3, 0, 0, 0]
    assert sc.vehicles[1].pose == [1, 2, 3, 0, 0, 90]
    with pytest.raises(ValidationError):
        Scenario.model_validate({"vehicles": [{"model": "a", "pose": [1, 2]}]})


def test_unknown_keys_are_errors():
    with pytest.raises(ValidationError):
        Scenario.model_validate({"vehicels": []})


def test_roundtrip(tmp_path):
    sc = Scenario.model_validate({
        "name": "t", "world": "runway", "outputs": ["udp:127.0.0.1:{15000+i}"],
        "vehicles": [{"model": "firtinam", "params": {"SIM_WIND_SPD": 4}, "camera": {"v4l2_device": "/dev/video2"}}],
    })
    path = tmp_path / "s.yaml"
    sc.save(path)
    again = Scenario.load(path)
    assert again == sc
    assert path.read_text().splitlines()[0] == "name: t"


def test_home_offset():
    home = Home(lat=40.0, lon=29.0)
    moved = home.offset(north_m=111_319.5, east_m=0)
    assert moved.lat == pytest.approx(41.0, abs=1e-3)
    assert moved.lon == pytest.approx(29.0)


def test_log_file_written_for_a_run(settings, tmp_path):
    from hangar import paths
    from hangar.catalog import Catalog
    from hangar.supervisor import Supervisor

    sc = Scenario.model_validate({"vehicles": [{"model": "sitl:plane"}]})
    sup = Supervisor(sc, settings, Catalog(settings))
    sup.log("hangar", "before the run", "info")
    sup._attempt_started = 0.0
    sup.run_dir = paths.runs_dir() / "20260101-000000-test"
    sup.run_dir.mkdir(parents=True)
    sup._open_logfile()
    sup.log("plane", "PreArm: Gyros inconsistent", "error")
    sup._close_logfile()
    text = (sup.run_dir / "logs" / "hangar.log").read_text()
    assert "before the run" in text and "ERROR    plane" in text
    assert (paths.runs_dir() / "latest").resolve() == sup.run_dir.resolve()


def test_vehicles_without_pose_are_spread_out():
    sc = Scenario.model_validate({"vehicles": [
        {"model": "a", "name": "one", "pose": [0, 5, 0.3]},
        {"model": "b", "name": "two"},
        {"model": "c", "name": "three"},
    ]})
    poses = sc.placed_poses()
    assert poses[0][:2] == [0, 5]  # given poses stay
    assert poses[1][:2] == [0, -15] and poses[2][:2] == [0, -30]  # (0, 0) is too close to "one"
    xy = [tuple(p[:2]) for p in poses]
    assert len(set(xy)) == 3


def test_a_given_default_pose_survives_saving(tmp_path):
    sc = Scenario.model_validate({"vehicles": [{"model": "a", "pose": [0, 0, 0.3]}, {"model": "b"}]})
    sc.save(tmp_path / "s.yaml")
    again = Scenario.load(tmp_path / "s.yaml")
    assert again.vehicles[0].placed and not again.vehicles[1].placed
