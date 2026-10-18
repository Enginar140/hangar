"""One MAVLink link per vehicle: router, telemetry and commands in one place.

Hangar connects to each SITL's SERIAL0 TCP port and fans the byte stream out
to every configured output (and everything coming back from an output goes to
the vehicle). This replaces the one-MAVProxy-per-vehicle setup: there is a
single process, the ports are the ones in the PortPlan, and the same link
feeds the TUI with telemetry and carries its commands.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mav

from .ports import Endpoint

HANGAR_SYSID = 254
HANGAR_COMPID = mav.MAV_COMP_ID_MISSIONPLANNER
HEARTBEAT_TIMEOUT = 3.0
ACK_TIMEOUT = 4.0
PEER_TIMEOUT = 10.0

TextCallback = Callable[[str, str, str], None]  # (vehicle, level, text)

_SEVERITY = {
    mav.MAV_SEVERITY_EMERGENCY: "error",
    mav.MAV_SEVERITY_ALERT: "error",
    mav.MAV_SEVERITY_CRITICAL: "error",
    mav.MAV_SEVERITY_ERROR: "error",
    mav.MAV_SEVERITY_WARNING: "warning",
}
_RESULT = {
    mav.MAV_RESULT_ACCEPTED: "accepted",
    mav.MAV_RESULT_TEMPORARILY_REJECTED: "temporarily rejected",
    mav.MAV_RESULT_DENIED: "denied",
    mav.MAV_RESULT_UNSUPPORTED: "unsupported",
    mav.MAV_RESULT_FAILED: "failed",
    mav.MAV_RESULT_IN_PROGRESS: "in progress",
}
_GPS_FIX = {0: "no GPS", 1: "no fix", 2: "2D", 3: "3D", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}


class CommandError(Exception):
    pass


def _mode_map(mav_type: int | None) -> dict[int, str]:
    if mav_type is None:
        return {}
    return mavutil.mode_mapping_bynumber(mav_type) or {}


@dataclass
class Telemetry:
    connected: bool = False
    last_heartbeat: float = 0.0
    mav_type: int | None = None
    armed: bool = False
    mode: str = "-"
    lat: float | None = None
    lon: float | None = None
    alt_rel: float | None = None
    heading: float | None = None
    groundspeed: float | None = None
    airspeed: float | None = None
    climb: float | None = None
    throttle: int | None = None
    battery: int | None = None
    voltage: float | None = None
    gps_fix: int = 0
    satellites: int = 0
    ready: bool | None = None  # pre-arm checks passing
    rx_messages: int = 0

    @property
    def alive(self) -> bool:
        return self.connected and time.monotonic() - self.last_heartbeat < HEARTBEAT_TIMEOUT

    @property
    def gps_text(self) -> str:
        return f"{_GPS_FIX.get(self.gps_fix, '?')} ({self.satellites})"


# --- outputs -------------------------------------------------------------


class _Output:
    def __init__(self, endpoint: Endpoint, to_vehicle: Callable[[bytes], None]):
        self.endpoint = endpoint
        self.to_vehicle = to_vehicle

    async def open(self) -> None: ...

    def send(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: _UdpOutput | _UdpInput):
        self.owner = owner

    def datagram_received(self, data: bytes, addr) -> None:
        self.owner.received(data, addr)

    def error_received(self, exc: Exception) -> None:
        pass  # nobody listening yet (ICMP port unreachable); keep sending


class _UdpOutput(_Output):
    """Send to host:port, accept the replies (like MAVProxy's --out=udp:)."""

    transport: asyncio.DatagramTransport | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self), remote_addr=(self.endpoint.host, self.endpoint.port)
        )

    def received(self, data: bytes, addr) -> None:
        self.to_vehicle(data)

    def send(self, data: bytes) -> None:
        if self.transport:
            self.transport.sendto(data)

    def close(self) -> None:
        if self.transport:
            self.transport.close()


class _UdpInput(_Output):
    """Listen on host:port and talk to whoever sent to us recently."""

    transport: asyncio.DatagramTransport | None = None

    async def open(self) -> None:
        self.peers: dict[tuple, float] = {}
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self), local_addr=(self.endpoint.host, self.endpoint.port)
        )

    def received(self, data: bytes, addr) -> None:
        self.peers[addr] = time.monotonic()
        self.to_vehicle(data)

    def send(self, data: bytes) -> None:
        if not self.transport:
            return
        now = time.monotonic()
        for addr, seen in list(self.peers.items()):
            if now - seen > PEER_TIMEOUT:
                del self.peers[addr]
            else:
                self.transport.sendto(data, addr)

    def close(self) -> None:
        if self.transport:
            self.transport.close()


