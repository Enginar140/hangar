"""Drives the terminal UI headlessly (no SITL needed)."""

from textual.widgets import Button, DataTable, Input, Select

from hangar.scenario import Scenario
from hangar.tui import HangarApp
from hangar.tui.screens import DoctorScreen, HelpScreen, VehicleEditor


async def test_edit_flow(settings, tmp_path):
    app = HangarApp(scenario=Scenario(name="t"), settings=settings)
    async with app.run_test(size=(140, 45)) as pilot:
        # add a vehicle through the dialog
        await pilot.press("a")
        await pilot.pause()
        editor = app.screen
        assert isinstance(editor, VehicleEditor)
        editor.query_one("#model", Select).value = "testplane"
        editor.query_one("#pose", Input).value = "5 6 0.3 90"
        editor.query_one("#params", Input).value = "SIM_WIND_SPD=4"
        await pilot.click("#ok")
        await pilot.pause()
        assert [v.model for v in app.scenario.vehicles] == ["testplane"]
        v = app.scenario.vehicles[0]
        assert v.pose == [5, 6, 0.3, 0, 0, 90] and v.params == {"SIM_WIND_SPD": 4}

        # a second one; the fleet table follows
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#model", Select).value = "sitl:copter"
        await pilot.click("#ok")
        await pilot.pause(0.6)
        fleet = app.query_one("#fleet", DataTable)
        assert fleet.row_count == 2

        # edit keeps position in the list
        app.selected = "testplane"
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "falcon"
        await pilot.click("#ok")
        await pilot.pause()
        assert [v.name for v in app.scenario.vehicles] == ["falcon", "copter"]

        # remove
        app.selected = "copter"
        await pilot.press("d")
        await pilot.pause()
        assert [v.name for v in app.scenario.vehicles] == ["falcon"]

        # scenario form writes through
        await pilot.press("4")
        await pilot.pause()
        app.query_one("#sc-world", Select).value = "runway"
        await pilot.pause()
        assert app.scenario.world == "runway"

        # save
        path = tmp_path / "saved.yaml"
        await pilot.press("ctrl+s")
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(path)
        await pilot.click("#ok")
        await pilot.pause()
        assert Scenario.load(path) == app.scenario

        # help and doctor dialogs open and close
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.press("f2")
        await pilot.pause(1.0)
        assert isinstance(app.screen, DoctorScreen)
        await pilot.press("escape")
        await pilot.pause()

        # vehicle buttons are disabled until the simulation runs
        assert app.query_one("#arm").disabled


async def test_open_scenario(settings, tmp_path):
    path = tmp_path / "s.yaml"
    Scenario.model_validate({"name": "opened", "vehicles": [{"model": "sitl:plane"}]}).save(path)
    app = HangarApp(settings=settings)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("ctrl+o")
        await pilot.pause()
        app.screen.query_one("#path", Input).value = str(path)
        await pilot.click("#ok")
        await pilot.pause()
        assert app.scenario.name == "opened"
        assert app.query_one("#fleet", DataTable).row_count == 1


async def test_editor_camera_switch(settings):
    from textual.widgets import Switch

    app = HangarApp(scenario=Scenario(name="t"), settings=settings)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        editor = app.screen
        editor.query_one("#model", Select).value = "testplane"  # has its own camera
        await pilot.pause()
        assert editor.query_one("#camera", Switch).value is True
        editor.query_one("#model", Select).value = "testcopter"  # has none
        await pilot.pause()
        cam = editor.query_one("#camera", Switch)
        assert cam.value is False
        cam.value = True
        editor.query_one("#v4l2", Input).value = "/dev/video2"
        await pilot.click("#ok")
        await pilot.pause()
        c = app.scenario.vehicles[0].camera
        assert c.enabled and c.v4l2_device == "/dev/video2"
        assert (c.width, c.height, c.fps) == (1280, 720, 30) and c.pose is None


async def test_editor_camera_quality_and_direction(settings):
    from textual.widgets import Switch

    app = HangarApp(scenario=Scenario(name="t"), settings=settings)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        ed = app.screen
        ed.query_one("#model", Select).value = "testcopter"
        await pilot.pause()
        ed.query_one("#camera", Switch).value = True
        await pilot.pause()
        ed.query_one("#cam-quality", Select).value = "low"
        await pilot.pause()
        assert ed.query_one("#cam-w", Input).value == "640"
        ed.query_one("#cam-fps", Input).value = "15"
        ed.query_one("#cam-dir", Select).value = "down"
        await pilot.pause()
        ed.query_one("#ok", Button).press()
        await pilot.pause()
        c = app.scenario.vehicles[0].camera
        assert (c.width, c.height, c.fps) == (640, 360, 15)
        assert c.pose == [0, 0, -0.05, 0, 90, 0]


async def test_ports_tab_add_and_remove_output(settings):
    from hangar.tui.screens import OutputDialog

    sc = Scenario.model_validate({"vehicles": [{"model": "sitl:plane"}, {"model": "sitl:copter"}]})
    app = HangarApp(scenario=sc, settings=settings)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        app.query_one("#out-add", Button).press()
        await pilot.pause()
        dlg = app.screen
        assert isinstance(dlg, OutputDialog)
        # a listening port without {i} would be bound twice: refused
        dlg.query_one("#kind", Select).value = "udpin"
        dlg.query_one("#port", Input).value = "14600"
        await pilot.pause()
        assert dlg.query_one("#ok", Button).disabled
        dlg.query_one("#port", Input).value = "{14600+i}"
        await pilot.pause()
        assert not dlg.query_one("#ok", Button).disabled
        dlg.query_one("#ok", Button).press()
        await pilot.pause(0.5)
        assert app.scenario.outputs == ["udp:127.0.0.1:14550", "udpin:127.0.0.1:{14600+i}"]
        table = app.query_one("#outputs", DataTable)
        assert table.row_count == 4  # 2 vehicles x 2 outputs
        table.move_cursor(row=1)  # plane's udpin row
        await pilot.pause()
        app.query_one("#out-remove", Button).press()
        await pilot.pause(0.5)
        assert app.scenario.outputs == ["udp:127.0.0.1:14550"]
