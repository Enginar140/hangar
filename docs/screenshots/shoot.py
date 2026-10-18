"""Run the demo scenario in the TUI and save README screenshots as SVG.

    uv run python docs/screenshots/shoot.py en|tr OUT_DIR

OUT_DIR must contain demo.yaml. The copter "scout" takes off to 15 m first so
the screenshots show a vehicle in flight. With "en" one frame of each camera
is saved too (cam-nose.png, cam-down.png).
"""

import asyncio
import subprocess
import sys
import time

from hangar.i18n import set_language

CAPS = "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264"


def grab_frame(port: int, path: str) -> subprocess.Popen:
    return subprocess.Popen([
        "timeout", "25", "gst-launch-1.0", "-q", "-e", "udpsrc", f"port={port}", f"caps={CAPS}",
        "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264", "!", "videoconvert",
        "!", "pngenc", "snapshot=true", "!", "filesink", f"location={path}",
    ])


async def shoot(lang: str, out: str) -> None:
    # imported after set_language: the UI picks its strings at import time
    from textual.widgets import Button, DataTable

    from hangar.scenario import Scenario
    from hangar.tui import HangarApp

    app = HangarApp(scenario=Scenario.load(f"{out}/demo.yaml"))
    async with app.run_test(size=(140, 42)) as pilot:
        sup = app.supervisor

        async def show(key: str, name: str, wait: float = 1.0) -> None:
            await pilot.press(key)
            await pilot.pause(wait)
            app.save_screenshot(f"{lang}-{name}.svg", path=out)

        await pilot.press("s")
        started = time.time()
        while time.time() - started < 150:
            await pilot.pause(1)
            scout = next((v for v in sup.vehicles if v.name == "scout"), None)
            cameras_on = all(v.camera_topic for v in sup.vehicles if v.ports.camera)
            if scout and scout.link and scout.link.telemetry.ready and cameras_on:
                break
        print(f"ready after {time.time() - started:.0f} s")

        link = sup.vehicle("scout").link
        print(await link.takeoff(15))
        for _ in range(40):
            await pilot.pause(1)
            if (link.telemetry.alt_rel or 0) > 12:
                break

        fleet = app.query_one("#fleet", DataTable)
        fleet.move_cursor(row=1)
        await pilot.pause(1.5)
        await show("1", "vehicle")
        await show("3", "ports", 1.5)

        await pilot.press("1")
        fleet.move_cursor(row=0)
        await pilot.pause(1.0)
        app.query_one("#camera", Button).press()
        await pilot.pause(1.0)
        app.save_screenshot(f"{lang}-camera.svg", path=out)
        await pilot.press("escape")
        await pilot.pause(0.3)
        await show("2", "logs")

        if lang == "en":
            grabs = [grab_frame(5600, f"{out}/cam-nose.png"), grab_frame(5601, f"{out}/cam-down.png")]
            for g in grabs:
                await asyncio.to_thread(g.wait)
        await sup.stop()


if __name__ == "__main__":
    language, out_dir = sys.argv[1], sys.argv[2]
    set_language(language)
    asyncio.run(shoot(language, out_dir))
