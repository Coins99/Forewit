"""Set up local email analysis: download the llama.cpp server and the model the manifest
pins, check them, and keep them ready (P0-B2 Part 2).

Setup is the only part of local AI that uses the internet. It runs only when the owner asks,
sends no mail or account data, and reaches only the manifest's hosts over HTTPS, redirects
included. A download resumes where it stopped, and a file is used only once its size and
SHA-256 match the manifest. A ready file is checked by its size and when it last changed, and
hashed again only when that changed. Nothing here logs a path.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tarfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final

import httpx

from mailbrief.infra.data_lock import held_lock
from mailbrief.providers.local.manifest import Manifest, PinnedFile, Runtime
from mailbrief.providers.local.transport import AllowedHostsGuard, UnlistedHostError

logger = logging.getLogger(__name__)

RECORD: Final = "setup.json"
LOCK: Final = "setup.lock"
_FORMAT: Final = 1
CHUNK_BYTES: Final = 1 << 20
# Room setup leaves for the database and everything else on the disk.
SPACE_MARGIN_BYTES: Final = 512 * 1024**2
RETRY_DELAYS: Final = (2.0, 5.0, 15.0)
TIMEOUT: Final = httpx.Timeout(60.0, connect=15.0)
_RETRY_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
_CONTENT_RANGE: Final = re.compile(r"bytes (\d+)-(\d+)/(\d+)")

BUSY: Final = "Local AI setup is already running in another Forewit window or command."
UNSUPPORTED: Final = (
    "Local analysis isn't available on this computer: it needs Windows on an x64 processor "
    "or a Mac with Apple silicon."
)
UNREACHABLE: Final = (
    "Couldn't reach the download server. Check the connection and try again; setup "
    "continues where it stopped."
)
UNLISTED: Final = (
    "A download was sent on to a server setup doesn't trust, so setup stopped; nothing from "
    "that server was kept."
)
LOOPING: Final = "The download server kept redirecting, so setup stopped. Try again later."
MISMATCH: Final = "A downloaded file didn't match its pinned checksum and was deleted. Try again."
UNSAFE: Final = "The runtime archive isn't laid out as expected, so nothing was unpacked."


class LocalSetupError(RuntimeError):
    """Setup couldn't finish; the message is static and names no path."""


class SetupUnsupportedError(LocalSetupError):
    """No manifest covers this computer."""


class NotEnoughSpaceError(LocalSetupError):
    """The disk lacks the room setup needs."""

    def __init__(self, needed: int, free: int) -> None:
        super().__init__(
            f"Local AI setup needs {gigabytes(needed)} of free disk space; "
            f"{gigabytes(free)} is free."
        )
        self.needed = needed
        self.free = free


def gigabytes(size: int) -> str:
    return f"{size / 1e9:.1f} GB"


def _size(size: int) -> str:
    return gigabytes(size) if size >= 1_000_000_000 else f"{size / 1e6:.1f} MB"


def setup_lines(manifest: Manifest) -> tuple[str, ...]:
    """What setup downloads, in plain sentences shared by the CLI and the desktop."""
    runtime = manifest.runtime
    archive, model = runtime.archive, manifest.model.file
    return (
        f"Forewit will download 2 files ({gigabytes(archive.size + model.size)}) so that email "
        "can be analyzed on this computer:",
        f"- llama.cpp {runtime.version}, the program that runs the model "
        f"({_size(archive.size)}, {archive.license} license), from github.com",
        f"- {manifest.model.name}, the model ({_size(model.size)}, {model.license} license; "
        f"{model.source}), from huggingface.co",
        "Each file is used only once it matches its pinned SHA-256 checksum. Nothing about "
        "your mail or accounts is sent, and an interrupted download continues where it "
        "stopped.",
    )


class SetupState(StrEnum):
    UNSUPPORTED = "unsupported"
    NOT_SET_UP = "not_set_up"  # Nothing yet, or a download that stopped part way.
    READY = "ready"
    DAMAGED = "damaged"  # A set-up file changed or went missing: set up again.


@dataclass(frozen=True, slots=True)
class SetupStatus:
    state: SetupState
    total_bytes: int = 0  # Everything setup downloads.
    downloaded_bytes: int = 0  # How much of that is on disk now.
    server: Path | None = None  # llama-server, when ready.
    model: Path | None = None  # The model file, when ready.