class _TcpInput(_Output):
    server: asyncio.Server | None = None

    async def open(self) -> None:
        self.clients: set[asyncio.StreamWriter] = set()
        self.server = await asyncio.start_server(self._client, self.endpoint.host, self.endpoint.port)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            while data := await reader.read(4096):
                self.to_vehicle(data)
        except (ConnectionError, OSError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    def send(self, data: bytes) -> None:
        for w in list(self.clients):
            if w.is_closing():
                self.clients.discard(w)
            else:
                w.write(data)

    def close(self) -> None:
        for w in self.clients:
            w.close()
        if self.server:
            self.server.close()


_OUTPUTS = {"udp": _UdpOutput, "udpin": _UdpInput, "tcpin": _TcpInput}


# --- link ----------------------------------------------------------------


class VehicleLink:
    def __init__(
        self,
        name: str,
        sysid: int,
        port: int,
        outputs: list[Endpoint],
        on_text: TextCallback | None = None,
        host: str = "127.0.0.1",
    ):
        self.name = name
        self.sysid = sysid
        self.host = host
        self.port = port
        self.endpoints = outputs
        self.on_text = on_text
        self.telemetry = Telemetry()
        self._mav = mav.MAVLink(None, srcSystem=HANGAR_SYSID, srcComponent=HANGAR_COMPID)
        self._mav.robust_parsing = True
        self._writer: asyncio.StreamWriter | None = None
        self._outputs: list[_Output] = []
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._acks: dict[int, asyncio.Future] = {}
        self._param_waits: dict[str, asyncio.Future] = {}
        self._connected = asyncio.Event()

    # --- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        for ep in self.endpoints:
            await self._open_output(ep)
        self._task = asyncio.create_task(self._run(), name=f"link-{self.name}")

    async def _open_output(self, ep: Endpoint) -> bool:
        out = _OUTPUTS[ep.kind](ep, self._to_vehicle)
        try:
            await out.open()
        except OSError as e:
            self._text("error", f"output {ep}: {e}")
            return False
        self._outputs.append(out)
        return True

    async def set_outputs(self, endpoints: list[Endpoint]) -> None:
        """Change outputs while running: open the new ones, close the dropped ones."""
        for out in [o for o in self._outputs if o.endpoint not in endpoints]:
            out.close()
            self._outputs.remove(out)
            self._text("info", f"output removed: {out.endpoint}")
        current = {o.endpoint for o in self._outputs}
        for ep in endpoints:
            if ep not in current and await self._open_output(ep):
                self._text("info", f"output added: {ep}")
        self.endpoints = list(endpoints)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for out in self._outputs:
            out.close()
        self._outputs.clear()
        self.telemetry.connected = False

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def _run(self) -> None:
        announced_wait = False
        while not self._stopping:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError:
                if not announced_wait:
                    self._text("info", f"waiting for SITL on tcp:{self.port}")
                    announced_wait = True
                await asyncio.sleep(0.5)
                continue
            self._writer = writer
            self._text("info", f"connected to tcp:{self.port}")
            hb = asyncio.create_task(self._heartbeat_loop())
            try:
                while data := await reader.read(65536):
                    self._from_vehicle(data)
            except (ConnectionError, OSError):
                pass
            finally:
                hb.cancel()
                self._writer = None
                self.telemetry.connected = False
                self._connected.clear()
                writer.close()
            if not self._stopping:
                self._text("warning", "connection to SITL lost, retrying")
                announced_wait = False
                await asyncio.sleep(1.0)

    async def _heartbeat_loop(self) -> None:
        while True:
            self._send(self._mav.heartbeat_encode(mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, 0))
            await asyncio.sleep(1.0)

    # --- traffic ---------------------------------------------------------

    def _to_vehicle(self, data: bytes) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.write(data)

    def _send(self, msg) -> None:
        self._to_vehicle(msg.pack(self._mav))

    def _from_vehicle(self, data: bytes) -> None:
        for out in self._outputs:
            out.send(data)
        try:
            msgs = self._mav.parse_buffer(data) or []
        except Exception:  # malformed frame; the bytes were still routed
            return
        for m in msgs:
            self._handle(m)

    def _handle(self, m) -> None:
        if m.get_srcSystem() != self.sysid:
            return
        t = self.telemetry
        t.rx_messages += 1
        kind = m.get_type()
        if kind == "HEARTBEAT":
            if m.get_srcComponent() != 1 or m.type == mav.MAV_TYPE_GCS:
                return
            t.last_heartbeat = time.monotonic()
            t.mav_type = m.type
            t.armed = bool(m.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED)
            t.mode = _mode_map(m.type).get(m.custom_mode, str(m.custom_mode))
            if not t.connected:
                t.connected = True
                self._connected.set()
                # ask for telemetry as soon as the vehicle is there (again, after a reconnect)
                self._send(self._mav.request_data_stream_encode(self.sysid, 1, mav.MAV_DATA_STREAM_ALL, 4, 1))
        elif kind == "GLOBAL_POSITION_INT":
            t.lat, t.lon = m.lat / 1e7, m.lon / 1e7
            t.alt_rel = m.relative_alt / 1000.0
            if m.hdg != 65535:
                t.heading = m.hdg / 100.0
        elif kind == "VFR_HUD":
            t.airspeed, t.groundspeed, t.climb, t.throttle = m.airspeed, m.groundspeed, m.climb, m.throttle
        elif kind == "SYS_STATUS":
            t.battery = m.battery_remaining if m.battery_remaining >= 0 else None
            t.voltage = m.voltage_battery / 1000.0 if m.voltage_battery != 65535 else None
            prearm = mav.MAV_SYS_STATUS_PREARM_CHECK
            if m.onboard_control_sensors_present & prearm:
                t.ready = bool(m.onboard_control_sensors_health & prearm)
        elif kind == "GPS_RAW_INT":
            t.gps_fix, t.satellites = m.fix_type, m.satellites_visible
        elif kind == "STATUSTEXT":
            self._text(_SEVERITY.get(m.severity, "info"), m.text.rstrip("\x00"))
        elif kind == "COMMAND_ACK":
            fut = self._acks.pop(m.command, None)
            if fut and not fut.done():
                fut.set_result(m.result)
        elif kind == "PARAM_VALUE":
            pid = m.param_id.rstrip("\x00") if isinstance(m.param_id, str) else m.param_id
            fut = self._param_waits.pop(pid, None)
            if fut and not fut.done():
                fut.set_result(m.param_value)

    def _text(self, level: str, text: str) -> None:
        if self.on_text:
            self.on_text(self.name, level, text)

    # --- commands --------------------------------------------------------

    def _require_link(self) -> None:
        if not self._writer:
            raise CommandError("not connected")

    async def command(self, command: int, *params: float) -> str:
        self._require_link()
        p = list(params) + [0.0] * (7 - len(params))
        fut = asyncio.get_running_loop().create_future()
        self._acks[command] = fut
        self._send(self._mav.command_long_encode(self.sysid, 1, command, 0, *p))
        try:
            result = await asyncio.wait_for(fut, ACK_TIMEOUT)
        except TimeoutError as e:
            self._acks.pop(command, None)
            raise CommandError("no response from vehicle") from e
        text = _RESULT.get(result, str(result))
        if result not in (mav.MAV_RESULT_ACCEPTED, mav.MAV_RESULT_IN_PROGRESS):
            raise CommandError(text)
        return text

    def mode_names(self) -> list[str]:
        return sorted(_mode_map(self.telemetry.mav_type).values())

    async def set_mode(self, name: str) -> str:
        mapping = _mode_map(self.telemetry.mav_type)
        if not mapping:
            raise CommandError("vehicle type not known yet")
        by_name = {v: k for k, v in mapping.items()}
        mode = by_name.get(name.upper())
        if mode is None:
            raise CommandError(f"unknown mode {name}; available: {', '.join(sorted(by_name))}")
        return await self.command(
            mav.MAV_CMD_DO_SET_MODE, mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode
        )

    async def arm(self, force: bool = False) -> str:
        return await self.command(mav.MAV_CMD_COMPONENT_ARM_DISARM, 1, 21196 if force else 0)

    async def disarm(self, force: bool = False) -> str:
        return await self.command(mav.MAV_CMD_COMPONENT_ARM_DISARM, 0, 21196 if force else 0)

    async def set_param(self, name: str, value: float) -> float:
        self._require_link()
        name = name.upper()
        fut = asyncio.get_running_loop().create_future()
        self._param_waits[name] = fut
        self._send(
            self._mav.param_set_encode(
                self.sysid, 1, name.encode(), float(value), mav.MAV_PARAM_TYPE_REAL32
            )
        )
        try:
            return await asyncio.wait_for(fut, ACK_TIMEOUT)
        except TimeoutError as e:
            self._param_waits.pop(name, None)
            raise CommandError(f"no PARAM_VALUE for {name}") from e

    async def takeoff(self, altitude: float) -> str:
        t = self.telemetry.mav_type
        if t == mav.MAV_TYPE_FIXED_WING or t in _VTOL_TYPES:
            steps = [
                ("TKOFF_ALT", self.set_param("TKOFF_ALT", altitude)),
                ("mode TAKEOFF", self.set_mode("TAKEOFF")),
                ("arm", self.arm()),
            ]
        elif t in _COPTER_TYPES:
            steps = [
                ("mode GUIDED", self.set_mode("GUIDED")),
                ("arm", self.arm()),
                ("takeoff", self._copter_takeoff(altitude)),
            ]
        else:
            raise CommandError("takeoff is only for aircraft")
        for i, (label, step) in enumerate(steps):
            try:
                await step
            except CommandError as e:
                for _, rest in steps[i + 1 :]:
                    rest.close()  # never awaited
                raise CommandError(f"{label}: {e}") from e
        return f"takeoff to {altitude:g} m"

    async def _copter_takeoff(self, altitude: float) -> str:
        await asyncio.sleep(0.5)
        return await self.command(mav.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, altitude)

    async def rtl(self) -> str:
        t = self.telemetry.mav_type
        return await self.set_mode("RTL" if t not in _VTOL_TYPES else "QRTL")

    async def land(self) -> str:
        t = self.telemetry.mav_type
        if t in _COPTER_TYPES:
            return await self.set_mode("LAND")
        if t in _VTOL_TYPES:
            return await self.set_mode("QLAND")
        raise CommandError("use RTL or a landing mission for this vehicle")

    async def run(self, line: str) -> str:
        """Text command interface shared by the TUI and scripts."""
        words = line.strip().split()
        if not words:
            return ""
        cmd, args = words[0].lower(), words[1:]
        force = "force" in (a.lower() for a in args)
        try:
            if cmd == "arm":
                return await self.arm(force)
            if cmd == "disarm":
                return await self.disarm(force)
            if cmd == "mode" and args:
                return await self.set_mode(args[0])
            if cmd == "takeoff":
                return await self.takeoff(float(args[0]) if args else 30.0)
            if cmd == "rtl":
                return await self.rtl()
            if cmd == "land":
                return await self.land()
            if cmd in ("param", "set") and len(args) >= 2:
                if args[0].lower() == "set":
                    args = args[1:]
                value = await self.set_param(args[0], float(args[1]))
                return f"{args[0].upper()} = {value:g}"
            if cmd in self.mode_names() or cmd.upper() in self.mode_names():
                return await self.set_mode(cmd)
        except ValueError as e:
            raise CommandError(f"bad number: {e}") from e
        raise CommandError(
            "commands: arm [force], disarm [force], mode <NAME>, <MODE>, takeoff [alt], rtl, land, "
            "param set <NAME> <VALUE>"
        )


_COPTER_TYPES = {
    mav.MAV_TYPE_QUADROTOR,
    mav.MAV_TYPE_HEXAROTOR,
    mav.MAV_TYPE_OCTOROTOR,
    mav.MAV_TYPE_TRICOPTER,
    mav.MAV_TYPE_COAXIAL,
    mav.MAV_TYPE_HELICOPTER,
    mav.MAV_TYPE_DODECAROTOR,
    mav.MAV_TYPE_DECAROTOR,
}
_VTOL_TYPES = {
    getattr(mav, n)
    for n in dir(mav)
    if n.startswith("MAV_TYPE_VTOL")
}
