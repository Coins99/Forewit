"""llama.cpp's llama-server as a side process: one model on 127.0.0.1, for Forewit alone.

As llama-server starts by default, any program and any web page on the computer could use
it (P0-B1). So it runs here on a free port of 127.0.0.1 only, with a fresh API key on every
launch, no web UI and one slot. Before it is used, the server on that port must refuse a
wrong key and accept the right one, so a program that took the port first is never sent an
email.

Its output stays in a bounded buffer in memory, never in Forewit's logs or files (more
verbose, llama-server prints prompts); only known startup failures become static codes. It
stops with its owner, also when starting is interrupted. On Windows a Job Object ends it when
Forewit exits, a crash included; elsewhere a record of the process lets the next start end a
server that a crash left running.
"""

import asyncio
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import IO, Final, Self

import httpx

from mailbrief.ports.errors import ProviderUnavailableError
from mailbrief.providers.local.provider import Endpoint
from mailbrief.providers.local.transport import LoopbackGuard

logger = logging.getLogger(__name__)

HOST: Final = "127.0.0.1"
# Loading a 2.5 GB model from a slow disk and preparing the GPU can take minutes.
READY_TIMEOUT_SECONDS: Final = 300.0
STOP_TIMEOUT_SECONDS: Final = 10.0
OUTPUT_LINES: Final = 200
_POLL_SECONDS: Final = 0.25
# Windows takes about 2 seconds to refuse a connection to a loopback port nothing listens on
# yet; one that listens accepts in milliseconds, even while the model loads.
_CHECK_TIMEOUT: Final = httpx.Timeout(5.0, connect=0.5)

# Codes for what can go wrong with the server; each has a static message.
OUT_OF_MEMORY: Final = "local_model_out_of_memory"
LOAD_FAILED: Final = "local_model_load_failed"
PORT_TAKEN: Final = "local_server_port_taken"
EXITED: Final = "local_server_exited"
NOT_READY: Final = "local_server_not_ready"
NOT_OURS: Final = "local_server_not_ours"
NOT_STARTED: Final = "local_server_not_started"
_MESSAGES: Final = {
    OUT_OF_MEMORY: "This computer doesn't have enough free memory for the local model.",
    LOAD_FAILED: "The local model couldn't be loaded.",
    PORT_TAKEN: "The local model server couldn't open its port.",
    EXITED: "The local model server stopped.",
    NOT_READY: "The local model didn't start in time.",
    NOT_OURS: "Another program answered on the local model server's port.",
    NOT_STARTED: "The local model server couldn't be started.",
}
# What llama-server prints for a failure, lower-cased, checked in order: running out of
# memory while loading also prints "failed to load model".
_SIGNS: Final = (
    (OUT_OF_MEMORY, ("out of memory", "outofdevicememory", "failed to allocate")),
    (LOAD_FAILED, ("failed to load model", "error loading model")),
    (PORT_TAKEN, ("couldn't bind",)),
)


def unavailable(code: str) -> ProviderUnavailableError:
    """The error for a server failure: its static message and its code."""
    return ProviderUnavailableError(_MESSAGES[code], provider_error_code=code)


def classify(lines: Sequence[str]) -> str:
    """The failure llama-server's output shows; EXITED when it shows none it knows."""
    text = "\n".join(lines).lower()
    for code, signs in _SIGNS:
        if any(sign in text for sign in signs):
            return code
    return EXITED


