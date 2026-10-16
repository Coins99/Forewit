"""Inspect generated packages without running their contents or reading owner data."""

import hashlib
import importlib.metadata
import io
import json
import platform
import re
import struct
import subprocess
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

NOTICES_MEMBER = "mailbrief/resources/THIRD_PARTY_NOTICES.txt"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def project_version(root: Path) -> str:
    from mailbrief import __version__

    version: str = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if version != __version__ or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version
    ):
        raise ValueError("Project and runtime must have the same stable release version.")
    if any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("Version components must fit Windows file-version fields.")
    return version


def audit_names(names: Iterable[str]) -> None:
    for raw in names:
        name = raw.replace("\\", "/").lower()
        parts = PurePosixPath(name).parts
        if (
            not parts
            or name.startswith("/")
            or ".." in parts
            or any(
                part in {".env", ".git", ".venv", "desktop-settings.json"}
                or part.startswith(".env.")
                for part in parts
            )
            or any(part.startswith("client_secret") for part in parts)
            or name.endswith(".log")
            or re.search(r"\.(sqlite3?|db)(-(wal|shm|journal))?$", name)
            or parts[-1]
            in {"credentials.json", "token.json", "token-cache.json", "msal-token-cache.bin"}
            or parts[0].split(".")[0] in {"pytest", "_pytest", "mypy"}
        ):
            raise ValueError("Distribution contains private or development artifacts.")


def require_resources(names: Iterable[str], revisions: Iterable[str]) -> None:
    present = {name.replace("\\", "/") for name in names}
    required = {
        "migrations/env.py",
        "mailbrief/ui/theme/fonts/Inter-Regular.ttf",
        "mailbrief/ui/theme/fonts/Inter-Medium.ttf",
        "mailbrief/ui/theme/fonts/Inter-SemiBold.ttf",
        "mailbrief/ui/theme/fonts/OFL.txt",
        "mailbrief/ui/theme/fonts/DM-Sans.ttf",
        "mailbrief/ui/theme/fonts/DM-Sans-OFL.txt",
        "mailbrief/ui/theme/icons/LICENSE",
        "mailbrief/resources/MailBrief.ico",
        "mailbrief/resources/THIRD_PARTY_NOTICES.txt",
        "certifi/cacert.pem",
    } | {f"migrations/versions/{name}" for name in revisions}
    required |= {
        f"mailbrief/ui/theme/icons/{name}.svg"
        for name in (
            "sun",
            "checkbox",
            "hourglass",
            "pencil",
            "calendar",
            "calendar-week",
            "settings",
            "refresh",
            "clock",
            "external-link",
            "mail",
            "database",
            "sparkles",
            "plus",
            "bookmark",
            "check",
            "trash",
        )
    }
    if required - present:
        raise ValueError("Package is missing required bundled resources.")
    if not any(name.endswith("/platforms/qwindows.dll") for name in present):
        raise ValueError("Package is missing its native Windows Qt plugin.")
    if not any(name.startswith("tzdata/zoneinfo/") for name in present):
        raise ValueError("Package is missing time zone data.")


def archive_inventory(executable: Path) -> list[dict[str, Any]]:
    # Build-side dependency only. The application never imports PyInstaller.
    from PyInstaller.archive.readers import CArchiveReader  # type: ignore[import-untyped]

    entries: list[dict[str, Any]] = []
    try:
        archive = CArchiveReader(str(executable))
        for name, entry in archive.toc.items():
            entries.append({"name": name, "bytes": entry[2], "type": entry[4]})
            if entry[4] == "z":
                embedded = archive.open_embedded_archive(name)
                entries.extend(
                    {"name": module, "bytes": metadata[2], "type": "python"}
                    for module, metadata in embedded.toc.items()
                )
            elif name == "base_library.zip":
                with zipfile.ZipFile(io.BytesIO(archive.extract(name))) as library:
                    entries.extend(
                        {"name": item.filename, "bytes": item.file_size, "type": "stdlib"}
                        for item in library.infolist()
                    )
    except Exception as exc:
        raise ValueError("Unable to inspect the generated executable archive.") from exc
    audit_names(
        item["name"].replace(".", "/") + ".pyc" if item["type"] == "python" else item["name"]
        for item in entries
    )
    return entries


def require_x64(executable: Path) -> None:
    """Check the PE machine, not a filename or the build host's reported architecture."""
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Expected a Windows executable.")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0" or stream.read(2) != b"\x64\x86":
            raise ValueError("Expected a Windows x64 executable.")


def embedded_notices(executable: Path) -> bytes:
    """Read release notices from the artifact, without executing it."""
    reader = importlib.import_module("PyInstaller.archive.readers").CArchiveReader
    archive = reader(str(executable))
    member = next(name for name in archive.toc if name.replace("\\", "/") == NOTICES_MEMBER)
    return bytes(archive.extract(member))


def verify_manifest(root: Path, executable: Path) -> dict[str, Any]:
    """Validate original build provenance; checking must never create or rewrite it."""
    manifest: dict[str, Any] = json.loads(
        executable.with_suffix(".manifest.json").read_text("utf-8")
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if (
        manifest.get("format") != 1
        or manifest.get("commit") != commit
        or manifest.get("version") != project_version(root)
        or manifest.get("architecture") != "x64"
        or manifest.get("lockfile_sha256") != sha256(root / "uv.lock")
        or manifest.get("executable_sha256") != sha256(executable)
    ):
        raise ValueError("Build manifest does not match the executable and source checkout.")
    return manifest


def write_manifest(root: Path, executable: Path) -> Path:
    require_x64(executable)
    entries = archive_inventory(executable)
    require_resources(
        (item["name"] for item in entries),
        (path.name for path in (root / "migrations/versions").glob("*.py")),
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = {
        "format": 1,
        "version": project_version(root),
        "commit": commit,
        "dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root,
                text=True,
            ).strip()
        ),
        "architecture": "x64",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "pyside6": importlib.metadata.version("pyside6"),
        "lockfile_sha256": sha256(root / "uv.lock"),
        "executable": executable.name,
        "executable_sha256": sha256(executable),
        "executable_bytes": executable.stat().st_size,
        "members": sorted(entries, key=lambda item: item["name"]),
    }
    destination = executable.with_suffix(".manifest.json")
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination
