"""Child processes that never open their own window and never outlive Hangar.

Each process runs in its own session (process group), its output goes to a
log file and to a callback, and stopping it signals the whole group, so
helpers it spawned (GStreamer pipelines, Gazebo's server and GUI) die too.
"""

from __future__ import annotations

import asyncio
import enum
import os
import signal
from collections.abc import Callable
from pathlib import Path

LineCallback = Callable[[str, str], None]  # (source, line)
ExitCallback = Callable[["ManagedProcess"], None]


class ProcState(enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    EXITED = "exited"  # ended on its own
    FAILED = "failed"  # could not start


class ManagedProcess:
    def __init__(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        log_path: Path | None = None,
        on_line: LineCallback | None = None,
        on_exit: ExitCallback | None = None,
    ):
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.on_line = on_line
        self.on_exit = on_exit
        self.state = ProcState.IDLE
        self.returncode: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._log = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def alive(self) -> bool:
        return self.state == ProcState.RUNNING

    async def start(self) -> None:
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("ab", buffering=0)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                limit=1 << 20,
            )
        except OSError as e:
            self.state = ProcState.FAILED
            self._emit(f"cannot start {self.argv[0]}: {e}")
            self._close_log()
            raise
        self.state = ProcState.RUNNING
        self._emit(f"$ {' '.join(self.argv)}  (pid {self._proc.pid})")
        self._tasks = [asyncio.create_task(self._pump()), asyncio.create_task(self._wait())]

    async def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        stream = self._proc.stdout
        while True:
            try:
                raw = await stream.readline()
            except ValueError:  # line longer than the limit
                raw = await stream.read(1 << 16)
            if not raw:
                break
            if self._log:
                self._log.write(raw)
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line and self.on_line:
                self.on_line(self.name, line)

    async def _wait(self) -> None:
        assert self._proc
        self.returncode = await self._proc.wait()
        # Drain remaining output; a leftover grandchild may hold the pipe open.
        try:
            await asyncio.wait_for(asyncio.shield(self._tasks[0]), 2.0)
        except TimeoutError:
            self._tasks[0].cancel()
        if self.state == ProcState.RUNNING:
            self.state = ProcState.EXITED
            self._emit(f"exited with code {self.returncode}")
        elif self.state == ProcState.STOPPING:
            self.state = ProcState.STOPPED
        self._close_log()
        if self.on_exit:
            self.on_exit(self)

    async def stop(self, timeout: float = 5.0) -> None:
        if not self._proc or self.state not in (ProcState.RUNNING, ProcState.STOPPING):
            return
        self.state = ProcState.STOPPING
        self._signal(signal.SIGINT)  # lets SITL / Gazebo shut down cleanly
        try:
            await asyncio.wait_for(asyncio.shield(self._tasks[1]), timeout)
            return
        except TimeoutError:
            pass
        self._signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(self._tasks[1]), 2.0)
        except TimeoutError:
            self._signal(signal.SIGKILL)
            await self._tasks[1]

    def kill_now(self) -> None:
        """Synchronous last resort, for interpreter exit."""
        if self._proc and self._proc.returncode is None:
            self._signal(signal.SIGKILL)

    def _signal(self, sig: int) -> None:
        if not self._proc:
            return
        try:
            os.killpg(self._proc.pid, sig)
        except ProcessLookupError:
            pass

    def _emit(self, text: str) -> None:
        if self._log:
            self._log.write(f"[hangar] {text}\n".encode())
        if self.on_line:
            self.on_line(self.name, f"[hangar] {text}")

    def _close_log(self) -> None:
        if self._log:
            self._log.close()
            self._log = None
