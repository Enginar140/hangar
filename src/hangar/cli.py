"""Command line entry point: `hangar`."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, paths
from .i18n import set_language
from .settings import LIST_KEYS, SCALAR_KEYS, Settings

console = Console()
err = Console(stderr=True)

EXAMPLES = {
    "quick": "quickstart.yaml",
    "gazebo": "gazebo-plane.yaml",
    "fleet": "mixed-fleet.yaml",
}


def _load_scenario(path: str):
    from .scenario import Scenario

    try:
        return Scenario.load(path)
    except FileNotFoundError:
        err.print(f"[red]scenario not found:[/] {path}")
    except Exception as e:  # noqa: BLE001 - pydantic/yaml errors are user errors
        err.print(f"[red]invalid scenario {path}:[/]\n{e}")
    sys.exit(2)


def _example(name: str):
    from .scenario import Scenario

    return Scenario.load(paths.package_data() / "scenarios" / EXAMPLES[name])


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        err.print("[red]not a terminal; pass --yes to confirm[/]")
        return False
    return console.input(f"{question} [bold][y/N][/] ").strip().lower() in ("y", "yes", "e", "evet")


# --- commands ----------------------------------------------------------------


def cmd_tui(args, settings: Settings) -> int:
    from .tui import HangarApp

    scenario = path = None
    if args.scenario:
        path = Path(args.scenario).resolve()
        scenario = _load_scenario(args.scenario)
    elif args.example:
        scenario = _example(args.example)
    HangarApp(scenario=scenario, scenario_path=path, settings=settings).run()
    return 0


def cmd_up(args, settings: Settings) -> int:
    from .catalog import Catalog
    from .supervisor import Supervisor, SupervisorError

    scenario = _load_scenario(args.scenario) if args.scenario else _example(args.example or "quick")
    styles = {"error": "red", "warning": "yellow", "event": "cyan", "info": ""}

    def on_log(line) -> None:
        if not args.verbose and line.source.endswith("/sitl") and line.level == "info":
            return
        style = styles.get(line.level, "")
        text = escape(line.text)
        console.print(f"[bold]{escape(line.source):>16}[/] " + (f"[{style}]{text}[/]" if style else text),
                      highlight=False)

    async def run() -> int:
        sup = Supervisor(scenario, settings, Catalog(settings), on_log=on_log)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        start = asyncio.create_task(sup.start())
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({start, waiter}, return_when=asyncio.FIRST_COMPLETED)
        code = 0
        if start in done and start.exception():
            code = 1
        elif start in done:
            console.print("[green]running[/] — Ctrl+C to stop")
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), args.status_every)
                except TimeoutError:
                    _print_status(sup)
        await sup.stop()
        waiter.cancel()
        if start.done() and not start.cancelled() and isinstance(start.exception(), SupervisorError):
            code = 1
        return code

    return asyncio.run(run())


def _print_status(sup) -> None:
    for v in sup.vehicles:
        tel = v.link.telemetry if v.link else None
        if not tel:
            continue
        alt = "—" if tel.alt_rel is None else f"{tel.alt_rel:.1f}m"
        gs = "—" if tel.groundspeed is None else f"{tel.groundspeed:.1f}m/s"
        console.print(f"[dim]{v.name:>16} {v.status:8} {tel.mode:10} "
                      f"{'ARMED' if tel.armed else 'disarmed':9} {alt:>8} {gs:>9} {tel.gps_text}[/]",
                      highlight=False)


def cmd_ports(args, settings: Settings) -> int:
    from .catalog import Catalog
    from .supervisor import Supervisor

    scenario = _load_scenario(args.scenario) if args.scenario else _example(args.example or "quick")
    pf = Supervisor(scenario, settings, Catalog(settings)).preflight()
    if pf.plan:
        table = Table("#", "Name", "SysID", "MAVLink", "Gazebo FDM", "Camera", "Outputs", header_style="bold")
        for r in pf.plan.rows():
            table.add_row(r["index"], r["name"], r["sysid"], r["mavlink"], r["fdm"], r["camera"], r["outputs"])
        console.print(table)
    for w in pf.warnings:
        console.print(f"[yellow]! {escape(w)}[/]")
    for e in pf.errors:
        console.print(f"[red]✗ {escape(e)}[/]")
    if pf.ok:
        console.print("[green]✓ ready to start[/]")
    return 0 if pf.ok else 1


def cmd_init(args, settings: Settings) -> int:
    src = paths.package_data() / "scenarios" / EXAMPLES[args.example]
    dest = Path(args.path or EXAMPLES[args.example])
    if dest.exists() and not args.force:
        err.print(f"[red]{dest} exists[/] (use --force to overwrite)")
        return 1
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    console.print(f"wrote [b]{dest}[/]  —  run it with: [b]hangar {dest}[/]")
    return 0


def cmd_models(args, settings: Settings) -> int:
    from .catalog import Catalog

    cat = Catalog(settings)
    table = Table("Model", "Type", "Physics", "Camera", "Parameters", header_style="bold")
    for name, m in sorted(cat.vehicles.items(), key=lambda kv: (kv[1].kind, kv[0])):
        if args.type and m.type != args.type:
            continue
        res = cat.resolve_params(m)
        params = ", ".join(p.name for p in res.files)
        if res.missing:
            params += f" [yellow](missing: {', '.join(res.missing)})[/]"
        table.add_row(name, m.type, "Gazebo" if m.needs_gazebo else "built-in",
                      "✓" if m.has_camera else "", params or "—")
    console.print(table)
    for w in cat.warnings:
        console.print(f"[yellow]! {escape(w)}[/]")
    return 0


def cmd_worlds(args, settings: Settings) -> int:
    from .catalog import Catalog

    cat = Catalog(settings)
    table = Table("World", "File", header_style="bold")
    for name, w in sorted(cat.worlds.items()):
        table.add_row(name, str(w.path))
    console.print(table if cat.worlds else "no worlds found — `hangar sources fetch sitl_models`")
    return 0


def cmd_doctor(args, settings: Settings) -> int:
    from .doctor import run_checks

    checks = run_checks(settings)
    icons = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]"}
    for c in checks:
        console.print(f" {icons[c.status]} [b]{c.name:<24}[/] {escape(c.detail)}", highlight=False)
        if c.hint and c.status != "ok":
            console.print(f"   [dim]→ {escape(c.hint)}[/]", highlight=False)
    failed = sum(c.status == "fail" for c in checks)
    console.print(f"\n{'[green]all good[/]' if not failed else f'[red]{failed} problem(s)[/]'}"
                  f"  ·  config: {paths.config_file()}")
    return 1 if failed else 0


def cmd_config(args, settings: Settings) -> int:
    if args.action == "path":
        console.print(str(paths.config_file()))
        return 0
    if args.action == "show":
        for k, v in settings.as_dict().items():
            console.print(f"[b]{k}[/] = {v!r}")
        console.print(f"\n[dim]resolved ardupilot: {settings.resolved_ardupilot_dir()}\n"
                      f"resolved binaries: {settings.resolved_bin_dir()}\n"
                      f"model paths: {[str(p) for p in settings.resolved_model_paths()]}\n"
                      f"world paths: {[str(p) for p in settings.resolved_world_paths()]}\n"
                      f"plugin paths: {[str(p) for p in settings.resolved_plugin_paths()]}[/]")
        return 0
    if not args.key or args.value is None:
        err.print("usage: hangar config set|add KEY VALUE")
        return 2
    try:
        if args.action == "set":
            settings.set_key(args.key, args.value)
        else:
            settings.add_key(args.key, args.value)
    except KeyError:
        keys = sorted(SCALAR_KEYS | LIST_KEYS) if args.action == "set" else sorted(LIST_KEYS)
        err.print(f"[red]unknown key {args.key}[/]; keys: {', '.join(keys)}")
        return 2
    console.print(f"saved {settings.save()}")
    return 0


def cmd_sources(args, settings: Settings) -> int:
    from . import sources

    manifest = sources.load_manifest()
    lock = sources.read_lock()

    if args.action == "list":
        table = Table("ID", "Project", "License", "Status", "Path", header_style="bold")
        for s in manifest.values():
            status = f"[green]{lock.get(s.id, {}).get('commit', 'fetched')[:10]}[/]" if s.fetched else "—"
            table.add_row(s.id, s.title, s.license, status, str(s.path))
        console.print(table)
        return 0

    ids = args.ids or ([k for k, s in manifest.items() if s.required] if args.action == "fetch" else [])
    unknown = [i for i in ids if i not in manifest]
    if unknown or not ids:
        err.print(f"[red]choose from:[/] {', '.join(manifest)}")
        return 2
    chosen = [manifest[i] for i in ids]

    if args.action == "fetch":
        console.print("These projects will be downloaded from their own repositories:\n")
        for s in chosen:
            console.print(f"  [b]{s.title}[/]\n    {s.url} @ {s.ref}\n"
                          f"    license: [yellow]{s.license}[/]\n    {s.purpose}\n")
        console.print("By continuing you obtain them under their licenses; Hangar does not redistribute them.")
        if not _confirm("Download?", args.yes):
            return 1
        for s in chosen:
            console.rule(s.title)
            try:
                commit = sources.fetch(s)
            except Exception as e:  # noqa: BLE001
                err.print(f"[red]{s.id}: {e}[/]")
                return 1
            console.print(f"[green]✓[/] {s.id} @ {commit[:10]} → {s.path}")
        return 0

    # build
    for s in chosen:
        if not s.fetched:
            err.print(f"[red]{s.id} is not fetched yet:[/] hangar sources fetch {s.id}")
            return 1
        steps = sources.build_steps(s)
        if not steps:
            console.print(f"{s.id}: nothing to build")
            continue
        console.print(f"[b]{s.title}[/] build steps:")
        for desc, _ in steps:
            console.print(f"  • {desc}")
        if not _confirm("Run them?", args.yes):
            return 1
        for desc, command in steps:
            console.rule(desc)
            try:
                sources.run_step(s, command)
            except Exception as e:  # noqa: BLE001
                err.print(f"[red]{desc} failed: {e}[/]")
                return 1
        console.print(f"[green]✓ {s.id} built[/]")
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hangar",
        description="Multi-vehicle ArduPilot SITL + Gazebo, from one terminal.",
        epilog="Run `hangar` alone to open the terminal UI.",
    )
    p.add_argument("--version", action="version", version=f"hangar {__version__}")
    sub = p.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="open the terminal UI (default)")
    tui.add_argument("scenario", nargs="?")
    tui.add_argument("--example", choices=EXAMPLES)

    up = sub.add_parser("up", help="run a scenario without the UI (Ctrl+C stops it)")
    up.add_argument("scenario", nargs="?")
    up.add_argument("--example", choices=EXAMPLES)
    up.add_argument("-v", "--verbose", action="store_true", help="also print raw SITL output")
    up.add_argument("--status-every", type=float, default=10.0, metavar="SEC")

    ports = sub.add_parser("ports", help="show the port plan and preflight result")
    ports.add_argument("scenario", nargs="?")
    ports.add_argument("--example", choices=EXAMPLES)

    init = sub.add_parser("init", help="write an example scenario file")
    init.add_argument("path", nargs="?")
    init.add_argument("--example", choices=EXAMPLES, default="quick")
    init.add_argument("--force", action="store_true")

    models = sub.add_parser("models", help="list vehicles that can be launched")
    models.add_argument("--type", choices=["copter", "plane", "rover", "boat"])
    sub.add_parser("worlds", help="list Gazebo worlds")
    sub.add_parser("doctor", help="check the installation")

    cfg = sub.add_parser("config", help="show or change settings")
    cfg.add_argument("action", choices=["show", "set", "add", "path"])
    cfg.add_argument("key", nargs="?")
    cfg.add_argument("value", nargs="?")

    src = sub.add_parser("sources", help="download/build ArduPilot, the Gazebo plugin and models")
    src.add_argument("action", nargs="?", choices=["list", "fetch", "build"], default="list")
    src.add_argument("ids", nargs="*")
    src.add_argument("-y", "--yes", action="store_true", help="do not ask")
    return p


COMMANDS = {
    "tui": cmd_tui, "up": cmd_up, "ports": cmd_ports, "init": cmd_init, "models": cmd_models,
    "worlds": cmd_worlds, "doctor": cmd_doctor, "config": cmd_config, "sources": cmd_sources,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `hangar my.yaml` is a shortcut for `hangar tui my.yaml`.
    if argv and argv[0].endswith((".yaml", ".yml")):
        argv.insert(0, "tui")
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    set_language(settings.language)
    if not args.command:
        args = build_parser().parse_args(["tui"])
    return COMMANDS[args.command](args, settings) or 0


if __name__ == "__main__":
    sys.exit(main())
