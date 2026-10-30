"""The llama-server side process, run against a stand-in program on loopback: hardening, the
check that the server on the port is ours, startup failures, stopping, and what a crash
leaves."""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from mailbrief.domain.analysis import AnalysisRequest
from mailbrief.domain.messages import EmailContact
from mailbrief.ports.errors import ProviderUnavailableError
from mailbrief.providers.local import server as server_module
from mailbrief.providers.local.provider import LocalModelProvider
from mailbrief.providers.local.server import (
    EXITED,
    LOAD_FAILED,
    NOT_OURS,
    NOT_READY,
    NOT_STARTED,
    OUT_OF_MEMORY,
    PORT_TAKEN,
    LlamaServer,
    end_leftover,
    unavailable,
)
from mailbrief.providers.local.transport import LoopbackGuard

STAND_IN = Path(__file__).with_name("stand_in_llama_server.py")
SRC = Path(__file__).resolve().parents[4] / "src"
MARKER = "SERVER-OUTPUT-MARKER prompt words"
# Starts a server for a stand-in, prints its port, then waits to be killed like a crash.
OWNER = """
import asyncio, sys
from pathlib import Path
from mailbrief.providers.local.server import LlamaServer

async def main() -> None:
    record = None if sys.argv[3] == "-" else Path(sys.argv[3])
    server = LlamaServer(
        [sys.executable, "-I", sys.argv[1]], Path(sys.argv[2]), context=512, gpu_layers=0,
        record=record,
    )
    await server.start()
    print(server.port, flush=True)
    await asyncio.sleep(3600)

asyncio.run(main())
"""


def scenario(tmp_path: Path, **values: object) -> Path:
    """The stand-in's "model file": what it does."""
    path = tmp_path / "scenario.gguf"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def stand_in(model_file: Path, **options: Any) -> LlamaServer:
    return LlamaServer(
        [sys.executable, "-I", str(STAND_IN)], model_file, context=512, gpu_layers=0, **options
    )


def make_request() -> AnalysisRequest:
    return AnalysisRequest(
        message_key="0000abcd",
        subject="Form",
        sender=EmailContact(name="Alex", address="alex@example.com"),
        received_at_utc=datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
        timezone_name="America/Toronto",
        body_text="Hi Sam, please send the signed form back by Friday.",
        body_truncated=False,
    )


