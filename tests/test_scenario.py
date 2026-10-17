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
