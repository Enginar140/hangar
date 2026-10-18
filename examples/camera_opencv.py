#!/usr/bin/env python3
"""Read a Hangar vehicle camera into numpy/OpenCV.

    pip install numpy opencv-python        # sudo apt install gstreamer1.0-libav
    python3 camera_opencv.py --port 5600 --size 1280x720

The port and size are in Hangar's Ports tab and in the camera window (Camera
button). The stream is H.264 over RTP/UDP; gst-launch decodes it and hands raw
BGR frames to Python, so pip's OpenCV works (it has no GStreamer support).
Only one program can read a UDP port at a time; for several readers give the
camera a v4l2 device and use cv2.VideoCapture('/dev/videoN').
"""

import argparse
import subprocess

import cv2
import numpy as np

CAPS = "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264"


def frames(port: int, width: int, height: int):
    """Yield BGR frames (height x width x 3, uint8) until the stream ends."""
    gst = subprocess.Popen(
        ["gst-launch-1.0", "-q", "udpsrc", f"port={port}", f"caps={CAPS}",
         "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
         "!", "videoconvert", "!", "videoscale",
         "!", f"video/x-raw,format=BGR,width={width},height={height}", "!", "fdsink"],
        stdout=subprocess.PIPE, bufsize=width * height * 3,
    )
    size = width * height * 3
    try:
        while True:
            raw = gst.stdout.read(size)
            if len(raw) < size:
                return
            yield np.frombuffer(raw, np.uint8).reshape(height, width, 3)
    finally:
        gst.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--size", default="1280x720", help="WIDTHxHEIGHT, frames are scaled to it")
    args = ap.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))

    for frame in frames(args.port, width, height):
        # Your image processing goes here; `frame` is a regular OpenCV image.
        edges = cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 80, 160)
        cv2.imshow(f"camera {args.port}", frame)
        cv2.imshow("edges", edges)
        if cv2.waitKey(1) == 27:  # Esc
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
