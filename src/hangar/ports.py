"""The port plan: every port a simulation uses, computed in one place.

ArduPilot fixes the per-instance layout (``-I i`` shifts all of its ports by
``10*i``), so the plan follows it and adds the MAVLink outputs and camera
streams on top. Outputs are templates: ``{i}`` is the vehicle index and
``{sysid}`` its MAVLink system id, with simple arithmetic allowed, e.g.
``udp:127.0.0.1:{15000+i}``.
"""

from __future__ import annotations

import ast
import operator
import re
import socket
from dataclasses import dataclass, field
from typing import Literal

from .scenario import Scenario

STRIDE = 10
SERIAL0_BASE = 5760  # MAVLink TCP, Hangar's router connects here
SERIAL1_BASE = 5762
SERIAL2_BASE = 5763
FDM_BASE = 9002  # SITL <-> Gazebo JSON physics
CAMERA_BASE = 5600

EndpointKind = Literal["udp", "udpin", "tcpin"]
_TEMPLATE = re.compile(r"\{([^{}]+)\}")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}


class PlanError(ValueError):
    pass


def _eval(expr: str, names: dict[str, int]) -> int:
    def walk(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        raise PlanError(f"unsupported expression in port template: {{{expr}}}")

    try:
        return walk(ast.parse(expr.strip(), mode="eval"))
    except SyntaxError as e:
        raise PlanError(f"bad port template: {{{expr}}}") from e


def expand_template(url: str, index: int, sysid: int) -> str:
    names = {"i": index, "sysid": sysid}
    return _TEMPLATE.sub(lambda m: str(_eval(m.group(1), names)), url)


@dataclass(frozen=True)
class Endpoint:
    """A MAVLink output.

    ``udp:host:port``    send to a listener (QGroundControl listens on 14550)
    ``udpin:host:port``  listen; reply to whoever sends to us
    ``tcpin:host:port``  accept TCP clients
    """

    kind: EndpointKind
    host: str
    port: int

    @classmethod
    def parse(cls, url: str) -> Endpoint:
        parts = url.strip().split(":")
        if len(parts) == 2:  # host:port means udp out, like MAVProxy
            parts = ["udp", *parts]
        if len(parts) != 3:
            raise PlanError(f"bad endpoint '{url}', expected kind:host:port")
        kind, host, port_s = parts
        kind = {"udpout": "udp", "tcp": "tcpin"}.get(kind, kind)
        if kind not in ("udp", "udpin", "tcpin"):
            raise PlanError(f"bad endpoint kind '{kind}' in '{url}' (udp, udpin, tcpin)")
        try:
            port = int(port_s)
        except ValueError as e:
            raise PlanError(f"bad port in '{url}'") from e
        if not 0 < port < 65536:
            raise PlanError(f"port out of range in '{url}'")
        return cls(kind, host or "127.0.0.1", port)  # type: ignore[arg-type]

    @property
    def binds_locally(self) -> bool:
        return self.kind in ("udpin", "tcpin")

    def __str__(self) -> str:
        return f"{self.kind}:{self.host}:{self.port}"


@dataclass
class VehiclePorts:
    index: int
    name: str
    sysid: int
    serial0: int
    fdm: int | None
    camera: int | None
    outputs: list[Endpoint] = field(default_factory=list)

    @property
    def serial1(self) -> int:
        return SERIAL1_BASE + STRIDE * self.index

    @property
    def serial2(self) -> int:
        return SERIAL2_BASE + STRIDE * self.index


@dataclass
class Binding:
    proto: Literal["tcp", "udp"]
    port: int
    owner: str


@dataclass
class PortPlan:
    vehicles: list[VehiclePorts]

    @classmethod
    def build(cls, scenario: Scenario, gazebo: dict[str, bool], cameras: dict[str, bool]) -> PortPlan:
        """``gazebo``/``cameras`` map vehicle name -> whether it uses Gazebo / a camera."""
        out: list[VehiclePorts] = []
        for i, v in enumerate(scenario.vehicles):
            name = v.name or f"vehicle{i}"
            sysid = i + 1
            urls = list(scenario.outputs) + list(v.outputs)
            endpoints: list[Endpoint] = []
            for u in urls:
                ep = Endpoint.parse(expand_template(u, i, sysid))
                if ep not in endpoints:
                    endpoints.append(ep)
            out.append(
                VehiclePorts(
                    index=i,
                    name=name,
                    sysid=sysid,
                    serial0=SERIAL0_BASE + STRIDE * i,
                    fdm=FDM_BASE + STRIDE * i if gazebo.get(name) else None,
                    camera=CAMERA_BASE + i if cameras.get(name) else None,
                    outputs=endpoints,
                )
            )
        return cls(out)

    def bindings(self) -> list[Binding]:
        """Every local port something in the simulation will bind."""
        b: list[Binding] = []
        for v in self.vehicles:
            b += [
                Binding("tcp", v.serial0, f"{v.name} SITL"),
                Binding("tcp", v.serial1, f"{v.name} SITL"),
                Binding("tcp", v.serial2, f"{v.name} SITL"),
            ]
            if v.fdm:
                b.append(Binding("udp", v.fdm, f"{v.name} Gazebo"))
            if v.camera:
                b.append(Binding("udp", v.camera, f"{v.name} camera"))
            for ep in v.outputs:
                if ep.binds_locally:
                    proto = "tcp" if ep.kind == "tcpin" else "udp"
                    b.append(Binding(proto, ep.port, f"{v.name} output"))
        return b

    def conflicts(self, check_system: bool = True) -> list[str]:
        problems: list[str] = []
        seen: dict[tuple[str, int], str] = {}
        for bind in self.bindings():
            key = (bind.proto, bind.port)
            if key in seen and seen[key] != bind.owner:
                problems.append(f"{bind.proto}/{bind.port}: {seen[key]} and {bind.owner}")
            seen.setdefault(key, bind.owner)
        if check_system:
            for (proto, port), owner in seen.items():
                if port_in_use(proto, port):
                    problems.append(f"{proto}/{port} ({owner}) is already in use by another program")
        return problems

    def rows(self) -> list[dict[str, str]]:
        return [
            {
                "index": str(v.index),
                "name": v.name,
                "sysid": str(v.sysid),
                "mavlink": f"tcp:{v.serial0}",
                "fdm": f"udp:{v.fdm}" if v.fdm else "-",
                "camera": f"udp:{v.camera}" if v.camera else "-",
                "outputs": ", ".join(str(e) for e in v.outputs) or "-",
            }
            for v in self.vehicles
        ]


def port_in_use(proto: str, port: int, host: str = "0.0.0.0") -> bool:
    kind = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as s:
        if proto == "tcp":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False
