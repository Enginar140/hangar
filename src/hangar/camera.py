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
