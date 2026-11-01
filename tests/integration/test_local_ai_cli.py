"""forewit-gmail-diagnostic local-ai: status, setup and remove through main(), with a small
synthetic manifest whose downloads come from a mock transport behind the real host guard.
No process runs: the memory reading and llama-server's device list are stand-ins."""

import hashlib
import io
import random
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from mailbrief.diagnostics import gmail
from mailbrief.infra.data_lock import held_lock
from mailbrief.providers.local.device import GpuDevice
from mailbrief.providers.local.manifest import Manifest, Model, PinnedFile, Runtime
from mailbrief.providers.local.transport import AllowedHostsGuard
from mailbrief.services.local_setup import (
    BUSY,
    CHUNK_BYTES,
    LOCK,
    MISMATCH,
    RECORD,
    RETRY_DELAYS,
    UNLISTED,
    UNREACHABLE,
    UNSUPPORTED,
    LocalSetup,
    SetupProgress,
    SetupStage,
)

GIB = 1024**3
HOSTS = frozenset({"github.com", "huggingface.co", ".hf.co"})
RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b-test/llama-b-test.zip"
MODEL_URL = "https://huggingface.co/test/model/resolve/0123abc/test-model.gguf"
CDN_URL = "https://us.aws.cdn.hf.co/xet/test-model.gguf"  # Where the model URL redirects.
EVIL_URL = "https://hf.co.evil.test/test-model.gguf"
SERVER_BYTES = b"stand-in llama-server"
RUNTIME_FILES = {"llama-server.exe": SERVER_BYTES, "ggml-base.dll": b"stand-in library"}
MODEL_BYTES = random.Random(20261030).randbytes(2 * CHUNK_BYTES + 4321)
GPU = GpuDevice(id="Vulkan0", name="Test GPU", memory_mib=8192, free_mib=8000)
READY = "Local AI: ready (llama.cpp b-test, Test-Model-Q4)"
WITH_GPU = "About 15 seconds per email on this computer (Test GPU)."
WITHOUT_GPU = "About 30 seconds per email: no graphics processor the model can use was found."
LOW_MEMORY = (
    "This computer has 8 GB of memory; the local model was measured with 16 GB, so it may be "
    "slower or fail to load."
)
FILE_FAILED = (
    "Local database or file operation failed. Check access and retry; no mailbox changes made."
)


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _pinned(name: str, url: str, data: bytes) -> PinnedFile:
    return PinnedFile(
        name=name,
        url=url,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        license="MIT",
        source="synthetic test file",
    )


ARCHIVE = _zip(RUNTIME_FILES)
MANIFEST = Manifest(
    runtime=Runtime(
        archive=_pinned("llama-b-test.zip", RUNTIME_URL, ARCHIVE),
        version="b-test",
        executable="llama-server.exe",
        unpacked_size=sum(len(data) for data in RUNTIME_FILES.values()),
    ),
    model=Model(file=_pinned("test-model.gguf", MODEL_URL, MODEL_BYTES), name="Test-Model-Q4"),
    hosts=HOSTS,
)


def sized(runtime: int, model: int) -> Manifest:
    """MANIFEST with other pinned sizes, for status only: nothing is downloaded from it."""
    return replace(
        MANIFEST,
        runtime=replace(MANIFEST.runtime, archive=replace(MANIFEST.runtime.archive, size=runtime)),
        model=replace(MANIFEST.model, file=replace(MANIFEST.model.file, size=model)),
    )


