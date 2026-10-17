"""Fixtures: an isolated Hangar home and a tiny fake model library."""

import textwrap
from pathlib import Path

import pytest

from hangar.settings import Settings

MODEL_SDF = """\
<?xml version="1.0"?>
<sdf version="1.9">
  <model name="{name}">
    <link name="base_link"/>
    <plugin name="ArduPilotPlugin" filename="ArduPilotPlugin">
      <fdm_addr>127.0.0.1</fdm_addr>
      <fdm_port_in>9002</fdm_port_in>
    </plugin>
    {extra}
  </model>
</sdf>
"""

CAMERA = """\
<link name="camera_link">
      <sensor name="camera" type="camera">
        <plugin name="GstCameraPlugin" filename="GstCameraPlugin">
          <udp_host>127.0.0.1</udp_host>
          <udp_port>5605</udp_port>
        </plugin>
      </sensor>
    </link>"""

WORLD_SDF = """\
<?xml version="1.0"?>
<sdf version="1.9">
  <world name="runway">
    <include><uri>model://runway_ground</uri></include>
    <include><uri>model://testplane</uri><pose>0 0 0 0 0 0</pose></include>
  </world>
</sdf>
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.delenv("HANGAR_CONFIG", raising=False)


@pytest.fixture
def library(tmp_path) -> Path:
    """SITL_Models-like layout: Gazebo/models, Gazebo/worlds, Gazebo/config."""
    root = tmp_path / "SITL_Models" / "Gazebo"
    for name, extra in (("testplane", CAMERA), ("mystery_thing", ""), ("testcopter", "")):
        d = root / "models" / name
        (d / "meshes").mkdir(parents=True)
        (d / "meshes" / "body.dae").write_text("mesh")
        (d / "model.sdf").write_text(MODEL_SDF.format(name=name, extra=extra))
        (d / "model.config").write_text(f"<?xml version='1.0'?><model><name>{name}</name></model>")
    (root / "models" / "testcopter" / "hangar.yaml").write_text("type: copter\n")
    (root / "models" / "runway_ground").mkdir()
    (root / "models" / "runway_ground" / "model.sdf").write_text("<sdf><model name='g'/></sdf>")
    (root / "worlds").mkdir()
    (root / "worlds" / "runway.sdf").write_text(WORLD_SDF)
    (root / "config").mkdir()
    (root / "config" / "testplane.param").write_text("SIM_X 1\n")
    return root


@pytest.fixture
def settings(library, tmp_path) -> Settings:
    ap = tmp_path / "ardupilot"
    dp = ap / "Tools" / "autotest" / "default_params"
    dp.mkdir(parents=True)
    for f in ("plane.parm", "copter.parm", "rover.parm", "motorboat.parm"):
        (dp / f).write_text(textwrap.dedent("FOO 1\n"))
    return Settings(
        ardupilot_dir=str(ap),
        model_paths=[str(library / "models")],
        world_paths=[str(library / "worlds")],
    )
