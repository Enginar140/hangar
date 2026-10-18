"""Hangar camera check: does the stream reach this computer, and can OpenCV show it?

    pip install opencv-python
    python camera_check.py 5600

Works on Windows, Linux and macOS. Prints what it finds at every step.
"""

import os
import socket
import sys
import tempfile
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5600

# --- 1. are packets arriving at all? (no OpenCV involved) -------------------
print(f"[1/3] Listening on UDP {PORT} for 5 seconds...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("0.0.0.0", PORT))
except OSError as e:
    sys.exit(f"      Cannot use port {PORT}: {e}\n"
             "      Another program (QGC, VLC, another script) is using it. Close it.")
sock.settimeout(0.5)
packets = size = 0
end = time.time() + 5
while time.time() < end:
    try:
        data, sender = sock.recvfrom(65536)
        packets += 1
        size += len(data)
    except TimeoutError:
        pass
sock.close()
if packets == 0:
    sys.exit(
        "      No packets arrived. The stream does not reach this computer. Check:\n"
        "      - Hangar > Ports > Camera outputs has THIS computer's IP (ipconfig / hostname -I) and this port\n"
        "      - the simulation is running and the camera says 'streaming'\n"
        "      - firewall: allow inbound UDP on this port. Windows, PowerShell as admin:\n"
        "        New-NetFirewallRule -DisplayName 'Hangar video' -Direction Inbound -Protocol UDP "
        f"-LocalPort {PORT} -Action Allow\n"
        "      - Windows Wi-Fi set to 'Private', not 'Public'"
    )
sender_ip = sender[0]
print(f"      OK: {packets} packets ({size / 5 / 1024:.0f} KB/s) from {sender_ip}")

# --- 2. describe the stream to OpenCV/FFmpeg with an SDP file --------------
sdp = os.path.join(tempfile.gettempdir(), f"hangar_{PORT}.sdp")
with open(sdp, "w") as f:
    f.write("v=0\no=- 0 0 IN IP4 0.0.0.0\ns=Hangar camera\nc=IN IP4 0.0.0.0\nt=0 0\n"
            f"m=video {PORT} RTP/AVP 96\na=rtpmap:96 H264/90000\n")
print(f"[2/3] Stream description written to {sdp}")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "protocol_whitelist;file,rtp,udp"  # before importing cv2
try:
    import cv2
except ImportError:
    sys.exit("      OpenCV is missing: pip install opencv-python")

# --- 3. decode and show ----------------------------------------------------
print(f"[3/3] Opening with OpenCV {cv2.__version__}...")
cap = cv2.VideoCapture(sdp, cv2.CAP_FFMPEG)
if not cap.isOpened():
    sys.exit("      OpenCV could not open the stream. Is this opencv-python from pip (it includes FFmpeg)?\n"
             "      Try: pip install --upgrade opencv-python")
frames, start = 0, time.time()
while True:
    ok, frame = cap.read()
    if not ok:
        if frames == 0 and time.time() - start > 15:
            sys.exit("      Packets arrive but no picture could be decoded in 15 s.")
        continue
    frames += 1
    if frames == 1:
        print(f"      OK: {frame.shape[1]}x{frame.shape[0]} frames. A window opens; Esc closes it.")
    if os.environ.get("HANGAR_CHECK_HEADLESS"):
        if frames >= 30:
            print(f"      {frames} frames in {time.time() - start:.1f}s")
            break
        continue
    cv2.imshow(f"Hangar camera {PORT}", frame)
    if cv2.waitKey(1) == 27:
        break
cap.release()
if not os.environ.get("HANGAR_CHECK_HEADLESS"):
    cv2.destroyAllWindows()
