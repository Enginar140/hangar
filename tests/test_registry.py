import json
import os
import subprocess
import sys

from hangar import registry
from hangar.catalog import Catalog
from hangar.cli import main
from hangar.scenario import Scenario
from hangar.supervisor import Supervisor

SCENARIO = {"name": "two", "vehicles": [{"model": "sitl:copter"}, {"model": "sitl:plane"}]}


def _other_run(proc, **extra) -> dict:
    """An entry as another Hangar process would write it."""
    entry = {"format": 1, "pid": proc.pid, "proc_start": registry.process_start(proc.pid), "run": "r1",
             "run_dir": "/x/r1", "state": "running", "started": 1.0, "owner": "savasan:t7", "scenario": "duel",
             "vehicles": [{"name": "a", "mavlink": "tcp:127.0.0.1:5760"}], **extra}
    registry.active_dir().mkdir(parents=True, exist_ok=True)
    (registry.active_dir() / f"{proc.pid}-r1.json").write_text(json.dumps(entry))
    return entry


def test_register_update_close(settings, tmp_path):
    sup = Supervisor(Scenario.model_validate(SCENARIO), settings, Catalog(settings), owner="me")
    pf = sup.preflight(check_ports=False)
    sup.run_dir = tmp_path / "run"
    sup._register(pf.plan)

    [entry] = registry.active_runs()
    assert entry["pid"] == os.getpid() and entry["owner"] == "me" and entry["state"] == "starting"
    assert [v["name"] for v in entry["vehicles"]] == ["copter", "plane"]
    assert entry["vehicles"][1]["mavlink"] == "tcp:127.0.0.1:5770"
    sup._registration.update(state="running")
    assert json.loads((sup.run_dir / "state.json").read_text())["state"] == "running"

    sup._unregister("stopped")
    assert registry.active_runs() == []
    kept = json.loads((sup.run_dir / "state.json").read_text())
    assert kept["state"] == "stopped" and kept["ended"] >= kept["started"]


def test_another_run_blocks_the_start_unless_allowed(settings):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _other_run(proc)
        sc = Scenario.model_validate(SCENARIO)
        pf = Supervisor(sc, settings, Catalog(settings)).preflight()
        assert any("another Hangar run is going on: 'duel'" in e and "savasan:t7" in e for e in pf.errors)
        pf = Supervisor(sc, settings, Catalog(settings), allow_parallel=True).preflight()
        assert not any("another Hangar run" in e for e in pf.errors)
        assert any("another Hangar run" in w for w in pf.warnings)
    finally:
        proc.kill()
        proc.wait()


def test_entries_of_ended_processes_are_dropped(settings):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    _other_run(proc)
    assert registry.active_runs() == []
    assert not list(registry.active_dir().glob("*.json"))

    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _other_run(live, proc_start=1)  # the pid was reused by another process
        assert registry.active_runs() == []
    finally:
        live.kill()
        live.wait()


def test_status_command(settings, capsys):
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _other_run(proc)
        assert main(["status", "--json"]) == 0
        [run] = json.loads(capsys.readouterr().out)
        assert run["owner"] == "savasan:t7"
        assert main(["status"]) == 0
        assert "savasan:t7" in capsys.readouterr().out
    finally:
        proc.kill()
        proc.wait()
