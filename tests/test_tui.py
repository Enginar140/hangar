"""Drives the terminal UI headlessly (no SITL needed)."""

from textual.widgets import DataTable, Input, Select

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
        app.query_one("#sc-outputs", Input).value = "udp:127.0.0.1:14550, udp:127.0.0.1:{15000+i}"
        await pilot.pause()
        assert app.scenario.world == "runway"
        assert app.scenario.outputs[1] == "udp:127.0.0.1:{15000+i}"

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