def accepts(port: int) -> bool:
    """Whether the port takes a connection; Windows takes about 2 seconds to refuse one."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3.0):
            return True
    except OSError:
        return False


async def until(condition: Callable[[], bool], seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.1)
    return False


async def test_a_started_server_answers_as_ours_and_analyzes_on_loopback(tmp_path: Path) -> None:
    async with stand_in(scenario(tmp_path, loading_seconds=0.5)) as server:
        assert await server.available()
        assert server.url == f"http://127.0.0.1:{server.port}"
        assert server.cold_start_seconds >= 0.5
        guard = LoopbackGuard()
        async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
            model = LocalModelProvider(client, server, model="stand-in", max_output_tokens=512)
            response = await model.analyze([make_request()])
        assert [candidate.message_key for candidate in response.candidates] == ["0000abcd"]
        assert dict(guard.sent) == {"127.0.0.1": 1}
        port = server.port
    assert not server.running and server.pid is None
    assert await until(lambda: not accepts(port))
    await server.stop()  # Stopping twice does nothing.


async def test_each_server_has_a_fresh_key_and_its_hardening_flags_win(tmp_path: Path) -> None:
    model_file = scenario(tmp_path)
    first = stand_in(model_file, extra=["--host", "0.0.0.0", "--api-key", "shared"])
    second = stand_in(model_file, threads=2)
    async with first, second, first.lease() as one, second.lease() as other:
        assert one.api_key and other.api_key and one.api_key != other.api_key
        assert first.command[-10:] == (
            "--host",
            "127.0.0.1",
            "--port",
            str(first.port),
            "--parallel",
            "1",
            "--jinja",
            "--no-ui",
            "--api-key",
            one.api_key,
        )
        assert second.command[second.command.index("-t") + 1] == "2"
    with pytest.raises(RuntimeError, match="starts once"):
        await first.start()


@pytest.mark.parametrize(
    ("printed", "code"),
    [
        (["E llama_model_load: error loading model: llama_model_loader: failed"], LOAD_FAILED),
        (
            ["ggml_vulkan: ErrorOutOfDeviceMemory", "E srv load_model: failed to load model"],
            OUT_OF_MEMORY,
        ),
        (["E srv start: couldn't bind HTTP server socket, port: 1"], PORT_TAKEN),
        ([MARKER], EXITED),
    ],
)
async def test_a_server_that_exits_while_starting_is_reported_by_a_code(
    tmp_path: Path, printed: list[str], code: str
) -> None:
    server = stand_in(scenario(tmp_path, print=printed, exit=1))
    with pytest.raises(ProviderUnavailableError) as raised:
        await server.start()
    assert raised.value.provider_error_code == code
    assert str(raised.value) == str(unavailable(code))
    assert not any(line in str(raised.value) for line in printed)
    assert not server.running and server.output() == tuple(printed)


async def test_a_program_that_cant_be_launched_is_reported(tmp_path: Path) -> None:
    server = LlamaServer(
        [str(tmp_path / "no-such-server")], tmp_path / "m.gguf", context=1, gpu_layers=0
    )
    with pytest.raises(ProviderUnavailableError) as raised:
        await server.start()
    assert raised.value.provider_error_code == NOT_STARTED
    with pytest.raises(ProviderUnavailableError) as leased:
        async with server.lease():
            pass
    assert leased.value.provider_error_code == EXITED


async def test_a_server_never_started_cannot_be_leased(tmp_path: Path) -> None:
    server = stand_in(scenario(tmp_path))
    assert not await server.available()
    with pytest.raises(ProviderUnavailableError) as raised:
        async with server.lease():
            pass
    assert raised.value.provider_error_code == NOT_STARTED


async def test_a_check_that_times_out_on_a_busy_computer_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_CHECK_TIMEOUT", httpx.Timeout(1.0))
    async with stand_in(scenario(tmp_path, slow_first_props=3)) as server:
        assert await server.available()


async def test_a_server_not_ready_in_time_is_stopped(tmp_path: Path) -> None:
    server = stand_in(scenario(tmp_path, loading_seconds=60), ready_timeout=1.0)
    with pytest.raises(ProviderUnavailableError) as raised:
        await server.start()
    assert raised.value.provider_error_code == NOT_READY
    assert await until(lambda: not accepts(server.port))


class Impostor(BaseHTTPRequestHandler):
    """Another program that holds the port and answers everything."""

    seen: ClassVar[list[tuple[str, str, bytes]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _answer(self, method: str) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.seen.append((method, self.path, body))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self) -> None:
        self._answer("GET")

    def do_POST(self) -> None:
        self._answer("POST")


@pytest.fixture
def impostor() -> Iterator[ThreadingHTTPServer]:
    Impostor.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Impostor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


async def test_another_program_on_the_port_is_never_sent_an_email(
    tmp_path: Path, impostor: ThreadingHTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = int(impostor.server_address[1])
    monkeypatch.setattr(server_module, "free_port", lambda: port)
    # The stand-in waits before binding, so it is still alive when the impostor answers.
    server = stand_in(scenario(tmp_path, bind_delay=5))
    with pytest.raises(ProviderUnavailableError) as raised:
        await server.start()
    assert raised.value.provider_error_code == NOT_OURS
    assert not server.running
    posts = [body for method, _, body in Impostor.seen if method == "POST"]
    assert posts == [b"{}"]


async def test_cancelling_the_start_stops_the_server(tmp_path: Path) -> None:
    server = stand_in(scenario(tmp_path, loading_seconds=60))
    starting = asyncio.create_task(server.start())
    assert await until(lambda: server.port != 0 and accepts(server.port))
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert not server.running
    assert await until(lambda: not accepts(server.port))


async def test_a_server_that_crashes_ends_its_lease(tmp_path: Path) -> None:
    async with stand_in(scenario(tmp_path, crash_on_chat=True)) as server:
        async with httpx.AsyncClient(transport=LoopbackGuard(), trust_env=False) as client:
            model = LocalModelProvider(client, server, model="stand-in", max_output_tokens=512)
            with pytest.raises(ProviderUnavailableError):
                await model.analyze([make_request()])
        assert await until(lambda: not server.running)
        assert not await server.available()
        with pytest.raises(ProviderUnavailableError) as raised:
            async with server.lease():
                pass
        assert raised.value.provider_error_code == EXITED


async def test_the_servers_output_stays_in_memory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lines: list[str] = []

    def failing_sink(line: str) -> None:
        lines.append(line)
        raise OSError("a developer's sink failed")

    async with stand_in(
        scenario(tmp_path, print=[MARKER, "second"]), on_output=failing_sink
    ) as server:
        pass
    assert server.output()[:2] == (MARKER, "second")
    assert lines == [MARKER]  # A failing sink is dropped; the output goes on.
    assert MARKER not in caplog.text and "Local model server ready" in caplog.text


async def test_a_server_is_recorded_only_where_no_job_object_holds_it(tmp_path: Path) -> None:
    record = tmp_path / "local-ai" / "server.json"
    async with stand_in(scenario(tmp_path), record=record) as server:
        if sys.platform == "win32":
            assert not record.exists()
        else:
            saved = json.loads(record.read_text(encoding="utf-8"))
            assert saved == {"pid": server.pid, "program": sys.executable, "port": server.port}
    assert not record.exists()


async def test_a_record_that_cant_be_written_doesnt_stop_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(server_module, "_contain", lambda pid: None)  # As if refused one.
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    async with stand_in(scenario(tmp_path), record=blocked / "server.json") as server:
        assert await server.available()
    assert "could not be recorded" in caplog.text


def never(pid: int) -> None:
    raise AssertionError(f"process {pid} must not be ended")


def test_a_record_under_a_file_is_no_leftover(tmp_path: Path) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    # macOS and Linux refuse the path as NotADirectoryError, which missing_ok doesn't cover.
    record = blocked / "server.json"
    assert not end_leftover(record, command_line=lambda pid: None, terminate=never)


def _record(tmp_path: Path, **values: object) -> Path:
    path = tmp_path / "server.json"
    path.write_text(
        json.dumps({"pid": 4242, "program": "/apps/llama-server", "port": 5555} | values),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("command_line", "ended"),
    [
        ("/apps/llama-server -m model.gguf --host 127.0.0.1 --port 5555 --api-key x", True),
        ("/apps/llama-server -m model.gguf --host 127.0.0.1 --port 5556", False),
        ("/usr/bin/other --port 5555", False),
        (None, False),
    ],
)
def test_a_leftover_server_is_ended_only_when_it_is_still_ours(
    tmp_path: Path, command_line: str | None, ended: bool
) -> None:
    terminated: list[int] = []
    record = _record(tmp_path)
    assert (
        end_leftover(record, command_line=lambda pid: command_line, terminate=terminated.append)
        is ended
    )
    assert terminated == ([4242] if ended else [])
    assert not record.exists()


@pytest.mark.parametrize("text", ["not json", "[]", '{"pid": "4242", "program": "x", "port": 1}'])
def test_an_unreadable_record_ends_nothing(tmp_path: Path, text: str) -> None:
    record = tmp_path / "server.json"
    record.write_text(text, encoding="utf-8")
    assert not end_leftover(record, command_line=lambda pid: "x --port 1", terminate=never)
    assert not record.exists()
    assert not end_leftover(record, terminate=never)  # No record: nothing to end.


def test_a_leftover_that_ends_on_its_own_is_not_an_error(tmp_path: Path) -> None:
    def gone(pid: int) -> None:
        raise ProcessLookupError(pid)

    record = _record(tmp_path)
    line = "/apps/llama-server --port 5555"
    assert not end_leftover(record, command_line=lambda pid: line, terminate=gone)


def _owner(model_file: Path, record: Path | None) -> subprocess.Popen[str]:
    environment = os.environ | {"PYTHONPATH": str(SRC)}
    return subprocess.Popen(
        [sys.executable, "-c", OWNER, str(STAND_IN), str(model_file), str(record or "-")],
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )


async def _owners_server_port(owner: subprocess.Popen[str]) -> int:
    assert owner.stdout is not None
    line = await asyncio.wait_for(asyncio.to_thread(owner.stdout.readline), 60)
    return int(line)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows'")
async def test_windows_ends_the_server_when_its_owner_crashes(tmp_path: Path) -> None:
    owner = _owner(scenario(tmp_path), None)
    try:
        port = await _owners_server_port(owner)
        assert accepts(port)
        owner.kill()  # No cleanup runs, as in a crash.
        owner.wait(30)
        assert await until(lambda: not accepts(port))
    finally:
        owner.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses a Job Object instead")
async def test_the_next_start_ends_a_server_a_crash_left_running(tmp_path: Path) -> None:
    record = tmp_path / "server.json"
    owner = _owner(scenario(tmp_path), record)
    try:
        port = await _owners_server_port(owner)
        owner.kill()  # No cleanup runs, as in a crash.
        owner.wait(30)
        assert accepts(port) and record.exists()
        assert end_leftover(record)
        assert await until(lambda: not accepts(port))
    finally:
        owner.kill()