def free_port() -> int:
    """A port nothing listens on now on 127.0.0.1; the server binds it moments later."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


if sys.platform == "win32":
    import ctypes
    import functools
    from ctypes import wintypes

    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
    _EXTENDED_LIMIT_INFORMATION = 9  # JobObjectExtendedLimitInformation
    _KILL_ON_JOB_CLOSE = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    _SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    @functools.cache
    def _kernel32() -> ctypes.WinDLL:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        return kernel32

    def _contain(pid: int) -> int | None:
        """A Job Object holding the process, which Windows ends when the job's last handle
        closes: when the server is stopped, or when Forewit exits, a crash included. None
        when Windows refused one."""
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        process = kernel32.OpenProcess(_SET_QUOTA_AND_TERMINATE, False, pid)
        try:
            if (
                process
                and kernel32.SetInformationJobObject(
                    job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
                )
                and kernel32.AssignProcessToJobObject(job, process)
            ):
                return int(job)
        finally:
            if process:
                kernel32.CloseHandle(process)
        kernel32.CloseHandle(job)
        return None

    def _release_job(job: int) -> None:
        _kernel32().CloseHandle(job)

else:
    _NO_WINDOW = 0

    def _contain(pid: int) -> int | None:
        return None  # No Job Objects: a record lets the next start end a leftover server.

    def _release_job(job: int) -> None:
        pass


def _command_line(pid: int) -> str | None:
    """The process's command line from ps, or None when it has ended or can't be read."""
    try:
        done = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = done.stdout.strip()
    return line if done.returncode == 0 and line else None


def _terminate(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def _forget(record: Path) -> None:
    """Remove the record if it can be. One that can't is harmless: it names no running
    server, or the next start checks the process before ending it."""
    try:
        record.unlink(missing_ok=True)
    except OSError:  # A parent that isn't a folder, or no permission.
        logger.info("Local model server record could not be removed")


def end_leftover(
    record: Path,
    *,
    command_line: Callable[[int], str | None] = _command_line,
    terminate: Callable[[int], None] = _terminate,
) -> bool:
    """End a server that an earlier Forewit started and never stopped (it crashed), when the
    process the record names still runs the recorded program on the recorded port. Removes
    the record either way; True when a server was ended."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        data = None
    _forget(record)
    if not isinstance(data, dict):
        return False
    pid, program, port = data.get("pid"), data.get("program"), data.get("port")
    if type(pid) is not int or not isinstance(program, str) or type(port) is not int:
        return False
    line = command_line(pid)
    if line is None or not line.startswith(program) or f"--port {port}" not in line:
        return False
    try:
        terminate(pid)
    except OSError:
        return False
    logger.info("Ended a local model server an earlier run left running")
    return True


