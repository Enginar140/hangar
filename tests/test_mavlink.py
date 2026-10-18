"""VehicleLink against a fake SITL: routing, telemetry and command acks."""

import asyncio
import socket

from pymavlink.dialects.v20 import ardupilotmega as mav

from hangar.mavlink import VehicleLink
from hangar.ports import Endpoint


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSitl:
    """Speaks just enough MAVLink: heartbeat, position, acks every command."""

    def __init__(self, sysid: int):
        self.enc = mav.MAVLink(None, srcSystem=sysid, srcComponent=1)
        self.dec = mav.MAVLink(None)
        self.dec.robust_parsing = True
        self.received: list = []
        self.mode = 0
        self.params = {"ARMING_CHECK": 1.0, "WPNAV_SPEED": 500.0, "SIM_WIND_SPD": 0.0}
        self.drop_first_list = {1}  # indexes lost in the first full-list answer
        self.positions: list = []

    async def handle(self, reader, writer):
        async def talk():
            while True:
                writer.write(self.enc.heartbeat_encode(
                    mav.MAV_TYPE_QUADROTOR, mav.MAV_AUTOPILOT_ARDUPILOTMEGA, 1, self.mode, 3).pack(self.enc))
                writer.write(self.enc.global_position_int_encode(
                    0, 400000000, 290000000, 100000, 12500, 0, 0, 0, 9000).pack(self.enc))
                await asyncio.sleep(0.1)

        task = asyncio.create_task(talk())
        try:
            while data := await reader.read(4096):
                for m in self.dec.parse_buffer(data) or []:
                    self.received.append(m)
                    kind = m.get_type()
                    if kind == "COMMAND_LONG" and m.command == mav.MAV_CMD_REQUEST_MESSAGE:
                        writer.write(self.enc.autopilot_version_encode(
                            0, 4 << 24 | 7 << 16 | 2 << 8 | 255, 0, 0, 0, b"\0" * 8, b"\0" * 8, b"\0" * 8,
                            0, 0, 0).pack(self.enc))
                    elif kind == "COMMAND_LONG":
                        if m.command == mav.MAV_CMD_DO_SET_MODE:
                            self.mode = int(m.param2)
                        writer.write(self.enc.command_ack_encode(m.command, 0).pack(self.enc))
                    elif kind == "COMMAND_INT":
                        self.positions.append((m.x, m.y, m.z, m.frame))
                        writer.write(self.enc.command_ack_encode(m.command, 0).pack(self.enc))
                    elif kind == "PARAM_REQUEST_LIST":
                        for i in range(len(self.params)):
                            if i not in self.drop_first_list:
                                self.send_param(writer, i)
                        self.drop_first_list = set()
                    elif kind == "PARAM_REQUEST_READ":
                        names = list(self.params)
                        pid = m.param_id.rstrip("\x00") if isinstance(m.param_id, str) else m.param_id
                        if m.param_index >= 0:
                            self.send_param(writer, m.param_index)
                        elif pid in self.params:
                            self.send_param(writer, names.index(pid))
                    elif kind == "PARAM_SET":
                        pid = m.param_id.rstrip("\x00") if isinstance(m.param_id, str) else m.param_id
                        self.params[pid] = m.param_value
                        self.send_param(writer, list(self.params).index(pid))
        finally:
            task.cancel()

    def send_param(self, writer, i: int) -> None:
        name = list(self.params)[i]
        ptype = mav.MAV_PARAM_TYPE_INT8 if name == "ARMING_CHECK" else mav.MAV_PARAM_TYPE_REAL32
        writer.write(self.enc.param_value_encode(
            name.encode(), self.params[name], ptype, len(self.params), i).pack(self.enc))


async def test_link_routes_and_commands():
    sitl = FakeSitl(sysid=3)
    port = free_port()
    server = await asyncio.start_server(sitl.handle, "127.0.0.1", port)

    # a "ground station" listening where the link sends its udp output
    gcs_port = free_port(socket.SOCK_DGRAM)
    got: list[bytes] = []
    loop = asyncio.get_running_loop()

    class Gcs(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            got.append(data)
            gcs = mav.MAVLink(None, srcSystem=200, srcComponent=1)
            hb = gcs.heartbeat_encode(mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            self.transport.sendto(hb.pack(gcs), addr)  # must travel back to the vehicle

        def connection_made(self, transport):
            self.transport = transport

    gcs_transport, _ = await loop.create_datagram_endpoint(Gcs, local_addr=("127.0.0.1", gcs_port))

    link = VehicleLink("v", 3, port, [Endpoint("udp", "127.0.0.1", gcs_port)])
    await link.start()
    try:
        assert await link.wait_connected(5)
        await asyncio.sleep(0.3)
        t = link.telemetry
        assert t.armed is False and t.mode == "STABILIZE"
        assert t.lat == 40.0 and t.lon == 29.0 and t.alt_rel == 12.5 and t.heading == 90.0
        assert got, "vehicle traffic was not routed to the udp output"

        assert await link.run("mode guided") == "accepted"
        await asyncio.sleep(0.3)
        assert link.telemetry.mode == "GUIDED"
        assert "GUIDED" in link.mode_names()
        kinds = {m.get_type() for m in sitl.received}
        assert {"HEARTBEAT", "REQUEST_DATA_STREAM", "COMMAND_LONG"} <= kinds
        assert any(m.get_srcSystem() == 200 for m in sitl.received), "GCS traffic not routed to vehicle"
    finally:
        await link.stop()
        gcs_transport.close()
        server.close()


async def test_parameters_and_goto():
    sitl = FakeSitl(sysid=2)
    port = free_port()
    server = await asyncio.start_server(sitl.handle, "127.0.0.1", port)
    link = VehicleLink("v", 2, port, [])
    await link.start()
    try:
        assert await link.wait_connected(5)
        params = await link.fetch_params(timeout=10)  # one value is lost and asked for again
        assert params == sitl.params and link.params_complete
        assert link.telemetry.version == "4.7.2"
        assert link.param_is_int("ARMING_CHECK") and not link.param_is_int("WPNAV_SPEED")
        before = link.params_changed
        assert await link.run("param set wpnav_speed 800") == "WPNAV_SPEED = 800"
        assert link.params["WPNAV_SPEED"] == 800 and link.params_changed > before
        assert await link.run("param get SIM_WIND_SPD") == "SIM_WIND_SPD = 0"
        assert (await link.run("goto 40.001 29.002 25")).startswith("going to 40.001000, 29.002000")
        x, y, z, frame = sitl.positions[-1]
        assert (x, y, z, frame) == (400010000, 290020000, 25.0, mav.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT)
        await asyncio.sleep(0.3)
        await link.run("goto 40 29")  # keeps the current altitude (12.5 m)
        assert sitl.positions[-1][2] == 12.5
    finally:
        await link.stop()
        server.close()


async def test_outputs_change_while_running():
    sitl = FakeSitl(sysid=1)
    port = free_port()
    server = await asyncio.start_server(sitl.handle, "127.0.0.1", port)
    gcs_port = free_port(socket.SOCK_DGRAM)
    got: list[bytes] = []

    class Gcs(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            got.append(data)

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        Gcs, local_addr=("127.0.0.1", gcs_port))
    link = VehicleLink("v", 1, port, [])
    await link.start()
    try:
        assert await link.wait_connected(5)
        await asyncio.sleep(0.3)
        assert not got
        ep = Endpoint("udp", "127.0.0.1", gcs_port)
        await link.set_outputs([ep])
        await asyncio.sleep(0.3)
        assert got, "new output did not receive traffic"
        await link.set_outputs([])
        await asyncio.sleep(0.15)
        got.clear()
        await asyncio.sleep(0.3)
        assert not got, "removed output still receives traffic"
    finally:
        await link.stop()
        transport.close()
        server.close()


async def test_video_relay_copies_to_every_destination():
    from hangar.camera import VideoRelay

    loop = asyncio.get_running_loop()
    got: dict[int, list[bytes]] = {}

    class Rx(asyncio.DatagramProtocol):
        def __init__(self, port):
            self.port = port

        def datagram_received(self, data, addr):
            got.setdefault(self.port, []).append(data)

    ports = [free_port(socket.SOCK_DGRAM) for _ in range(3)]
    receivers = [(await loop.create_datagram_endpoint(lambda p=p: Rx(p), local_addr=("127.0.0.1", p)))[0]
                 for p in ports]
    listen = free_port(socket.SOCK_DGRAM)
    relay = VideoRelay(listen, [("127.0.0.1", ports[0]), ("127.0.0.1", ports[1])])
    await relay.start()
    send, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=("127.0.0.1", listen))
    try:
        send.sendto(b"\x80rtp-1")
        await asyncio.sleep(0.2)
        assert got.get(ports[0]) == [b"\x80rtp-1"] and got.get(ports[1]) == [b"\x80rtp-1"]
        relay.set_destinations([("127.0.0.1", ports[1]), ("127.0.0.1", ports[2])])  # while running
        send.sendto(b"\x80rtp-2")
        await asyncio.sleep(0.2)
        assert got[ports[0]] == [b"\x80rtp-1"]  # dropped destination gets nothing new
        assert got[ports[1]][-1] == b"\x80rtp-2" and got[ports[2]] == [b"\x80rtp-2"]
        assert relay.packets == 2
    finally:
        send.close()
        relay.close()
        for r in receivers:
            r.close()
