#!/usr/bin/env python3
"""Talk to one Hangar vehicle from your own script.

    pip install pymavlink
    python3 mavlink_script.py --port 5762     # vehicle 0; vehicle i is 5762 + 10*i

Each vehicle has its own script ports (Ports tab, "Scripts" column), so a
script sees only that vehicle and never mixes it up with the others. Hangar,
QGroundControl and other scripts keep working at the same time.
"""

import argparse
import time

from pymavlink import mavutil


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=5762)
    args = ap.parse_args()

    m = mavutil.mavlink_connection(f"tcp:127.0.0.1:{args.port}")
    hb = m.wait_heartbeat(timeout=10)
    if hb is None:
        raise SystemExit(f"no vehicle on tcp:{args.port}; is the simulation running?")
    print(f"connected to system {m.target_system}, mode {m.flightmode}")

    # ask for position at 4 Hz on this link
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)
    end = time.time() + 10
    while time.time() < end:
        msg = m.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
        if msg:
            print(f"lat {msg.lat / 1e7:.6f}  lon {msg.lon / 1e7:.6f}  alt {msg.relative_alt / 1000:.1f} m")

    # e.g. change mode:  m.set_mode("GUIDED")   arm:  m.arducopter_arm()


if __name__ == "__main__":
    main()
