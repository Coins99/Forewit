"""Local AI setup (P0-B2 Part 2): the pinned runtime and model downloaded, checked, unpacked
and recorded, from a fake download server behind the real host guard, with no network."""

import hashlib
import io
import json
import logging
import os
import random
import re
import stat
import tarfile
import zipfile
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest

from mailbrief.infra.data_lock import DataInUseError, held_lock
from mailbrief.providers.local.manifest import (
    MACOS_ARM64,
    WINDOWS_X64,
    Manifest,
    Model,
    PinnedFile,
    Runtime,
)
from mailbrief.providers.local.transport import AllowedHostsGuard, UnlistedHostError
from mailbrief.services import local_setup
from mailbrief.services.local_setup import (
    BUSY,
    LOCK,
    LOOPING,
    MISMATCH,
    RECORD,
    RETRY_DELAYS,
    SPACE_MARGIN_BYTES,
    TIMEOUT,
    UNLISTED,
    UNREACHABLE,
    UNSAFE,
    UNSUPPORTED,
    LocalSetup,
    LocalSetupError,
    NotEnoughSpaceError,
    SetupProgress,
    SetupStage,
    SetupState,
    SetupStatus,
    SetupUnsupportedError,
    download_client,
    setup_lines,
)

HOSTS = frozenset({"downloads.test", ".cdn.test"})
ZIP_URL = "https://downloads.test/runtime.zip"
TAR_URL = "https://downloads.test/runtime.tar.gz"
MODEL_URL = "https://downloads.test/model.gguf"
CDN_URL = "https://files.cdn.test/model.gguf"
CHUNK = 4096  # The download chunk size in these tests (see small_chunks).
MODEL_BYTES = random.Random(2026).randbytes(300_001)  # Not a whole number of chunks.
SIZE = len(MODEL_BYTES)
KEPT = 10 * CHUNK  # Where a cut connection stops: whole chunks, so all of it is written.
RESUME = f"bytes={KEPT}-{SIZE - 1}"
MODEL_KEY = "model/model.gguf"
RANGE = re.compile(r"bytes=(\d+)-(\d+)")

# Archives.

ZipEntry = tuple[str | zipfile.ZipInfo, bytes]
TarEntry = tuple[tarfile.TarInfo, bytes | None]