class LlamaServer:
    """One llama-server process serving one model, started once by ``start()`` (or ``async
    with``) and ended by ``stop()``; a ModelServer while it runs.

    ``program`` is the command that runs llama-server, its executable's path; ``extra`` adds
    flags, and the hardening flags come after them, so they win. ``record`` is where the
    process is recorded when no Job Object holds it. ``on_output`` receives each line the
    server prints, for a developer's harness; the app passes none.
    """

    def __init__(
        self,
        program: Sequence[str],
        model_file: Path,
        *,
        context: int,
        gpu_layers: int,
        threads: int | None = None,
        extra: Sequence[str] = (),
        record: Path | None = None,
        on_output: Callable[[str], None] | None = None,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not program:
            raise ValueError("the program needs llama-server's executable")
        self._program = tuple(program)
        self._model_file = model_file
        self._options = (
            "-c",
            str(context),
            "-ngl",
            str(gpu_layers),
            *(() if threads is None else ("-t", str(threads))),
            *extra,
        )
        self._record = record
        self._on_output = on_output
        self._ready_timeout = ready_timeout
        self._clock = clock
        self._sleep = sleep
        self._api_key = secrets.token_urlsafe(32)
        self._started = False
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._job: int | None = None
        self._output: deque[str] = deque(maxlen=OUTPUT_LINES)
        self._output_lock = threading.Lock()
        self.port = 0
        self.command: tuple[str, ...] = ()  # Holds the API key: never log or store it as is.
        self.cold_start_seconds = 0.0

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def output(self) -> tuple[str, ...]:
        """The last lines the server printed, for a developer; never log or store them."""
        with self._output_lock:
            return tuple(self._output)

    async def available(self) -> bool:
        return self.running

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Endpoint]:
        if not self.running:
            raise unavailable(EXITED if self._started else NOT_STARTED)
        yield Endpoint(self.url, self._api_key)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch the server and wait until it answers as ours.

        Raises ProviderUnavailableError, with a static message and a code, when it can't be
        launched, exits, isn't ready within ``ready_timeout`` or another program answers on
        its port; the server is stopped then, and when starting is cancelled.
        """
        if self._started:
            raise RuntimeError("a LlamaServer starts once; make another for a fresh key")
        self._started = True
        if self._record is not None:
            await asyncio.to_thread(end_leftover, self._record)
        self.port = free_port()
        self.command = (
            *self._program,
            "-m",
            str(self._model_file),
            *self._options,
            "--host",
            HOST,
            "--port",
            str(self.port),
            "--parallel",
            "1",
            "--jinja",
            "--no-ui",
            "--api-key",
            self._api_key,
        )
        started = self._clock()
        try:
            # On the loop's thread: Popen returns in milliseconds, and a cancellation can't
            # then leave a process that nothing will stop.
            self._launch()
            await self._wait_until_ready()
        except BaseException:
            self._release()
            raise
        self.cold_start_seconds = self._clock() - started
        logger.info("Local model server ready in %.1f s", self.cold_start_seconds)

    async def stop(self) -> None:
        """Stop the server and wait for it to exit; nothing happens when it isn't running."""
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, STOP_TIMEOUT_SECONDS)
            if self._reader is not None:
                await asyncio.to_thread(self._reader.join, STOP_TIMEOUT_SECONDS)
        finally:
            self._release()
        logger.info("Local model server stopped")

    def _launch(self) -> None:
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_NO_WINDOW,
            )
        except OSError:
            logger.info("Local model server could not be launched")
            raise unavailable(NOT_STARTED) from None
        self._process = process
        self._job = _contain(process.pid)
        if self._job is None and sys.platform == "win32":
            logger.info("Local model server runs outside a Job Object")
        if self._job is None and self._record is not None:
            record = {"pid": process.pid, "program": self._program[0], "port": self.port}
            try:
                self._record.parent.mkdir(parents=True, exist_ok=True)
                self._record.write_text(json.dumps(record), encoding="utf-8")
            except OSError:  # Only a crash would need it; the server still runs.
                logger.info("Local model server could not be recorded")
        assert process.stdout is not None
        self._reader = threading.Thread(
            target=self._read, args=(process.stdout,), name="forewit-llama-output", daemon=True
        )
        self._reader.start()

    def _read(self, stream: IO[str]) -> None:
        """Keep the server's last lines until it exits (then the pipe ends)."""
        with stream:
            for line in stream:
                text = line.rstrip("\r\n")
                with self._output_lock:
                    self._output.append(text)
                if self._on_output is not None:
                    try:
                        self._on_output(text)
                    except Exception:  # A developer's sink never stops the server's output.
                        self._on_output = None

    async def _wait_until_ready(self) -> None:
        process = self._process
        assert process is not None
        deadline = self._clock() + self._ready_timeout
        async with httpx.AsyncClient(
            transport=LoopbackGuard(), trust_env=False, timeout=_CHECK_TIMEOUT
        ) as client:
            while True:
                # The process first: a program that took the port would answer the checks
                # for a server that couldn't bind it and is exiting.
                if process.poll() is not None:
                    raise await self._exited()
                ours = await self._ours(client)
                if ours:
                    break
                if ours is False:
                    logger.info("Local model server's port answered without its key")
                    raise unavailable(NOT_OURS)
                if self._clock() > deadline:
                    logger.info("Local model server not ready in %.0f s", self._ready_timeout)
                    raise unavailable(NOT_READY)
                await self._sleep(_POLL_SECONDS)
        if process.poll() is not None:
            raise await self._exited()

    async def _ours(self, client: httpx.AsyncClient) -> bool | None:
        """Whether the server on the port is ready and ours: it refuses a wrong key where
        emails go and accepts Forewit's on a request that runs no model (llama-server leaves
        only its health checks open to every caller). None while it can't tell: the server
        is loading, or didn't answer in time on a busy computer."""
        wrong = {"Authorization": f"Bearer {secrets.token_urlsafe(32)}"}
        right = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if (await client.get(f"{self.url}/health")).status_code != 200:
                return None
            refused = await client.post(f"{self.url}/v1/chat/completions", json={}, headers=wrong)
            accepted = await client.get(f"{self.url}/props", headers=right)
        except httpx.HTTPError:
            return None
        return refused.status_code == 401 and accepted.status_code == 200

    async def _exited(self) -> ProviderUnavailableError:
        """The error for a server that exited while starting, from what it printed."""
        if self._reader is not None:
            await asyncio.to_thread(self._reader.join, STOP_TIMEOUT_SECONDS)
        code = classify(self.output())
        logger.info("Local model server exited while starting: %s", code)
        return unavailable(code)

    def _release(self) -> None:
        """End the process at once if it still runs, and free its job and record."""
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.kill()
        if self._job is not None:
            _release_job(self._job)
            self._job = None
        if self._record is not None and process is not None:
            _forget(self._record)
