import gzip
import json
import math

from hangar import track
from hangar.cli import main
from hangar.mavlink import Telemetry


def _tel(lat, lon, alt, mode="GUIDED", armed=False, yaw=90.0):
    return Telemetry(lat=lat, lon=lon, alt_rel=alt, mode=mode, armed=armed, roll=1.5, pitch=-2.0, yaw=yaw,
                     groundspeed=12.0)


def _record(run_dir):
    rec = track.TrackRecorder(run_dir, started=0)
    rec.now = iter([1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0]).__next__  # a known clock
    rec.sample("plane", _tel(-35.0, 149.0, 0.0))  # mode event at 1.0
    rec.sample("plane", _tel(-35.0, 149.0, 0.0, armed=True))  # arm event at 2.0
    rec.sample("plane", _tel(-35.0 + math.degrees(100 / track.EARTH), 149.0, 30.0, mode="AUTO", armed=True))
    rec.event("text", "plane", "Mission: 1 WP")
    rec.close()


def test_record_and_build(tmp_path):
    run = tmp_path / "20261108-120000-duel"
    run.mkdir()
    (run / "state.json").write_text(json.dumps({"world": "w", "home": {"lat": -35.0, "lon": 149.0, "alt": 584},
                                                "vehicles": [{"name": "plane", "model": "skywalker_x8"}]}))
    _record(run)
    assert track.has_track(run)

    data = track.build(run)
    assert data["version"] == 1 and data["world"] == "w" and data["origin"]["alt"] == 584
    t = data["tracks"]["plane"]
    assert t["model"] == "skywalker_x8" and t["t"] == [1.0, 2.0, 3.0]
    assert t["n"][0] == 0 and abs(t["n"][2] - 100) < 0.05 and t["e"][2] == 0
    assert t["h"] == [0.0, 0.0, 30.0] and t["yaw"][0] == 90.0 and t["roll"][0] == 1.5
    assert t["fields"]["mode"] == ["GUIDED", "GUIDED", "AUTO"] and t["fields"]["armed"][1] == "armed"
    kinds = [(e["t"], e["kind"]) for e in data["events"]]
    assert kinds == [(1.0, "mode"), (2.0, "arm"), (3.0, "mode"), (4.0, "text")]
    # every track column has the same length
    assert len({len(t[c]) for c in ("t", "n", "e", "h", "yaw", "roll", "pitch")}) == 1


def test_replay_command(tmp_path, monkeypatch, capsys):
    from hangar import paths

    run = paths.runs_dir() / "20261108-120000-duel"
    run.mkdir(parents=True)
    _record(run)
    assert main(["replay"]) == 0
    data = json.loads(gzip.decompress((run / "replay.json.gz").read_bytes()))
    assert list(data["tracks"]) == ["plane"] and data["origin"]["lat"] == -35.0
    out = tmp_path / "x.json"
    assert main(["replay", "duel", "-o", str(out)]) == 0
    assert json.loads(out.read_text())["name"] == "20261108-120000-duel"
    assert main(["replay", "nothing-like-this"]) == 1
