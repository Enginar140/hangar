"""Camera streams from ArduPilot's GstCameraPlugin.

The plugin sends H.264 over RTP/UDP once its enable topic receives ``true``.
From there Hangar can feed a v4l2loopback device (so OpenCV, ROS or any
webcam app can read it) or open a viewer window on demand.
"""

from __future__ import annotations

import asyncio
import time

RTP_CAPS = "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264"


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