def make_zip(entries: Iterable[ZipEntry]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def zip_link(name: str) -> zipfile.ZipInfo:
    """A zip entry that unpacks as a symbolic link on Unix."""
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def tar_file(name: str, data: bytes, mode: int = 0o644) -> TarEntry:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info, data


def tar_special(name: str, kind: bytes, target: str = "") -> TarEntry:
    """A folder, a link or a device: a tar member without data."""
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    info.mode = 0o755
    return info, None


def make_tar(entries: Iterable[TarEntry]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info, data in entries:
            archive.addfile(info, None if data is None else io.BytesIO(data))
    return buffer.getvalue()


ZIP_ENTRIES: tuple[ZipEntry, ...] = (
    ("llama-server.exe", b"MZ llama-server"),
    ("ggml.dll", b"MZ ggml"),
    ("llama.dll", b"MZ llama"),
    ("licenses/", b""),
    ("licenses/LICENSE-llama.cpp", b"MIT License"),
)
ZIP = make_zip(ZIP_ENTRIES)
TOTAL = len(ZIP) + SIZE

TAR_ENTRIES: tuple[TarEntry, ...] = (
    tar_special("llama-test", tarfile.DIRTYPE),
    tar_file("llama-test/llama-server", b"llama-server for macOS", 0o755),
    tar_file("llama-test/libllama.0.dylib", b"library"),
    tar_special("llama-test/libllama.dylib", tarfile.SYMTYPE, "libllama.0.dylib"),
    tar_file("llama-test/LICENSE", b"MIT License"),
)
TAR = make_tar(TAR_ENTRIES)


def pinned(url: str, data: bytes, license: str = "MIT") -> PinnedFile:
    return PinnedFile(
        name=url.rsplit("/", 1)[1],
        url=url,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        license=license,
        source="A synthetic test file",
    )


# The fake download server.


class Body(httpx.AsyncByteStream):
    """A body with no Content-Length that sends ``data``, then ends or loses the connection."""

    def __init__(self, data: bytes, *, then_fail: bool) -> None:
        self._data = data
        self._then_fail = then_fail

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data
        if self._then_fail:
            raise httpx.ReadError("connection reset")


Cue = Callable[[httpx.Request, bytes], httpx.Response]


def honest(request: httpx.Request, data: bytes) -> tuple[int, dict[str, str], bytes]:
    """What a good server sends: the range asked for with 206, or the whole file with 200."""
    match = RANGE.fullmatch(request.headers.get("Range", ""))
    if match is None:
        return 200, {}, data
    start, end = int(match[1]), int(match[2])
    return 206, {"Content-Range": f"bytes {start}-{end}/{len(data)}"}, data[start : end + 1]


class Downloads:
    """Serves each file and honours Range; ``moved`` URLs redirect every time. It counts the
    requests per URL and records their Range headers, and the cues make it misbehave, one
    request per cue."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.moved: dict[str, str] = {}
        self.requests: Counter[str] = Counter()
        self.ranges: dict[str, list[str | None]] = {}
        self._cues: dict[str, list[Cue]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests[url] += 1
        self.ranges.setdefault(url, []).append(request.headers.get("Range"))
        if url in self.moved:
            return httpx.Response(302, headers={"Location": self.moved[url]})
        if url not in self.files:
            return httpx.Response(404)
        cues = self._cues.get(url)
        if cues:
            return cues.pop(0)(request, self.files[url])
        status, headers, body = honest(request, self.files[url])
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))

    def cue(self, url: str, answer: Cue, times: int = 1) -> None:
        self._cues.setdefault(url, []).extend([answer] * times)

    def fail(self, url: str, status: int, times: int = 1) -> None:
        self.cue(url, lambda request, data: httpx.Response(status), times)

    def refuse_connection(self, url: str) -> None:
        def refuse(request: httpx.Request, data: bytes) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        self.cue(url, refuse)

    def cut(self, url: str, keep: int, *, then_fail: bool = True) -> None:
        """Answer honestly, but stop after ``keep`` bytes."""

        def answer(request: httpx.Request, data: bytes) -> httpx.Response:
            status, headers, body = honest(request, data)
            return httpx.Response(
                status, headers=headers, stream=Body(body[:keep], then_fail=then_fail)
            )

        self.cue(url, answer)

    def ignore_range(self, url: str) -> None:
        self.cue(url, lambda request, data: httpx.Response(200, stream=httpx.ByteStream(data)))

    def wrong_bytes(self, url: str) -> None:
        self.cue(
            url,
            lambda request, data: httpx.Response(200, stream=httpx.ByteStream(bytes(len(data)))),
        )

    def wrong_length(self, url: str) -> None:
        self.cue(
            url, lambda request, data: httpx.Response(200, stream=httpx.ByteStream(data + b"!"))
        )

    def too_long(self, url: str) -> None:
        """The whole file and more, with no Content-Length to give it away."""
        self.cue(
            url,
            lambda request, data: httpx.Response(200, stream=Body(data * 2, then_fail=False)),
        )

    def wrong_range(self, url: str, content_range: str) -> None:
        def answer(request: httpx.Request, data: bytes) -> httpx.Response:
            body = honest(request, data)[2]
            headers = {"Content-Range": content_range} if content_range else {}
            return httpx.Response(206, headers=headers, stream=httpx.ByteStream(body))

        self.cue(url, answer)


class Harness:
    """A setup folder, its fake server, the sleeps setup asked for and the free space it sees."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.server = Downloads()
        self.sleeps: list[float] = []
        self.free = 10**12
        self.space_checks: list[Path] = []
        self.guards: list[AllowedHostsGuard] = []

    def pin(
        self,
        archive: bytes = ZIP,
        *,
        url: str = ZIP_URL,
        version: str = "b1",
        model: bytes = MODEL_BYTES,
    ) -> Manifest:
        """A manifest pinning ``archive`` and the model, both served."""
        tar = url.endswith(".tar.gz")
        self.server.files[url] = archive
        self.server.files[MODEL_URL] = model
        return Manifest(
            runtime=Runtime(
                archive=pinned(url, archive),
                version=version,
                executable="llama-test/llama-server" if tar else "llama-server.exe",
                unpacked_size=50_000,
            ),
            model=Model(file=pinned(MODEL_URL, model, "Apache-2.0"), name="Test-Model-Q4"),
            hosts=HOSTS,
        )

    def local(self, manifest: Manifest | None) -> LocalSetup:
        return LocalSetup(
            self.folder,
            manifest,
            client=self.client,
            free_space=self.free_space,
            sleep=self.sleep,
        )

    def client(self, hosts: frozenset[str]) -> httpx.AsyncClient:
        guard = AllowedHostsGuard(hosts, inner=httpx.MockTransport(self.server))
        self.guards.append(guard)
        return httpx.AsyncClient(transport=guard, follow_redirects=True)

    def free_space(self, folder: Path) -> int:
        self.space_checks.append(folder)
        return self.free

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def runtime(self, version: str = "b1") -> Path:
        return self.folder / "runtime" / version

    @property
    def model(self) -> Path:
        return self.folder / "model" / "model.gguf"

    def part(self, name: str = "model.gguf") -> Path:
        return self.folder / "downloads" / f"{name}.part"

    def leave_part(self, data: bytes, name: str = "model.gguf") -> Path:
        """An unfinished download left by an earlier setup."""
        path = self.part(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def record(self) -> dict[str, Any]:
        data = json.loads((self.folder / RECORD).read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data

    def write_record(self, record: dict[str, Any]) -> None:
        (self.folder / RECORD).write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small chunks: many progress reports, and a cut keeps a predictable number of bytes."""
    monkeypatch.setattr(local_setup, "CHUNK_BYTES", CHUNK)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "local-ai")


async def set_up(harness: Harness, archive: bytes = ZIP, url: str = ZIP_URL) -> LocalSetup:
    """A LocalSetup that has finished setting up."""
    setup = harness.local(harness.pin(archive, url=url))
    assert (await setup.setup()).state is SetupState.READY
    return setup


def assert_progress(seen: list[SetupProgress], archive: int, total: int) -> None:
    """Runtime, unpacking, then model; never down; ending at the total."""
    done = [update.done_bytes for update in seen]
    assert done == sorted(done)
    assert done[-1] == total
    assert {update.total_bytes for update in seen} == {total}
    stages = list(dict.fromkeys(update.stage for update in seen))
    assert stages == [SetupStage.RUNTIME, SetupStage.UNPACKING, SetupStage.MODEL]
    assert SetupProgress(SetupStage.UNPACKING, archive, total) in seen


def touch(path: Path) -> None:
    """Move the file's modification time on, so a change shows even within one clock tick."""
    later = path.stat().st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(later, later))


def count_hashes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The files setup hashes from now on."""
    hashed: list[Path] = []
    original = local_setup._hash

    def counting(path: Path) -> "hashlib._Hash":
        hashed.append(path)
        return original(path)

    monkeypatch.setattr(local_setup, "_hash", counting)
    return hashed


# Unsupported computers.


async def test_without_a_manifest_status_is_unsupported_and_setup_refuses(
    harness: Harness,
) -> None:
    setup = harness.local(None)
    assert setup.manifest is None
    assert await setup.status() == SetupStatus(SetupState.UNSUPPORTED)
    with pytest.raises(SetupUnsupportedError) as raised:
        await setup.setup()
    assert str(raised.value) == UNSUPPORTED
    assert not harness.folder.exists()
    assert not harness.server.requests


# A fresh setup.


async def test_a_fresh_zip_setup_ends_ready_with_every_file_checked_and_recorded(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    setup = harness.local(manifest)
    assert setup.manifest is manifest
    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, 0)
    seen: list[SetupProgress] = []

    status = await setup.setup(seen.append)

    runtime = harness.runtime()
    server = runtime / "llama-server.exe"
    assert status == SetupStatus(SetupState.READY, TOTAL, TOTAL, server=server, model=harness.model)
    assert server.read_bytes() == b"MZ llama-server"
    assert (runtime / "ggml.dll").read_bytes() == b"MZ ggml"
    assert (runtime / "licenses" / "LICENSE-llama.cpp").read_bytes() == b"MIT License"
    assert harness.model.read_bytes() == MODEL_BYTES
    assert_progress(seen, len(ZIP), TOTAL)
    assert not list(harness.folder.rglob("*.part"))
    assert not (harness.folder / "downloads" / "runtime.partial").exists()
    assert not (harness.folder / f"{RECORD}.tmp").exists()
    assert not (harness.folder / LOCK).exists()  # Released.
    record = harness.record()
    assert record["format"] == 1
    assert record["runtime"]["sha256"] == manifest.runtime.archive.sha256
    assert sorted(record["runtime"]["files"]) == [
        "runtime/b1/ggml.dll",
        "runtime/b1/licenses/LICENSE-llama.cpp",
        "runtime/b1/llama-server.exe",
        "runtime/b1/llama.dll",
    ]
    sha256 = manifest.model.file.sha256
    mtime = harness.model.stat().st_mtime_ns
    assert record["model"] == {"sha256": sha256, "files": {MODEL_KEY: [SIZE, mtime, sha256]}}
    assert harness.server.requests == Counter({ZIP_URL: 1, MODEL_URL: 1})
    assert harness.server.ranges == {ZIP_URL: [None], MODEL_URL: [None]}
    assert harness.sleeps == []
    assert harness.space_checks == [harness.folder]
    if os.name != "nt":
        assert os.access(server, os.X_OK)


async def test_a_fresh_tar_setup_keeps_the_library_link_inside_the_folder(
    harness: Harness,
) -> None:
    seen: list[SetupProgress] = []

    status = await harness.local(harness.pin(TAR, url=TAR_URL)).setup(seen.append)

    folder = harness.runtime() / "llama-test"
    server = folder / "llama-server"
    total = len(TAR) + SIZE
    assert status == SetupStatus(SetupState.READY, total, total, server=server, model=harness.model)
    assert server.read_bytes() == b"llama-server for macOS"
    assert harness.model.read_bytes() == MODEL_BYTES
    assert_progress(seen, len(TAR), total)
    assert not list(harness.folder.rglob("*.part"))
    assert not (harness.folder / "downloads" / "runtime.partial").exists()
    files = harness.record()["runtime"]["files"]
    assert {
        "runtime/b1/llama-test/llama-server",
        "runtime/b1/llama-test/libllama.0.dylib",
        "runtime/b1/llama-test/LICENSE",
    } <= set(files)
    link = folder / "libllama.dylib"
    if os.name != "nt" or link.is_symlink():
        assert link.is_symlink()
        assert os.readlink(link) == "libllama.0.dylib"
        assert link.resolve().is_relative_to(harness.runtime().resolve())
        assert "runtime/b1/llama-test/libllama.dylib" not in files  # Links aren't hashed.
    else:  # Windows without the symlink privilege: tarfile copies the link's target.
        assert link.read_bytes() == b"library"
    if os.name != "nt":
        assert os.access(server, os.X_OK)
        assert stat.S_IMODE(server.stat().st_mode) == 0o755


async def test_a_failing_progress_callback_does_not_stop_setup(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[SetupProgress] = []

    def broken(update: SetupProgress) -> None:
        calls.append(update)
        raise RuntimeError(str(harness.folder))

    with caplog.at_level(logging.WARNING, logger=local_setup.__name__):
        status = await harness.local(harness.pin()).setup(broken)

    assert status.state is SetupState.READY
    assert calls[-1] == SetupProgress(SetupStage.MODEL, TOTAL, TOTAL)
    assert "Setup progress callback raised RuntimeError" in caplog.text
    assert str(harness.folder) not in caplog.text


async def test_a_ready_setup_neither_checks_space_nor_downloads(harness: Harness) -> None:
    setup = await set_up(harness)
    harness.free = 0

    assert (await setup.setup()).state is SetupState.READY

    assert harness.space_checks == [harness.folder]  # Only the first setup's.
    assert len(harness.guards) == 1  # No client for the second.
    assert harness.server.requests == Counter({ZIP_URL: 1, MODEL_URL: 1})


async def test_a_new_runtime_version_replaces_the_old_one(harness: Harness) -> None:
    await set_up(harness)
    newer = make_zip([*ZIP_ENTRIES, ("ggml-vulkan.dll", b"MZ vulkan")])
    url = "https://downloads.test/runtime-b2.zip"

    status = await harness.local(harness.pin(newer, url=url, version="b2")).setup()

    assert status.state is SetupState.READY
    assert status.server == harness.runtime("b2") / "llama-server.exe"
    assert [child.name for child in (harness.folder / "runtime").iterdir()] == ["b2"]
    assert (harness.runtime("b2") / "ggml-vulkan.dll").read_bytes() == b"MZ vulkan"
    assert all(path.startswith("runtime/b2/") for path in harness.record()["runtime"]["files"])
    assert harness.server.requests == Counter({ZIP_URL: 1, url: 1, MODEL_URL: 1})


# Status, and what it hashes.


async def test_status_of_a_folder_not_made_yet_creates_nothing(harness: Harness) -> None:
    assert await harness.local(harness.pin()).status() == SetupStatus(
        SetupState.NOT_SET_UP, TOTAL, 0
    )
    assert not harness.folder.exists()


@pytest.mark.parametrize(("left", "counted"), [(12_345, 12_345), (SIZE + 99, SIZE)])
async def test_a_partial_download_counts_toward_the_downloaded_bytes(
    harness: Harness, left: int, counted: int
) -> None:
    manifest = harness.pin()
    harness.leave_part(bytes(left))
    assert await harness.local(manifest).status() == SetupStatus(
        SetupState.NOT_SET_UP, TOTAL, counted
    )


async def test_status_hashes_only_a_file_whose_time_changed_and_then_remembers_it(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = await set_up(harness)
    hashed = count_hashes(monkeypatch)

    assert (await setup.status()).state is SetupState.READY
    assert hashed == []

    touch(harness.model)  # The same bytes, a new time.
    assert (await setup.status()).state is SetupState.READY
    assert hashed == [harness.model]
    saved = harness.record()["model"]["files"][MODEL_KEY]
    assert saved[1] == harness.model.stat().st_mtime_ns

    hashed.clear()
    assert (await setup.status()).state is SetupState.READY
    assert hashed == []


async def test_a_model_changed_in_place_is_damaged_and_setup_fetches_only_the_model(
    harness: Harness,
) -> None:
    setup = await set_up(harness)
    harness.model.write_bytes(bytes(SIZE))  # The same size, other bytes.
    touch(harness.model)

    assert await setup.status() == SetupStatus(SetupState.DAMAGED, TOTAL, len(ZIP))
    assert (await setup.setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.requests == Counter({ZIP_URL: 1, MODEL_URL: 2})


async def test_a_missing_runtime_file_is_damaged_and_setup_fetches_only_the_runtime(
    harness: Harness,
) -> None:
    setup = await set_up(harness)
    (harness.runtime() / "ggml.dll").unlink()

    assert await setup.status() == SetupStatus(SetupState.DAMAGED, TOTAL, SIZE)
    assert (await setup.setup()).state is SetupState.READY

    assert (harness.runtime() / "ggml.dll").read_bytes() == b"MZ ggml"
    assert harness.server.requests == Counter({ZIP_URL: 2, MODEL_URL: 1})


@pytest.mark.parametrize(
    ("stage", "key", "value"),
    [
        pytest.param("model", None, [], id="files-not-a-mapping"),
        pytest.param("runtime", None, {}, id="server-not-recorded"),
        pytest.param("model", MODEL_KEY, "x", id="entry-not-a-list"),
        pytest.param("model", MODEL_KEY, [SIZE, 1], id="entry-too-short"),
        pytest.param("model", MODEL_KEY, [True, 1, "x"], id="size-not-a-number"),
        pytest.param("model", MODEL_KEY, [SIZE, 1, 2], id="hash-not-text"),
        pytest.param("model", MODEL_KEY, [SIZE + 1, 1, "x"], id="size-differs"),
        pytest.param("runtime", "../outside.dll", [1, 1, "x"], id="path-outside"),
        pytest.param("runtime", "runtime/b1/gone.dll", [1, 1, "x"], id="file-missing"),
    ],
)
async def test_a_record_that_does_not_check_out_is_damage(
    harness: Harness, stage: str, key: str | None, value: object
) -> None:
    setup = await set_up(harness)
    record = harness.record()
    if key is None:
        record[stage]["files"] = value
    else:
        record[stage]["files"][key] = value
    harness.write_record(record)

    assert (await setup.status()).state is SetupState.DAMAGED


@pytest.mark.parametrize(
    "entry", [{"sha256": "0" * 64, "files": {}}, "not a mapping"], ids=["other-pin", "garbled"]
)
async def test_a_record_for_another_pin_is_not_damage_and_the_model_is_adopted(
    harness: Harness, entry: object
) -> None:
    setup = await set_up(harness)
    record = harness.record()
    record["model"] = entry
    harness.write_record(record)

    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, len(ZIP))
    assert (await setup.setup()).state is SetupState.READY
    assert harness.server.requests == Counter({ZIP_URL: 1, MODEL_URL: 1})


@pytest.mark.parametrize(
    "record",
    [None, "{not json", "[]", '{"format": 99}'],
    ids=["missing", "garbage", "not-a-mapping", "other-format"],
)
async def test_a_model_in_place_without_a_readable_record_is_adopted_without_downloading(
    harness: Harness, record: str | None
) -> None:
    setup = await set_up(harness)
    if record is None:
        (harness.folder / RECORD).unlink()
    else:
        (harness.folder / RECORD).write_text(record, encoding="utf-8")

    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, 0)
    assert (await setup.setup()).state is SetupState.READY

    assert harness.server.requests == Counter({ZIP_URL: 2, MODEL_URL: 1})
    assert list(harness.record()["model"]["files"]) == [MODEL_KEY]


# Interrupted downloads and retries.


async def test_an_interrupted_download_resumes_where_it_stopped(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.cut(MODEL_URL, KEPT)
    seen: list[SetupProgress] = []

    status = await harness.local(manifest).setup(seen.append)

    assert status.state is SetupState.READY
    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.ranges[MODEL_URL] == [None, RESUME]
    assert harness.sleeps == [2.0]
    assert_progress(seen, len(ZIP), TOTAL)


async def test_a_response_that_ends_early_is_resumed(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.cut(MODEL_URL, 1000, then_fail=False)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.server.ranges[MODEL_URL] == [None, f"bytes=1000-{SIZE - 1}"]
    assert harness.sleeps == [2.0]


async def test_a_server_that_ignores_the_range_sends_the_whole_file_again(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    harness.server.cut(MODEL_URL, KEPT)
    harness.server.ignore_range(MODEL_URL)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.ranges[MODEL_URL] == [None, RESUME]
    assert harness.sleeps == [2.0]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_a_busy_server_is_tried_again_after_a_pause(harness: Harness, status: int) -> None:
    manifest = harness.pin()
    harness.server.fail(MODEL_URL, status)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.server.requests[MODEL_URL] == 2
    assert harness.sleeps == [2.0]


async def test_a_refused_connection_is_tried_again_after_a_pause(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.refuse_connection(ZIP_URL)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.server.requests == Counter({ZIP_URL: 2, MODEL_URL: 1})
    assert harness.sleeps == [2.0]


async def test_when_every_try_fails_setup_gives_up_and_keeps_what_it_downloaded(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    manifest = harness.pin()
    harness.server.cut(MODEL_URL, KEPT)
    harness.server.fail(MODEL_URL, 503, times=len(RETRY_DELAYS))
    setup = harness.local(manifest)

    with (
        caplog.at_level(logging.INFO, logger=local_setup.__name__),
        pytest.raises(LocalSetupError) as raised,
    ):
        await setup.setup()

    assert type(raised.value) is LocalSetupError
    assert str(raised.value) == UNREACHABLE
    assert harness.sleeps == list(RETRY_DELAYS)
    assert harness.server.requests[MODEL_URL] == len(RETRY_DELAYS) + 1
    assert harness.part().read_bytes() == MODEL_BYTES[:KEPT]
    assert "retrying in 2 s" in caplog.text
    assert str(harness.folder) not in caplog.text
    assert harness.folder.as_posix() not in caplog.text
    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, len(ZIP) + KEPT)

    harness.sleeps.clear()
    assert (await setup.setup()).state is SetupState.READY
    assert harness.server.ranges[MODEL_URL][-1] == RESUME
    assert harness.server.requests[ZIP_URL] == 1
    assert harness.sleeps == []


async def test_a_416_when_resuming_starts_the_download_over(harness: Harness) -> None:
    manifest = harness.pin()
    harness.leave_part(MODEL_BYTES[:5000])
    harness.server.fail(MODEL_URL, 416)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.ranges[MODEL_URL] == [f"bytes=5000-{SIZE - 1}", None]
    assert harness.sleeps == [2.0]


async def test_a_complete_correct_partial_download_is_used_without_a_request(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    harness.leave_part(MODEL_BYTES)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert MODEL_URL not in harness.server.requests
    assert not harness.part().exists()


async def test_progress_ends_at_the_total_when_a_complete_download_was_left(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    harness.leave_part(MODEL_BYTES)
    seen: list[SetupProgress] = []

    assert (await harness.local(manifest).setup(seen.append)).state is SetupState.READY

    assert seen[-1].done_bytes == seen[-1].total_bytes


@pytest.mark.parametrize("left", [bytes(SIZE), MODEL_BYTES + b"more"], ids=["wrong", "too-long"])
async def test_a_complete_but_wrong_partial_download_is_replaced(
    harness: Harness, left: bytes
) -> None:
    manifest = harness.pin()
    harness.leave_part(left)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.ranges[MODEL_URL] == [None]


# Refusals, redirects and files that don't match.


@pytest.mark.parametrize("status", [403, 404, 416])
async def test_a_refused_download_names_the_http_status_and_is_not_retried(
    harness: Harness, status: int
) -> None:
    manifest = harness.pin()
    harness.server.fail(MODEL_URL, status)

    with pytest.raises(LocalSetupError, match=f"HTTP {status}"):
        await harness.local(manifest).setup()

    assert harness.server.requests[MODEL_URL] == 1
    assert harness.sleeps == []


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/runtime.zip",
        "http://downloads.test/runtime.zip",
        "https://cdn.test/runtime.zip",  # ".cdn.test" covers hosts under it, not itself.
    ],
)
async def test_a_redirect_to_an_unlisted_host_stops_setup_before_reaching_it(
    harness: Harness, target: str
) -> None:
    manifest = harness.pin()
    harness.server.files[target] = ZIP  # Served, so only the guard keeps it out.
    harness.server.moved[ZIP_URL] = target

    with pytest.raises(LocalSetupError) as raised:
        await harness.local(manifest).setup()

    assert str(raised.value) == UNLISTED
    assert harness.server.requests == Counter({ZIP_URL: 1})
    assert not any("evil.test" in url for url in harness.server.requests)
    assert not harness.part("runtime.zip").exists()
    assert harness.sleeps == []


async def test_a_redirect_to_a_listed_cdn_host_is_followed_and_resumes_there(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    harness.server.files[CDN_URL] = MODEL_BYTES
    harness.server.moved[MODEL_URL] = CDN_URL
    harness.server.cut(CDN_URL, KEPT)

    assert (await harness.local(manifest).setup()).state is SetupState.READY

    assert harness.model.read_bytes() == MODEL_BYTES
    assert harness.server.ranges[MODEL_URL] == [None, RESUME]
    assert harness.server.ranges[CDN_URL] == [None, RESUME]
    assert harness.guards[0].sent == Counter({"downloads.test": 3, "files.cdn.test": 2})


async def test_a_redirect_loop_is_a_setup_error(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.moved[MODEL_URL] = MODEL_URL

    with pytest.raises(LocalSetupError) as raised:
        await harness.local(manifest).setup()
    assert str(raised.value) == LOOPING


async def test_wrong_bytes_are_deleted_and_reported_as_a_mismatch(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.wrong_bytes(MODEL_URL)
    setup = harness.local(manifest)

    with pytest.raises(LocalSetupError) as raised:
        await setup.setup()

    assert str(raised.value) == MISMATCH
    assert not harness.part().exists()
    assert not harness.model.exists()
    assert harness.sleeps == []
    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, len(ZIP))


async def test_a_body_longer_than_the_pin_is_a_mismatch(harness: Harness) -> None:
    manifest = harness.pin()
    harness.server.too_long(MODEL_URL)

    with pytest.raises(LocalSetupError) as raised:
        await harness.local(manifest).setup()

    assert str(raised.value) == MISMATCH
    assert not harness.part().exists()


async def test_a_whole_file_answer_of_the_wrong_length_deletes_the_partial_download(
    harness: Harness,
) -> None:
    manifest = harness.pin()
    harness.leave_part(MODEL_BYTES[:5000])
    harness.server.wrong_length(MODEL_URL)

    with pytest.raises(LocalSetupError) as raised:
        await harness.local(manifest).setup()

    assert str(raised.value) == MISMATCH
    assert harness.server.ranges[MODEL_URL] == [f"bytes=5000-{SIZE - 1}"]
    assert not harness.part().exists()


@pytest.mark.parametrize(
    "content_range",
    [
        f"bytes 0-{SIZE - 1}/{SIZE}",
        f"bytes 5000-{SIZE}/{SIZE + 1}",
        f"bytes */{SIZE}",
        "",
    ],
    ids=["wrong-start", "wrong-total", "unsatisfied", "missing"],
)
async def test_a_partial_answer_with_the_wrong_content_range_deletes_the_partial_download(
    harness: Harness, content_range: str
) -> None:
    manifest = harness.pin()
    harness.leave_part(MODEL_BYTES[:5000])
    harness.server.wrong_range(MODEL_URL, content_range)

    with pytest.raises(LocalSetupError) as raised:
        await harness.local(manifest).setup()

    assert str(raised.value) == MISMATCH
    assert not harness.part().exists()
    assert harness.sleeps == []


# Disk space.


async def test_too_little_free_space_stops_setup_before_any_request(harness: Harness) -> None:
    manifest = harness.pin()
    harness.free = 400_000_000

    with pytest.raises(NotEnoughSpaceError) as raised:
        await harness.local(manifest).setup()

    needed = TOTAL + manifest.runtime.unpacked_size + SPACE_MARGIN_BYTES
    assert (raised.value.needed, raised.value.free) == (needed, 400_000_000)
    assert str(raised.value) == "Local AI setup needs 0.5 GB of free disk space; 0.4 GB is free."
    assert not harness.server.requests
    assert harness.space_checks == [harness.folder]


async def test_free_space_counts_only_what_is_still_to_download(harness: Harness) -> None:
    setup = await set_up(harness)
    harness.model.unlink()
    harness.leave_part(MODEL_BYTES[:KEPT])
    needed = SIZE - KEPT + SPACE_MARGIN_BYTES  # The runtime is ready: nothing to unpack.

    harness.free = needed - 1
    with pytest.raises(NotEnoughSpaceError) as raised:
        await setup.setup()
    assert raised.value.needed == needed

    harness.free = needed  # Exactly enough is enough.
    assert (await setup.setup()).state is SetupState.READY
    assert harness.server.ranges[MODEL_URL] == [None, RESUME]


# Unsafe archives.

UNSAFE_ARCHIVES = [
    pytest.param(make_zip([*ZIP_ENTRIES, ("../evil.txt", b"evil")]), ZIP_URL, id="zip-parent"),
    pytest.param(make_zip([*ZIP_ENTRIES, ("..\\evil.txt", b"evil")]), ZIP_URL, id="zip-backslash"),
    pytest.param(make_zip([*ZIP_ENTRIES, ("/abs.txt", b"evil")]), ZIP_URL, id="zip-absolute"),
    pytest.param(make_zip([*ZIP_ENTRIES, ("C:/x.txt", b"evil")]), ZIP_URL, id="zip-drive"),
    pytest.param(
        make_zip([*ZIP_ENTRIES, (zip_link("ggml-link.dll"), b"../../outside.dll")]),
        ZIP_URL,
        id="zip-symlink",
    ),
    pytest.param(
        make_zip(entry for entry in ZIP_ENTRIES if entry[0] != "llama-server.exe"),
        ZIP_URL,
        id="zip-no-server",
    ),
    pytest.param(
        make_tar([*TAR_ENTRIES, tar_file("llama-test/../../evil.txt", b"evil")]),
        TAR_URL,
        id="tar-parent",
    ),
    pytest.param(make_tar([*TAR_ENTRIES, tar_file("/abs.txt", b"evil")]), TAR_URL, id="tar-abs"),
    pytest.param(
        make_tar(
            [*TAR_ENTRIES, tar_special("llama-test/out.dylib", tarfile.SYMTYPE, "../../x.dylib")]
        ),
        TAR_URL,
        id="tar-link-outside",
    ),
    pytest.param(
        make_tar([*TAR_ENTRIES, tar_special("llama-test/z.dylib", tarfile.SYMTYPE, "/lib/z")]),
        TAR_URL,
        id="tar-absolute-link",
    ),
    pytest.param(
        make_tar([*TAR_ENTRIES, tar_special("llama-test/null", tarfile.CHRTYPE)]),
        TAR_URL,
        id="tar-device",
    ),
    pytest.param(
        make_tar(entry for entry in TAR_ENTRIES if entry[0].name != "llama-test/llama-server"),
        TAR_URL,
        id="tar-no-server",
    ),
]


@pytest.mark.parametrize(("archive", "url"), UNSAFE_ARCHIVES)
async def test_an_unsafe_archive_unpacks_nothing(
    harness: Harness, tmp_path: Path, archive: bytes, url: str
) -> None:
    setup = harness.local(harness.pin(archive, url=url))

    with pytest.raises(LocalSetupError) as raised:
        await setup.setup()

    assert str(raised.value) == UNSAFE
    assert not (harness.folder / "runtime").exists()
    assert not (harness.folder / "downloads" / "runtime.partial").exists()
    left = {path.name for path in sorted(tmp_path.rglob("*")) if path.is_file()}  # noqa: ASYNC240
    assert left <= {RECORD, f"{url.rsplit('/', 1)[1]}.part"}  # At most the checked download.
    assert "runtime" not in harness.record()
    assert MODEL_URL not in harness.server.requests
    assert (await setup.status()).state is SetupState.NOT_SET_UP


# Removing, and the lock.


async def test_remove_deletes_everything_and_status_starts_over(harness: Harness) -> None:
    setup = await set_up(harness)
    harness.leave_part(b"unfinished", name="other.bin")

    await setup.remove()

    assert list(harness.folder.iterdir()) == []
    assert await setup.status() == SetupStatus(SetupState.NOT_SET_UP, TOTAL, 0)
    await setup.remove()  # Nothing left to remove is fine.
    assert list(harness.folder.iterdir()) == []


async def test_setup_and_remove_are_refused_while_another_holds_the_lock(
    harness: Harness,
) -> None:
    setup = harness.local(harness.pin())

    with held_lock(harness.folder / LOCK, "held by the test"):
        with pytest.raises(DataInUseError) as raised:
            await setup.setup()
        assert str(raised.value) == BUSY
        with pytest.raises(DataInUseError, match=re.escape(BUSY)):
            await setup.remove()

    assert not harness.server.requests
    assert (await setup.setup()).state is SetupState.READY  # Free once released.


# What the owner reads, and the real client.


@pytest.mark.parametrize(
    ("manifest", "archive"), [(WINDOWS_X64, "37.5 MB"), (MACOS_ARM64, "16.5 MB")]
)
def test_setup_lines_name_both_files_their_sizes_licenses_and_hosts(
    manifest: Manifest, archive: str
) -> None:
    intro, runtime, model, closing = setup_lines(manifest)

    assert "2 files (2.5 GB)" in intro
    assert f"llama.cpp {manifest.runtime.version}" in runtime
    assert f"({archive}, MIT license)" in runtime
    assert f"from {httpx.URL(manifest.runtime.archive.url).host}" in runtime
    assert manifest.model.name in model
    assert "(2.5 GB, Apache-2.0 license;" in model
    assert manifest.model.file.source in model
    assert f"from {httpx.URL(manifest.model.file.url).host}" in model
    assert "SHA-256" in closing


async def test_the_download_client_refuses_plain_http_and_unlisted_hosts() -> None:
    async with download_client(frozenset({"downloads.test"})) as client:
        guard = client._transport
        assert isinstance(guard, AllowedHostsGuard)
        assert client.follow_redirects
        assert not client.trust_env
        assert client.timeout == TIMEOUT
        for url in ("http://downloads.test/runtime.zip", "https://evil.test/runtime.zip"):
            with pytest.raises(UnlistedHostError):
                await client.get(url)
        assert not guard.sent


def test_a_move_refused_for_a_moment_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = tmp_path / "staged", tmp_path / "placed"
    source.mkdir()
    real = os.replace
    refusals = iter([PermissionError("scanning"), PermissionError("scanning")])

    def busy_then_free(old: Path, new: Path) -> None:
        refusal = next(refusals, None)
        if refusal is not None:
            raise refusal
        real(old, new)

    monkeypatch.setattr(os, "replace", busy_then_free)
    local_setup._replace(source, target, pause=0)
    assert target.is_dir() and not source.exists()
    source.mkdir()

    def always_held(old: Path, new: Path) -> None:
        raise PermissionError("held")

    monkeypatch.setattr(os, "replace", always_held)
    with pytest.raises(PermissionError):
        local_setup._replace(source, tmp_path / "other", tries=3, pause=0)
