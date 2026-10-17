import socket

import pytest

from hangar.ports import Endpoint, PlanError, PortPlan, expand_template
from hangar.scenario import Scenario


def scenario(**kw) -> Scenario:
    data = {"vehicles": [{"model": "sitl:plane"}, {"model": "sitl:copter"}, {"model": "sitl:plane"}]}
    data.update(kw)
    return Scenario.model_validate(data)


def test_template_arithmetic():
    assert expand_template("udp:127.0.0.1:{15000+i}", 2, 3) == "udp:127.0.0.1:15002"
    assert expand_template("udp:h:{14550 + 10*i}", 1, 2) == "udp:h:14560"
    assert expand_template("udp:h:{sysid}", 0, 7) == "udp:h:7"
    assert expand_template("udp:h:14550", 5, 6) == "udp:h:14550"


@pytest.mark.parametrize("bad", ["udp:h:{__import__('os')}", "udp:h:{i/2}", "udp:h:{x}", "udp:h:{1+}"])
def test_template_rejects_anything_else(bad):
    with pytest.raises(PlanError):
        expand_template(bad, 0, 1)


def test_endpoint_parse():
    assert Endpoint.parse("udp:127.0.0.1:14550") == Endpoint("udp", "127.0.0.1", 14550)
    assert Endpoint.parse("127.0.0.1:14550") == Endpoint("udp", "127.0.0.1", 14550)
    assert Endpoint.parse("udpout:10.0.0.2:14550").kind == "udp"
    assert Endpoint.parse("tcp:0.0.0.0:6000") == Endpoint("tcpin", "0.0.0.0", 6000)
    for bad in ("serial:/dev/ttyUSB0:57600", "udp:h:99999", "udp:h:abc", "nonsense"):
        with pytest.raises(PlanError):
            Endpoint.parse(bad)


def test_plan_follows_ardupilot_instance_layout():
    plan = PortPlan.build(scenario(), gazebo={"plane": True}, cameras={"plane": True})
    v0, v1, v2 = plan.vehicles
    assert (v0.sysid, v0.serial0, v0.fdm, v0.camera) == (1, 5760, 9002, 5600)
    assert (v1.sysid, v1.serial0, v1.fdm, v1.camera) == (2, 5770, None, None)
    assert v2.name == "plane_2" and v2.serial0 == 5780


def test_outputs_merge_and_dedupe():
    sc = scenario(outputs=["udp:127.0.0.1:14550", "udp:127.0.0.1:{15000+i}"])
    sc.vehicles[1].outputs = ["udp:127.0.0.1:14550", "tcpin:0.0.0.0:{6000+i}"]
    plan = PortPlan.build(sc, {}, {})
    assert [str(e) for e in plan.vehicles[1].outputs] == [
        "udp:127.0.0.1:14550", "udp:127.0.0.1:15001", "tcpin:0.0.0.0:6001",
    ]


def test_conflict_inside_plan():
    sc = scenario()
    sc.vehicles[0].outputs = ["udpin:0.0.0.0:9012"]  # collides with vehicle 1's FDM port
    plan = PortPlan.build(sc, gazebo={"copter": True}, cameras={})
    problems = plan.conflicts(check_system=False)
    assert any("9012" in p for p in problems)


def test_conflict_with_running_program():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        s.listen()
        busy = s.getsockname()[1]
        sc = scenario()
        sc.vehicles[0].outputs = [f"tcpin:0.0.0.0:{busy}"]
        plan = PortPlan.build(sc, {}, {})
        assert any(str(busy) in p for p in plan.conflicts())
