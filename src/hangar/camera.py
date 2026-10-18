"""Camera streams from ArduPilot's GstCameraPlugin.

The plugin sends H.264 over RTP/UDP once its enable topic receives ``true``.
From there Hangar can feed a v4l2loopback device (so OpenCV, ROS or any
webcam app can read it) or open a viewer window on demand.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Callable

RTP_CAPS = "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264"


class VideoRelay(asyncio.DatagramProtocol):
    """Copies a camera's RTP packets, untouched, to every destination.

    The camera plugin can send to one address only; this lets one stream reach
    this computer's readers and other machines at the same time, and the
    destinations can change while it runs."""

    def __init__(self, listen_port: int, destinations: list[tuple[str, int]],
                 on_error: Callable[[str], None] | None = None):
        self.listen_port = listen_port
        self.destinations: list[tuple[str, int]] = []
        self.on_error = on_error
        self.packets = 0
        self.errors = 0
        self._resolved: dict[tuple[str, int], tuple[str, int] | None] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._out: socket.socket | None = None
        self.set_destinations(destinations)

    async def start(self) -> None:
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.setblocking(False)
        out.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # stay on the local network
        out.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)  # this computer can join too
        self._out = out
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=("127.0.0.1", self.listen_port)
        )

    def set_destinations(self, destinations: list[tuple[str, int]]) -> None:
        self.destinations = list(dict.fromkeys(destinations))
        for dest in self.destinations:
            if dest not in self._resolved:
                self._resolved[dest] = self._resolve(dest)

    def _resolve(self, dest: tuple[str, int]) -> tuple[str, int] | None:
        host, port = dest
        try:
            ipaddress.ip_address(host)
            return host, port
        except ValueError:
            pass
        try:
            return socket.gethostbyname(host), port
        except OSError as e:
            if self.on_error:
                self.on_error(f"camera output {host}:{port}: {e}")
            return None

    def datagram_received(self, data: bytes, addr) -> None:
        self.packets += 1
        if not self._out:
            return
        for dest in self.destinations:
            target = self._resolved.get(dest)
            if target is None:
                continue
            try:
                self._out.sendto(data, target)
            except BlockingIOError:
                pass  # a full send buffer drops one packet, like the network would
            except OSError as e:
                self.errors += 1
                if self.errors in (1, 100, 10000) and self.on_error:
                    self.on_error(f"camera output {dest[0]}:{dest[1]}: {e}")

    def close(self) -> None:
        if self._transport:
            self._transport.close()
        if self._out:
            self._out.close()


async def _run(argv: list[str], env: dict[str, str], timeout: float = 5.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode or 0, out.decode(errors="replace")


async def enable_streaming(instance: str, env: dict[str, str], timeout: float = 30.0) -> str | None:
    """Find the model's enable_streaming topic and switch it on. Returns the topic."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, out = await _run(["gz", "topic", "-l"], env)
        if code == 0:
            topics = [t.strip() for t in out.splitlines()]
            hits = [t for t in topics if f"/model/{instance}/" in t and t.endswith("enable_streaming")]
            if hits:
                topic = sorted(hits, key=lambda t: ("camera" not in t, t))[0]
                code, out = await _run(
                    ["gz", "topic", "-t", topic, "-m", "gz.msgs.Boolean", "-p", "data: true"], env
                )
                return topic if code == 0 else None
        await asyncio.sleep(1.0)
    return None


def v4l2_bridge_argv(port: int, device: str) -> list[str]:
    return [
        "gst-launch-1.0", "-q",
        "udpsrc", f"port={port}", f"caps={RTP_CAPS}",
        "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
        "!", "videoconvert", "!", "video/x-raw,format=YUY2",
        "!", "v4l2sink", f"device={device}", "sync=false",
    ]


def viewer_argv(port: int, device: str | None = None) -> list[str]:
    """A window showing the stream. With a v4l2 bridge running the UDP port is
    taken, so the viewer reads the loopback device instead."""
    if device:
        source = ["v4l2src", f"device={device}"]
    else:
        source = [
            "udpsrc", f"port={port}", f"caps={RTP_CAPS}",
            "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
        ]
    return ["gst-launch-1.0", "-q", *source, "!", "videoconvert", "!", "autovideosink", "sync=false"]


# --- how to use a stream (shown in the UI and by `hangar camera`) ------------


def gst_receive(port: int) -> str:
    return (f'udpsrc port={port} caps="{RTP_CAPS}" ! rtph264depay ! h264parse ! avdec_h264 '
            "! videoconvert")


def usage(port: int, width: int, height: int, device: str | None = None) -> dict[str, str]:
    """Ready-to-paste ways to read a camera stream, filled in for this camera."""
    py = f'''# pip install numpy opencv-python   (system: sudo apt install gstreamer1.0-libav)
import subprocess
import cv2
import numpy as np

W, H = {width}, {height}
gst = subprocess.Popen(
    ["gst-launch-1.0", "-q", "udpsrc", "port={port}",
     "caps={RTP_CAPS}",
     "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
     "!", "videoconvert", "!", "videoscale",
     "!", f"video/x-raw,format=BGR,width={{W}},height={{H}}", "!", "fdsink"],
    stdout=subprocess.PIPE, bufsize=W * H * 3)

while True:
    raw = gst.stdout.read(W * H * 3)
    if len(raw) < W * H * 3:
        break
    frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)  # BGR, like cv2
    # ... your image processing here ...
    cv2.imshow("camera {port}", frame)
    if cv2.waitKey(1) == 27:  # Esc
        break
gst.terminate()
'''
    out = {
        "python": py,
        "opencv-gstreamer": (
            "# only if your OpenCV is built with GStreamer (pip's opencv-python is not)\n"
            "import cv2\n"
            f"cap = cv2.VideoCapture('{gst_receive(port)} ! video/x-raw,format=BGR ! appsink drop=true', "
            "cv2.CAP_GSTREAMER)\n"
            "ok, frame = cap.read()\n"
        ),
        "gst-launch": f"gst-launch-1.0 {gst_receive(port)} ! autovideosink sync=false",
    }
    if device:
        out["v4l2"] = f'''# {device} behaves like a webcam: OpenCV, ROS (usb_cam / v4l2_camera), OBS...
# pip install opencv-python
import cv2

cap = cv2.VideoCapture("{device}")
while True:
    ok, frame = cap.read()  # BGR numpy array
    if not ok:
        break
    # ... your image processing here ...
    cv2.imshow("{device}", frame)
    if cv2.waitKey(1) == 27:  # Esc
        break
cap.release()
'''
    return out
