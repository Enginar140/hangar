"""The shared MAVProxy map: install commands, routing, '!commands', clean close."""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from hangar import sources
from hangar.catalog import Catalog
from hangar.mavlink import VehicleLink
from hangar.ports import PortPlan
from hangar.scenario import Scenario
from hangar.supervisor import SimState, Supervisor, VehicleRuntime
from test_mavlink import FakeSitl, free_port

FAKE_MAVPROXY = textwrap.dedent("""\
    #!{python}
    # Stands in for mavproxy.py: listens where Hangar sends, records stdin, quits on 'exit'.
    import socket, sys, threading
    port = int(next(a for a in sys.argv if a.startswith("--master=")).rsplit(":", 1)[1])
    out = open("{record}", "a", buffering=1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    def rx():
        while True:
            s.recv(65536)
            out.write("datagram\\n")
    threading.Thread(target=rx, daemon=True).start()
    print("GUIDED> Loaded module map", flush=True)
    for line in sys.stdin:
        out.write("stdin " + line)
        if line.strip() == "exit":
            break
    """)


def test_install_commands_pick_the_ubuntu_wxpython_wheel(tmp_path):
    src = sources.load_manifest()["mavproxy"]
    cmds = sources.python_env_commands(src, "uv", tmp_path / "env", "24.04", "x86_64")
    flat = [" ".join(c) for c in cmds]
    assert flat[0].startswith("uv venv")
    assert any("--no-deps MAVProxy==" in c for c in flat)
    assert any("ubuntu-24.04/wxPython-4.2.2-cp312-cp312-linux_x86_64.whl" in c for c in flat)
    assert not any("vtk" in c.lower() for c in flat)


def test_install_explains_unsupported_systems(tmp_path):
    src = sources.load_manifest()["mavproxy"]
    with pytest.raises(RuntimeError, match="only for Ubuntu"):
        sources.python_env_commands(src, "uv", tmp_path / "env", None, "aarch64")


async def test_map_routes_commands_and_closes_cleanly(settings, tmp_path, monkeypatch):
    record = tmp_path / "mavproxy.record"
    fake = tmp_path / "mavproxy.py"
    fake.write_text(FAKE_MAVPROXY.format(python=sys.executable, record=record))
    fake.chmod(0o755)
    monkeypatch.setattr(type(settings), "mavproxy", lambda self: fake)

    sitl = FakeSitl(sysid=1)
    port = free_port()
    server = await asyncio.start_server(sitl.handle, "127.0.0.1", port)
    sc = Scenario.model_validate({"vehicles": [{"model": "sitl:plane"}]})
    cat = Catalog(settings)
    sup = Supervisor(sc, settings, cat)
    plan = PortPlan.build(sc, {}, {})
    v = VehicleRuntime(sc.vehicles[0], cat.get("sitl:plane"), plan.vehicles[0], tmp_path / "plane")
    v.link = VehicleLink("plane", 1, port, [])
    await v.link.start()
    sup.vehicles, sup.run_dir, sup.state = [v], tmp_path, SimState.RUNNING
    try:
        assert await v.link.wait_connected(5)
        assert await sup.toggle_map() is True
        map_ep = sup._map_output
        assert map_ep in v.link.endpoints
        await asyncio.sleep(1.0)
        assert sup.map_open
        assert "datagram" in record.read_text(), "vehicle traffic did not reach MAVProxy"
        sup.mavproxy_command(1, "wp list")
        await asyncio.sleep(0.3)
        assert "stdin vehicle 1\nstdin wp list\n" in record.read_text()
        assert any("Loaded module map" in line.text for line in sup.logs)  # prompt stripped

        assert await sup.toggle_map() is False
        assert "stdin exit" in record.read_text(), "MAVProxy was not asked to exit cleanly"
        assert not sup.map_open and sup._map_output is None
        assert map_ep not in v.link.endpoints
        assert v.link.endpoints == plan.vehicles[0].outputs  # the planned outputs stay
    finally:
        await v.link.stop()
        server.close()


def test_settings_find_installed_mavproxy(settings):
    from hangar import paths

    assert settings.mavproxy() is None or "mavproxy" in str(settings.mavproxy())
    env = paths.sources_dir() / "tools" / "mavproxy"
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "mavproxy.py").write_text("")
    assert settings.mavproxy() is None or settings.mavproxy() != env / "bin" / "mavproxy.py"
    (env / ".hangar-fetched").write_text("1.8.75")
    assert settings.mavproxy() == env / "bin" / "mavproxy.py"
    assert Path(settings.mavproxy()).name == "mavproxy.py"