class SetupStage(StrEnum):
    RUNTIME = "runtime"
    MODEL = "model"
    UNPACKING = "unpacking"


@dataclass(frozen=True, slots=True)
class SetupProgress:
    stage: SetupStage
    done_bytes: int
    total_bytes: int


class _Interrupted(Exception):
    """A download stopped in a way that trying again may fix."""


def download_client(hosts: frozenset[str]) -> httpx.AsyncClient:
    """The client for setup's downloads: HTTPS to ``hosts`` only, on every redirect too, and
    no proxy or certificate settings from the environment."""
    return httpx.AsyncClient(
        transport=AllowedHostsGuard(hosts),
        follow_redirects=True,
        trust_env=False,
        timeout=TIMEOUT,
    )


def _free_space(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def _hash(path: Path) -> "hashlib._Hash":
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256")


def _replace(source: Path, target: Path, *, tries: int = 20, pause: float = 0.25) -> None:
    """Move ``source`` to ``target`` in one step. On Windows an antivirus scan of files just
    unpacked can hold the folder for a moment, so a refused move is tried again for about
    five seconds."""
    for _ in range(tries - 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(pause)
    os.replace(source, target)


def _resume_point(part: Path, pinned: PinnedFile) -> tuple[int, "hashlib._Hash"]:
    """How much of the download is already there, and the hash of it so far. A part that is
    too long, or complete but wrong, is deleted so the download starts over."""
    try:
        have = part.stat().st_size
    except FileNotFoundError:
        return 0, hashlib.sha256()
    digest = _hash(part)
    if have < pinned.size or (have == pinned.size and digest.hexdigest() == pinned.sha256):
        return have, digest
    part.unlink()
    return 0, hashlib.sha256()


def _member(name: str) -> PurePosixPath:
    """An archive member's path, refused when it could land outside the target folder."""
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        path.is_absolute()
        or ":" in name
        or not path.parts
        or any(part in ("", "..") for part in path.parts)
    ):
        raise LocalSetupError(UNSAFE)
    return path


def _unzip(archive: Path, staging: Path) -> None:
    """The Windows build: regular files only, every path checked before anything is written."""
    with zipfile.ZipFile(archive) as unzipped:
        members = [(info, _member(info.filename)) for info in unzipped.infolist()]
        if any(stat.S_ISLNK(info.external_attr >> 16) for info, _ in members):
            raise LocalSetupError(UNSAFE)
        for info, relative in members:
            if info.is_dir():
                continue
            target = staging.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with unzipped.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK_BYTES)
            if os.name != "nt":
                target.chmod(stat.S_IMODE(info.external_attr >> 16) or 0o644)


def _untar(archive: Path, staging: Path) -> None:
    """The macOS build: tarfile's "data" filter refuses absolute paths, links that leave the
    folder and device files, keeps executable bits and ignores the archive's owners; its
    libraries' version symlinks stay, pointing inside the folder."""
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as untarred:
            for member in untarred.getmembers():
                _member(member.name)
            untarred.extractall(staging, filter="data")
    except tarfile.FilterError:
        raise LocalSetupError(UNSAFE) from None


