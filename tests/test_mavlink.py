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
                    if m.get_type() == "COMMAND_LONG":
                        if m.command == mav.MAV_CMD_DO_SET_MODE:
                            self.mode = int(m.param2)
                        writer.write(self.enc.command_ack_encode(m.command, 0).pack(self.enc))
        finally:
            task.cancel()


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