class Computer:
    """What local_ai() meets: the manifest, the download hosts (a mock transport behind the
    real guard), the disk, the memory, the devices llama-server would list and the owner's
    answers. It records requests, retry sleeps, prompts and device listings."""

    def __init__(self) -> None:
        self.manifest: Manifest | None = MANIFEST
        self.bodies: dict[str, bytes] = {RUNTIME_URL: ARCHIVE, CDN_URL: MODEL_BYTES}
        self.redirects: dict[str, str] = {MODEL_URL: CDN_URL}
        self.status: int | None = None  # When set, every download answers it.
        self.connection_fails = False
        self.free = 10**12
        self.disk_error: OSError | None = None
        self.memory: int | None = 32 * GIB
        self.devices: tuple[GpuDevice, ...] | None = (GPU,)
        self.answers: list[str] = []
        self.prompts: list[str] = []
        self.requests: list[httpx.URL] = []
        self.sleeps: list[float] = []
        self.listed: list[Path] = []
        self.guards: list[AllowedHostsGuard] = []

    @property
    def hosts(self) -> list[str]:
        return [url.host for url in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        if self.connection_fails:
            raise httpx.ConnectError("connection refused", request=request)
        if self.status is not None:
            return httpx.Response(self.status)
        url = str(request.url)
        if url in self.redirects:
            return httpx.Response(302, headers={"Location": self.redirects[url]})
        body = self.bodies[url]
        # A stream, as a real transport returns: content= would be read already.
        return httpx.Response(
            200, headers={"Content-Length": str(len(body))}, stream=httpx.ByteStream(body)
        )

    def client(self, hosts: frozenset[str]) -> httpx.AsyncClient:
        guard = AllowedHostsGuard(hosts, inner=httpx.MockTransport(self.handle))
        self.guards.append(guard)
        return httpx.AsyncClient(transport=guard, follow_redirects=True)

    def free_space(self, folder: Path) -> int:
        if self.disk_error is not None:
            raise self.disk_error
        return self.free

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def local_setup(self, folder: Path, manifest: Manifest | None) -> LocalSetup:
        return LocalSetup(
            folder, manifest, client=self.client, free_space=self.free_space, sleep=self.sleep
        )

    def list_devices(self, executable: Path) -> tuple[GpuDevice, ...] | None:
        self.listed.append(executable)
        return self.devices

    async def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            pytest.fail(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)


@pytest.fixture
def computer(monkeypatch: pytest.MonkeyPatch) -> Computer:
    fake = Computer()
    monkeypatch.setattr(gmail, "manifest_for", lambda: fake.manifest)
    monkeypatch.setattr(gmail, "LocalSetup", fake.local_setup)
    monkeypatch.setattr(gmail, "total_memory_bytes", lambda: fake.memory)
    monkeypatch.setattr(gmail, "list_devices", fake.list_devices)
    monkeypatch.setattr(gmail, "_ask", fake.ask)
    return fake


def local_ai(folder: Path, *arguments: str) -> int:
    return gmail.main(["local-ai", *arguments, "--folder", str(folder)])


def server_path(folder: Path) -> Path:
    return folder / "runtime" / "b-test" / "llama-server.exe"


@pytest.mark.parametrize(("memory", "warned"), [(8 * GIB, True), (16 * GIB, False), (None, False)])
def test_status_before_setup_shows_the_size_and_any_memory_warning(
    tmp_path: Path,
    computer: Computer,
    capsys: pytest.CaptureFixture[str],
    memory: int | None,
    warned: bool,
) -> None:
    computer.manifest = sized(500_000_000, 2_000_000_000)
    computer.memory = memory

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [
        "Local AI: not set up (0% of 2.5 GB downloaded). Run local-ai setup.",
        *([LOW_MEMORY] if warned else []),
    ]
    assert computer.requests == [] and computer.guards == []
    assert computer.prompts == [] and computer.listed == []
    assert list(tmp_path.iterdir()) == []  # Status writes nothing.


def test_status_shows_the_share_downloaded_rounded_down(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    computer.manifest = sized(1_000, 2_000)
    part = tmp_path / "downloads" / "test-model.gguf.part"
    part.parent.mkdir()
    part.write_bytes(b"x" * 1_999)  # 1,999 of 3,000 bytes: 66.6%.

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [
        "Local AI: not set up (66% of 0.0 GB downloaded). Run local-ai setup."
    ]


def test_status_on_an_unsupported_computer(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    computer.manifest = None

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [f"Local AI: not available. {UNSUPPORTED}"]
    assert computer.requests == [] and computer.listed == []


@pytest.mark.parametrize("options", [(), ("--yes",)])
def test_setup_on_an_unsupported_computer_exits_3_and_asks_nothing(
    tmp_path: Path,
    computer: Computer,
    capsys: pytest.CaptureFixture[str],
    options: tuple[str, ...],
) -> None:
    computer.manifest = None

    assert local_ai(tmp_path, "setup", *options) == 3

    assert capsys.readouterr().out.splitlines() == [UNSUPPORTED]
    assert computer.requests == [] and computer.prompts == []


def test_setup_with_yes_downloads_unpacks_and_reports_ready(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0].startswith("Forewit will download 2 files")
    assert lines.count("Unpacking llama.cpp...") == 1
    assert 1 <= sum(line.startswith("Downloaded ") for line in lines) <= 11
    assert lines[-2:] == [READY, WITH_GPU]
    assert str(tmp_path) not in output
    assert computer.prompts == []
    # The model's pinned URL redirects to the CDN; every hop went through the guard.
    assert computer.hosts == ["github.com", "huggingface.co", "us.aws.cdn.hf.co"]
    assert [dict(guard.sent) for guard in computer.guards] == [
        {"github.com": 1, "huggingface.co": 1, "us.aws.cdn.hf.co": 1}
    ]
    assert computer.listed == [server_path(tmp_path)]
    assert server_path(tmp_path).read_bytes() == SERVER_BYTES
    assert (tmp_path / "model" / "test-model.gguf").read_bytes() == MODEL_BYTES


@pytest.mark.parametrize("answer", ["y", "YES"])
def test_setup_downloads_after_the_owner_says_yes(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    computer.answers = [answer]

    assert local_ai(tmp_path, "setup") == 0

    assert capsys.readouterr().out.splitlines()[-2:] == [READY, WITH_GPU]
    assert computer.prompts == ["Download? [y/N] "]
    assert len(computer.requests) == 3


@pytest.mark.parametrize("answer", ["no", "n", "", "maybe"])
def test_setup_declined_downloads_nothing_and_exits_6(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    computer.memory = 8 * GIB
    computer.answers = [answer]

    assert local_ai(tmp_path, "setup") == 6

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Forewit will download 2 files")
    assert lines[-2:] == [LOW_MEMORY, "Nothing downloaded."]
    assert computer.prompts == ["Download? [y/N] "]
    assert computer.requests == [] and computer.guards == []
    assert list(tmp_path.iterdir()) == []


def test_setup_when_ready_asks_nothing_and_downloads_nothing(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    capsys.readouterr()

    # No --yes and no answer queued: a prompt would fail the test.
    assert local_ai(tmp_path, "setup") == 0

    assert capsys.readouterr().out.splitlines() == [READY, WITH_GPU]
    assert computer.prompts == []
    assert len(computer.requests) == 3 and len(computer.guards) == 1


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("unavailable", UNREACHABLE),
        ("connection", UNREACHABLE),
        ("refused", "The download server answered HTTP 404. Try again later."),
        ("mismatch", MISMATCH),
        ("unlisted", UNLISTED),
    ],
)
def test_a_failed_download_exits_4_with_a_static_message(
    tmp_path: Path,
    computer: Computer,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    message: str,
) -> None:
    if failure == "unavailable":
        computer.status = 503
    elif failure == "connection":
        computer.connection_fails = True
    elif failure == "refused":
        computer.status = 404
    elif failure == "mismatch":
        computer.bodies[CDN_URL] = MODEL_BYTES[::-1]  # Same size, other bytes.
    else:
        computer.redirects[MODEL_URL] = EVIL_URL

    assert local_ai(tmp_path, "setup", "--yes") == 4

    output = capsys.readouterr().out
    assert output.splitlines()[-1] == message
    assert READY not in output and str(tmp_path) not in output
    retried = failure in {"unavailable", "connection"}
    assert computer.sleeps == (list(RETRY_DELAYS) if retried else [])
    assert "hf.co.evil.test" not in computer.hosts  # Refused before it was sent.
    assert not (tmp_path / "model").exists()
    assert not (tmp_path / "downloads" / "test-model.gguf.part").exists()


def test_too_little_disk_space_exits_3_before_any_download(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    computer.free = 0

    assert local_ai(tmp_path, "setup", "--yes") == 3

    assert capsys.readouterr().out.splitlines()[-1] == (
        "Local AI setup needs 0.5 GB of free disk space; 0.0 GB is free."
    )
    assert computer.requests == [] and computer.guards == []


@pytest.mark.parametrize("action", ["setup", "remove"])
def test_a_setup_running_elsewhere_exits_5(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    with held_lock(tmp_path / LOCK, "held by the test"):
        assert local_ai(tmp_path, action, "--yes") == 5

    assert capsys.readouterr().out.splitlines()[-1] == BUSY
    assert computer.requests == []


def test_a_disk_that_cannot_be_read_exits_5(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    computer.disk_error = PermissionError("access denied")

    assert local_ai(tmp_path, "setup", "--yes") == 5

    assert capsys.readouterr().out.splitlines()[-1] == FILE_FAILED
    assert computer.requests == []


def test_remove_with_yes_deletes_everything_setup_left(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    (tmp_path / "downloads").mkdir(exist_ok=True)
    (tmp_path / "downloads" / "left-over.part").write_bytes(b"part")
    capsys.readouterr()

    assert local_ai(tmp_path, "remove", "--yes") == 0

    assert capsys.readouterr().out.splitlines() == ["Local AI files removed."]
    assert computer.prompts == []
    for name in ("runtime", "model", "downloads", RECORD):
        assert not (tmp_path / name).exists()
    assert local_ai(tmp_path, "status") == 0
    assert capsys.readouterr().out.startswith("Local AI: not set up (0% of ")


@pytest.mark.parametrize("answer", ["no", ""])
def test_remove_declined_keeps_everything_and_exits_6(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    capsys.readouterr()
    computer.answers = [answer]

    assert local_ai(tmp_path, "remove") == 6

    assert capsys.readouterr().out.splitlines() == ["Nothing removed."]
    assert computer.prompts == ["Remove the local AI files? [y/N] "]
    assert local_ai(tmp_path, "status") == 0
    assert capsys.readouterr().out.splitlines() == [READY, WITH_GPU]


@pytest.mark.parametrize("devices", [(), None])
def test_ready_status_without_a_usable_graphics_processor(
    tmp_path: Path,
    computer: Computer,
    capsys: pytest.CaptureFixture[str],
    devices: tuple[GpuDevice, ...] | None,
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    capsys.readouterr()
    computer.devices = devices  # None: llama-server couldn't list them.

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [READY, WITHOUT_GPU]
    assert computer.listed == [server_path(tmp_path)] * 2


def test_ready_status_adds_the_memory_warning(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    capsys.readouterr()
    computer.memory = 8 * GIB

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [READY, f"{WITH_GPU} {LOW_MEMORY}"]


def test_status_after_a_set_up_file_changed_says_damaged(
    tmp_path: Path, computer: Computer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert local_ai(tmp_path, "setup", "--yes") == 0
    capsys.readouterr()
    (tmp_path / "model" / "test-model.gguf").write_bytes(MODEL_BYTES[:-1])

    assert local_ai(tmp_path, "status") == 0

    assert capsys.readouterr().out.splitlines() == [
        "Local AI: damaged. Run local-ai setup to download what changed again."
    ]
    assert computer.listed == [server_path(tmp_path)]  # Only after setup.


# The progress printer, fed what setup reports.

TOTAL = 2_500_000_000
RUNTIME_SHARE = 15  # In tenths of a percent: the runtime is about 1.5% of the real total.


def downloading(stage: SetupStage, start: int, end: int) -> list[SetupProgress]:
    """Reports every 0.5% from ``start`` to ``end`` tenths of a percent, each one twice."""
    done = [TOTAL * tenths // 1000 for tenths in range(start, end + 1, 5)]
    return [SetupProgress(stage, value, TOTAL) for value in done for _ in range(2)]


def a_whole_setup() -> list[SetupProgress]:
    """The runtime, unpacking, then the model from one chunk past where the runtime ended."""
    runtime_end = TOTAL * RUNTIME_SHARE // 1000
    model_start = runtime_end + CHUNK_BYTES
    return [
        *downloading(SetupStage.RUNTIME, 5, RUNTIME_SHARE),
        SetupProgress(SetupStage.UNPACKING, runtime_end, TOTAL),
        SetupProgress(SetupStage.MODEL, model_start, TOTAL),
        *downloading(SetupStage.MODEL, 20, 1000),
    ]


def printed(updates: list[SetupProgress], capsys: pytest.CaptureFixture[str]) -> list[str]:
    show = gmail._setup_progress()
    for update in updates:
        show(update)
    return capsys.readouterr().out.splitlines()


def test_progress_prints_one_line_per_ten_percent(capsys: pytest.CaptureFixture[str]) -> None:
    # Only the model: the runtime was already set up.
    lines = printed(downloading(SetupStage.MODEL, 5, 1000), capsys)

    assert len(lines) == 11  # 0% to 100%.
    assert lines[0] == "Downloaded 0.0 GB of 2.5 GB"
    assert lines[-1] == "Downloaded 2.5 GB of 2.5 GB"


def test_progress_prints_unpacking_once_between_the_downloads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = printed(a_whole_setup(), capsys)

    assert lines.count("Unpacking llama.cpp...") == 1
    assert lines[0] == "Downloaded 0.0 GB of 2.5 GB"
    assert lines[1] == "Unpacking llama.cpp..."
    assert lines[-1] == "Downloaded 2.5 GB of 2.5 GB"


def test_progress_never_repeats_a_step_after_unpacking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = printed(a_whole_setup(), capsys)

    downloaded = [line for line in lines if line != "Unpacking llama.cpp..."]
    assert len(downloaded) == 11  # One per step, 0% to 100%.
