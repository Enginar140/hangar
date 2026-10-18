"""`hangar doctor`: checks the machine and says how to fix what is missing."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .catalog import BINARIES, Catalog
from .settings import Settings

Status = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


def _run(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _find_lib(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        if (d / name).is_file():
            return d / name
    return None


def run_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    add = checks.append

    v = sys.version_info
    add(Check("Python", "ok" if v >= (3, 11) else "fail", f"{v.major}.{v.minor}.{v.micro}",
              "Hangar needs Python 3.11 or newer"))

    bin_dir = settings.resolved_bin_dir()
    for binary in sorted(set(BINARIES.values())):
        exe = bin_dir / binary if bin_dir else None
        if exe and exe.is_file() and os.access(exe, os.X_OK):
            add(Check(f"SITL {binary}", "ok", str(exe)))
        elif exe and exe.is_file():
            add(Check(f"SITL {binary}", "fail", "not executable", f"chmod +x {exe}"))
        else:
            add(Check(f"SITL {binary}", "fail", "not found",
                      "hangar sources fetch ardupilot_sitl   (prebuilt, seconds)   or   "
                      "hangar config set ardupilot.dir /path/to/your/ardupilot"))

    at = settings.autotest_dir()
    add(Check("ArduPilot parameter files", "ok" if at else "warn", str(at) if at else "not found",
              "hangar sources fetch ardupilot_sitl   (downloads them with the binaries)"))

    gz = shutil.which("gz")
    if gz:
        code, out = _run(["gz", "sim", "--versions"])
        add(Check("Gazebo", "ok" if code == 0 else "warn", out.splitlines()[0] if out else gz,
                  "gz is installed but `gz sim` failed; reinstall Gazebo Harmonic"))
    else:
        add(Check("Gazebo", "warn", "not installed (only needed for Gazebo vehicles)",
                  "https://gazebosim.org/docs/harmonic/install_ubuntu  or rerun install.sh with --gazebo"))

    plugin_dirs = settings.resolved_plugin_paths()
    lib = _find_lib("libArduPilotPlugin.so", plugin_dirs)
    add(Check("ArduPilot Gazebo plugin", "ok" if lib else "warn", str(lib) if lib else "not built",
              "hangar sources build ardupilot_gazebo"))
    cam = _find_lib("libGstCameraPlugin.so", plugin_dirs)
    add(Check("Camera plugin", "ok" if cam else "warn", str(cam) if cam else "not built",
              "Built together with ardupilot_gazebo (needs libgstreamer1.0-dev)"))

    if shutil.which("gst-launch-1.0"):
        code, _ = _run(["gst-inspect-1.0", "avdec_h264"])
        add(Check("GStreamer H.264", "ok" if code == 0 else "warn",
                  "avdec_h264 available" if code == 0 else "avdec_h264 missing",
                  "sudo apt install gstreamer1.0-libav gstreamer1.0-plugins-good gstreamer1.0-plugins-bad"))
    else:
        add(Check("GStreamer", "warn", "gst-launch-1.0 not found",
                  "sudo apt install gstreamer1.0-tools gstreamer1.0-libav gstreamer1.0-plugins-good"))

    if Path("/sys/module/v4l2loopback").exists():
        devices = sorted(str(p) for p in Path("/dev").glob("video*"))
        add(Check("v4l2loopback", "ok", ", ".join(devices) or "loaded"))
    else:
        add(Check("v4l2loopback", "warn", "module not loaded (only for camera -> OpenCV)",
                  "sudo apt install v4l2loopback-dkms && "
                  "sudo modprobe v4l2loopback video_nr=2 card_label=Hangar exclusive_caps=1"))

    clip = next((c for c in ("wl-copy", "xclip", "xsel") if shutil.which(c)), None)
    add(Check("Clipboard", "ok" if clip else "warn", clip or "no clipboard tool (Copy buttons need one)",
              "sudo apt install wl-clipboard xclip"))

    if shutil.which("nvidia-smi"):
        code, out = _run(["nvidia-smi", "-L"])
        add(Check("NVIDIA driver", "ok" if code == 0 else "warn",
                  out.splitlines()[0] if code == 0 and out else "nvidia-smi failed",
                  "Driver/library mismatch after an update usually needs a reboot"))

    cat = Catalog(settings)
    n_gz = sum(1 for m in cat.vehicles.values() if m.needs_gazebo)
    add(Check("Vehicle catalog", "ok" if n_gz else "warn",
              f"{len(cat.vehicles)} vehicles ({n_gz} Gazebo), {len(cat.worlds)} worlds",
              "hangar sources fetch sitl_models   or   hangar config add catalog.model_paths DIR"))
    for w in cat.warnings:
        add(Check("Model", "warn", w))
    return checks
