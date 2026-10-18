"""Third-party sources, fetched only with the user's consent.

Hangar's repository contains only Hangar. ArduPilot, its Gazebo plugin and
the example models are downloaded on the user's machine from their upstream
servers (git repositories, or ArduPilot's firmware server for prebuilt SITL),
after their licenses have been shown, so Hangar never redistributes anyone
else's code.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths


@dataclass
class Source:
    id: str
    title: str
    url: str
    ref: str
    license: str
    purpose: str
    required: bool = False
    submodules: bool = False
    dir: str = ""
    kind: str = "git"  # git | download | python
    files: dict[str, str] = field(default_factory=dict)  # download: relative path -> URL
    packages: dict = field(default_factory=dict)  # python: nodeps / deps / wxpython
    build: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return paths.sources_dir() / (self.dir or self.id)

    @property
    def fetched(self) -> bool:
        return (self.path / ".git").exists() or (self.path / ".hangar-fetched").exists()


def load_manifest() -> dict[str, Source]:
    with (paths.package_data() / "sources.yaml").open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {k: Source(id=k, **v) for k, v in raw.items()}


def _lock_path() -> Path:
    return paths.sources_dir() / "lock.json"


def read_lock() -> dict[str, dict]:
    try:
        return json.loads(_lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_lock(source: Source, commit: str, extra: dict | None = None) -> None:
    lock = read_lock()
    lock[source.id] = {"url": source.url, "ref": source.ref, "commit": commit, "license": source.license,
                       **(extra or {})}
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    _lock_path().write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _git(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


SITL_ARCH = {"x86_64": "SITL_x86_64_linux_gnu", "armv7l": "SITL_arm_linux_gnueabihf"}


def fetch(source: Source) -> str:
    """Download ``source`` at its pinned version. Returns the commit or version."""
    if source.kind == "download":
        return _download(source)
    if source.kind == "python":
        return _python_env(source)
    return _clone(source)


PYTHON = "3.12"


def find_uv() -> str | None:
    return shutil.which("uv") or next(
        (str(p) for p in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv") if p.exists()), None
    )


def ubuntu_release() -> str | None:
    try:
        info = dict(
            line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
        )
    except OSError:
        return None
    if info.get("ID") != "ubuntu":
        return None
    return info.get("VERSION_ID", "").strip('"') or None


def python_env_commands(source: Source, uv: str, venv: Path, ubuntu: str | None, machine: str) -> list[list[str]]:
    """The uv commands that build a tool's own environment (kept pure for tests)."""
    pk = source.packages
    py = "cp" + PYTHON.replace(".", "")
    target = ["--python", str(venv / "bin" / "python")]
    cmds = [[uv, "venv", "--quiet", "--python", PYTHON, str(venv)]]
    if pk.get("nodeps"):
        cmds.append([uv, "pip", "install", "--quiet", *target, "--no-deps", *pk["nodeps"]])
    deps = list(pk.get("deps", []))
    if pk.get("wxpython"):
        if ubuntu in ("22.04", "24.04") and machine == "x86_64":
            deps.append("wxPython @ " + pk["wxpython"].format(ubuntu=ubuntu, py=py))
        else:
            raise RuntimeError(
                "a ready-made wxPython exists only for Ubuntu 22.04/24.04 on x86_64. Elsewhere install "
                "MAVProxy yourself (see ardupilot.org/mavproxy, 'Download and Installation') and Hangar "
                "will use mavproxy.py from your PATH"
            )
    if deps:
        cmds.append([uv, "pip", "install", "--quiet", *target, *deps])
    return cmds


def _python_env(source: Source, progress=print) -> str:
    """Build the tool's own environment in place (a venv cannot be moved later);
    a failed build is removed, and only a finished one gets the marker file."""
    uv = find_uv()
    if not uv:
        raise RuntimeError("uv is required: curl -LsSf https://astral.sh/uv/install.sh | sh")
    dest = source.path
    commands = python_env_commands(source, uv, dest, ubuntu_release(), platform.machine())
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        for argv in commands:
            progress("  $ uv " + " ".join(argv[1:]))
            subprocess.run(argv, check=True)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    (dest / ".hangar-fetched").write_text(source.ref, encoding="utf-8")
    _write_lock(source, source.ref, {"python": PYTHON})
    return source.ref


def _download(source: Source, progress=print) -> str:
    machine = platform.machine()
    arch = SITL_ARCH.get(machine)
    if arch is None and any("{arch}" in u for u in source.files.values()):
        raise RuntimeError(f"no prebuilt SITL for {machine}; use `hangar sources fetch ardupilot` "
                           "and `hangar sources build ardupilot`")
    dest = source.path
    staging = Path(tempfile.mkdtemp(prefix=".download-", dir=_ensure(dest.parent)))
    hashes: dict[str, str] = {}
    try:
        for rel, url in source.files.items():
            url = url.replace("{arch}", arch or "")
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            progress(f"  {rel}  <-  {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "hangar"})
            with urllib.request.urlopen(req, timeout=60) as r, target.open("wb") as f:
                shutil.copyfileobj(r, f)
            hashes[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
            if rel.startswith("bin/"):
                target.chmod(0o755)
        (staging / ".hangar-fetched").write_text(source.ref, encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(staging, dest)  # all files or none
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _write_lock(source, source.ref, {"arch": arch, "sha256": hashes})
    return source.ref


def _ensure(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clone(source: Source) -> str:
    if not shutil.which("git"):
        raise RuntimeError("git is required: sudo apt install git")
    dest = source.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        if dest.exists():
            raise RuntimeError(f"{dest} exists and is not a git checkout; move it away first")
        _git(["init", "-q", str(dest)])
        _git(["remote", "add", "origin", source.url], cwd=dest)
    # Fetching a single ref works for tags, branches and (on GitHub) commits.
    _git(["fetch", "--depth", "1", "origin", source.ref], cwd=dest)
    _git(["checkout", "-q", "--force", "FETCH_HEAD"], cwd=dest)
    if source.submodules:
        _git(["submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "8"], cwd=dest)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write_lock(source, commit)
    return commit


def build_steps(source: Source) -> list[tuple[str, list[str] | str]]:
    """(description, command) pairs; a str command runs through the shell."""
    b = source.build or {}
    steps: list[tuple[str, list[str] | str]] = []
    if b.get("apt"):
        steps.append(("system packages", ["sudo", "apt-get", "install", "-y", *b["apt"]]))
    if b.get("prereqs"):
        steps.append(("ArduPilot prerequisites (uses sudo)", b["prereqs"]))
    for cmd in b.get("commands", []):
        steps.append((cmd, cmd))
    return steps


def run_step(source: Source, command: list[str] | str) -> None:
    if isinstance(command, str):
        subprocess.run(["bash", "-lc", command], cwd=source.path, check=True)
    else:
        subprocess.run(command, cwd=source.path, check=True)