class LocalSetup:
    """The runtime and model in ``folder``, as ``manifest`` pins them (None: unsupported).

    Setup and removal hold a lock in the folder, so the desktop and a command can't both
    change it. Files: ``runtime/<version>/``, ``model/<file>``, ``downloads/`` for
    downloads in progress, and ``setup.json``, the record of every checked file.
    """

    def __init__(
        self,
        folder: Path,
        manifest: Manifest | None,
        *,
        client: Callable[[frozenset[str]], httpx.AsyncClient] = download_client,
        free_space: Callable[[Path], int] = _free_space,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._folder = folder
        self._manifest = manifest
        self._client = client
        self._free_space = free_space
        self._sleep = sleep

    @property
    def manifest(self) -> Manifest | None:
        return self._manifest

    async def status(self) -> SetupStatus:
        """What is on disk now; never downloads. Hashes only files that changed."""
        if self._manifest is None:
            return SetupStatus(SetupState.UNSUPPORTED)
        return await asyncio.to_thread(self._status, self._manifest)

    async def setup(self, progress: Callable[[SetupProgress], None] | None = None) -> SetupStatus:
        """Download, check and unpack whatever isn't ready yet, then return the status.

        Raises LocalSetupError (static messages) when this computer is unsupported, the disk
        is too full, a server can't be reached after retries, refuses a file or redirects
        to a host the manifest doesn't list, or a file doesn't match its checksum, and
        DataInUseError when another setup is running. A cancelled or failed download keeps
        what it got, and the next setup continues from there.
        """
        manifest = self._manifest
        if manifest is None:
            raise SetupUnsupportedError(UNSUPPORTED)
        with held_lock(self._folder / LOCK, BUSY):
            needed = await asyncio.to_thread(self._prepare, manifest)
            if needed:
                total = sum(pinned.size for _, pinned in needed)
                done = 0
                async with self._client(manifest.hosts) as client:
                    for stage, pinned in needed:
                        path = await self._download(client, stage, pinned, done, total, progress)
                        done += pinned.size
                        if stage is SetupStage.RUNTIME:
                            _report(progress, SetupProgress(SetupStage.UNPACKING, done, total))
                            await asyncio.to_thread(self._unpack, path, manifest.runtime)
                        else:
                            await asyncio.to_thread(self._install_model, path, manifest)
                logger.info("Local AI setup downloaded %d files", len(needed))
        return await self.status()

    async def remove(self) -> None:
        """Delete the runtime, the model, unfinished downloads and the record."""
        with held_lock(self._folder / LOCK, BUSY):
            await asyncio.to_thread(self._remove)
        logger.info("Local AI files removed")

    # Paths.

    def _runtime_dir(self, runtime: Runtime) -> Path:
        return self._folder / "runtime" / runtime.version

    def _model_path(self, manifest: Manifest) -> Path:
        return self._folder / "model" / manifest.model.file.name

    def _part(self, pinned: PinnedFile) -> Path:
        return self._folder / "downloads" / f"{pinned.name}.part"

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._folder).as_posix()

    # The record of checked files: {"runtime"|"model": {"sha256": ..., "files": {path: [size,
    # mtime_ns, sha256]}}}, the paths relative to the folder.

    def _read_record(self) -> dict[str, Any]:
        try:
            data = json.loads((self._folder / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) and data.get("format") == _FORMAT else {}

    def _write_record(self, record: dict[str, Any]) -> None:
        record["format"] = _FORMAT
        staged = self._folder / f"{RECORD}.tmp"
        staged.write_text(json.dumps(record, indent=1), encoding="utf-8")
        os.replace(staged, self._folder / RECORD)

    def _components(self, manifest: Manifest) -> tuple[tuple[SetupStage, PinnedFile, str], ...]:
        """Each part setup installs, its download and the file that must be in its record."""
        runtime = manifest.runtime
        server = self._runtime_dir(runtime) / runtime.executable
        return (
            (SetupStage.RUNTIME, runtime.archive, self._relative(server)),
            (SetupStage.MODEL, manifest.model.file, self._relative(self._model_path(manifest))),
        )

    def _check(
        self, record: dict[str, Any], stage: SetupStage, pinned: PinnedFile, key: str
    ) -> bool | None:
        """True when the part's recorded files are all as recorded; False when one isn't;
        None when the record has nothing for this download. Updates the times of files that
        were touched but hash the same."""
        entry = record.get(stage.value)
        if not isinstance(entry, dict) or entry.get("sha256") != pinned.sha256:
            return None
        files = entry.get("files")
        if not isinstance(files, dict) or key not in files:
            return False
        for relative, saved in files.items():
            if not (
                isinstance(relative, str)
                and isinstance(saved, list)
                and len(saved) == 3
                and all(type(value) is int for value in saved[:2])
                and isinstance(saved[2], str)
            ):
                return False
            try:
                path = self._folder.joinpath(*_member(relative).parts)
                found = path.stat()
            except (LocalSetupError, OSError):
                return False
            if found.st_size != saved[0]:
                return False
            if found.st_mtime_ns != saved[1]:
                if _hash(path).hexdigest() != saved[2]:
                    return False
                saved[1] = found.st_mtime_ns
        return True

    def _part_size(self, pinned: PinnedFile) -> int:
        try:
            return min(self._part(pinned).stat().st_size, pinned.size)
        except OSError:
            return 0

    def _status(self, manifest: Manifest) -> SetupStatus:
        record = self._read_record()
        before = json.dumps(record, sort_keys=True)
        total = done = ready = 0
        damaged = False
        for stage, pinned, key in self._components(manifest):
            total += pinned.size
            checked = self._check(record, stage, pinned, key)
            if checked:
                done += pinned.size
                ready += 1
            else:
                damaged = damaged or checked is False
                done += self._part_size(pinned)
        if record and json.dumps(record, sort_keys=True) != before:
            self._write_record(record)  # Only touched files' times.
        if damaged:
            return SetupStatus(SetupState.DAMAGED, total, done)
        if ready < 2:
            return SetupStatus(SetupState.NOT_SET_UP, total, done)
        runtime = manifest.runtime
        return SetupStatus(
            SetupState.READY,
            total,
            done,
            server=self._runtime_dir(runtime) / runtime.executable,
            model=self._model_path(manifest),
        )

    def _prepare(self, manifest: Manifest) -> list[tuple[SetupStage, PinnedFile]]:
        """The downloads still needed, after dropping damaged parts and keeping a model file
        that is already in place and checks out; raises when the disk is too full."""
        self._folder.mkdir(parents=True, exist_ok=True)
        record = self._read_record()
        needed: list[tuple[SetupStage, PinnedFile]] = []
        for stage, pinned, key in self._components(manifest):
            if self._check(record, stage, pinned, key):
                continue
            record.pop(stage.value, None)
            if stage is SetupStage.MODEL and self._adopt_model(record, manifest):
                continue
            needed.append((stage, pinned))
        self._write_record(record)
        space = sum(pinned.size - self._part_size(pinned) for _, pinned in needed)
        if any(stage is SetupStage.RUNTIME for stage, _ in needed):
            space += manifest.runtime.unpacked_size
        if space:
            free = self._free_space(self._folder)
            if free < space + SPACE_MARGIN_BYTES:
                raise NotEnoughSpaceError(space + SPACE_MARGIN_BYTES, free)
        return needed

    def _adopt_model(self, record: dict[str, Any], manifest: Manifest) -> bool:
        """Keep a model file already in place, as an unreadable record would leave it, when
        its size and SHA-256 match, rather than download 2.5 GB again."""
        path = self._model_path(manifest)
        pinned = manifest.model.file
        try:
            if path.stat().st_size != pinned.size or _hash(path).hexdigest() != pinned.sha256:
                return False
        except OSError:
            return False
        self._record_files(record, SetupStage.MODEL, pinned, {path: pinned.sha256})
        return True

    def _record_files(
        self,
        record: dict[str, Any],
        stage: SetupStage,
        pinned: PinnedFile,
        digests: dict[Path, str],
    ) -> None:
        files = {}
        for path, digest in digests.items():
            found = path.stat()
            files[self._relative(path)] = [found.st_size, found.st_mtime_ns, digest]
        record[stage.value] = {"sha256": pinned.sha256, "files": files}

    # Downloading.

    async def _download(
        self,
        client: httpx.AsyncClient,
        stage: SetupStage,
        pinned: PinnedFile,
        done: int,
        total: int,
        progress: Callable[[SetupProgress], None] | None,
    ) -> Path:
        """The download, checked, retried after interruptions, each try resuming."""
        part = self._part(pinned)
        part.parent.mkdir(parents=True, exist_ok=True)

        def report(have: int) -> None:
            _report(progress, SetupProgress(stage, done + have, total))

        for delay in (*RETRY_DELAYS, None):
            try:
                return await self._fetch(client, pinned, part, report)
            except _Interrupted:
                if delay is None:
                    raise LocalSetupError(UNREACHABLE) from None
                logger.info("Local AI download interrupted; retrying in %.0f s", delay)
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        pinned: PinnedFile,
        part: Path,
        report: Callable[[int], None],
    ) -> Path:
        have, digest = await asyncio.to_thread(_resume_point, part, pinned)
        if have == pinned.size:
            report(have)  # Complete and checked, from an earlier run.
            return part
        headers = {"Accept-Encoding": "identity"}
        if have:  # GitHub's download host refuses some range forms; this one it takes.
            headers["Range"] = f"bytes={have}-{pinned.size - 1}"
        try:
            async with client.stream("GET", pinned.url, headers=headers) as response:
                mode = self._resume_mode(response, have, pinned, part)
                if mode == "wb":
                    have = 0
                    digest = hashlib.sha256()
                with part.open(mode) as out:
                    async for chunk in response.aiter_raw(CHUNK_BYTES):
                        have += len(chunk)
                        if have > pinned.size:
                            break
                        out.write(chunk)
                        digest.update(chunk)
                        report(have)
        except httpx.TransportError:
            raise _Interrupted from None
        except httpx.TooManyRedirects:
            logger.info("Local AI download redirected too many times")
            raise LocalSetupError(LOOPING) from None
        except UnlistedHostError:
            logger.info("Local AI download redirected to an unlisted host")
            raise LocalSetupError(UNLISTED) from None
        if have < pinned.size:
            raise _Interrupted  # The connection ended early; resume.
        return self._verified(part, pinned, digest)

    def _resume_mode(
        self, response: httpx.Response, have: int, pinned: PinnedFile, part: Path
    ) -> str:
        """ "ab" to append to what is there, "wb" to start over; raises for anything else."""
        status = response.status_code
        if status in _RETRY_STATUSES:
            logger.info("Local AI download answered HTTP %d", status)
            raise _Interrupted
        if status == 416 and have:  # The server won't resume this part: start over.
            part.unlink(missing_ok=True)
            raise _Interrupted
        if status == 206 and have:
            match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if match and int(match[1]) == have and int(match[3]) == pinned.size:
                return "ab"
            raise self._mismatch(part)
        if status == 200:
            length = response.headers.get("Content-Length")
            if length is not None and length.isdigit() and int(length) != pinned.size:
                raise self._mismatch(part)
            return "wb"
        logger.info("Local AI download refused: HTTP %d", status)
        raise LocalSetupError(f"The download server answered HTTP {status}. Try again later.")

    def _mismatch(self, part: Path) -> LocalSetupError:
        part.unlink(missing_ok=True)
        logger.info("Local AI download didn't match its pin")
        return LocalSetupError(MISMATCH)

    def _verified(self, part: Path, pinned: PinnedFile, digest: "hashlib._Hash") -> Path:
        if digest.hexdigest() != pinned.sha256:
            raise self._mismatch(part)
        return part

    # Installing.

    def _unpack(self, archive: Path, runtime: Runtime) -> None:
        """Unpack the checked archive beside the folder's other downloads, then move it into
        place in one step, replacing any other version; only then is it recorded."""
        staging = self._folder / "downloads" / "runtime.partial"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            if runtime.archive.name.endswith(".tar.gz"):
                _untar(archive, staging)
            else:
                _unzip(archive, staging)
            server = staging.joinpath(*runtime.executable.split("/"))
            if not server.is_file() or server.is_symlink():
                raise LocalSetupError(UNSAFE)
            if os.name != "nt":
                server.chmod(0o755)
            digests = {
                path: _hash(path).hexdigest()
                for path in sorted(staging.rglob("*"))
                if path.is_file() and not path.is_symlink()
            }
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        root = self._folder / "runtime"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir()
        directory = self._runtime_dir(runtime)
        _replace(staging, directory)
        record = self._read_record()
        self._record_files(
            record,
            SetupStage.RUNTIME,
            runtime.archive,
            {directory / path.relative_to(staging): digest for path, digest in digests.items()},
        )
        self._write_record(record)
        archive.unlink()

    def _install_model(self, part: Path, manifest: Manifest) -> None:
        path = self._model_path(manifest)
        shutil.rmtree(path.parent, ignore_errors=True)
        path.parent.mkdir()
        os.replace(part, path)
        record = self._read_record()
        self._record_files(
            record, SetupStage.MODEL, manifest.model.file, {path: manifest.model.file.sha256}
        )
        self._write_record(record)

    def _remove(self) -> None:
        for name in ("runtime", "model", "downloads"):
            path = self._folder / name
            if path.exists():
                shutil.rmtree(path)
        (self._folder / RECORD).unlink(missing_ok=True)


def _report(progress: Callable[[SetupProgress], None] | None, update: SetupProgress) -> None:
    """Report progress; a failing callback is logged by type and never stops setup."""
    if progress is None:
        return
    try:
        progress(update)
    except Exception as exc:
        logger.warning("Setup progress callback raised %s", type(exc).__name__)
