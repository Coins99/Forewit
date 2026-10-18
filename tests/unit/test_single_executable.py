"""Single-file audit must inspect embedded members and reject incomplete packages."""

import json
import struct
import subprocess
from pathlib import Path

import pytest
from PyInstaller.archive.writers import CArchiveWriter  # type: ignore[import-untyped]

from scripts.package_manifest import (
    archive_inventory,
    audit_names,
    embedded_notices,
    project_version,
    require_resources,
    require_x64,
    sha256,
    verify_manifest,
)
from scripts.windows_metadata import runtime_packages


@pytest.mark.parametrize(
    "name",
    [
        "cache/owner.sqlite3",
        "cache/owner.sqlite3-wal",
        "cache/owner.sqlite3-shm",
        "cache/owner.sqlite-journal",
        "cache/owner.db-wal",
        "settings/.env.production",
        "cache/msal-token-cache.bin",
        "settings/desktop-settings.json",
        "credentials.json",
        "a/client_secret_personal.json",
        "_pytest/assertion.pyc",
        "mypy/api.pyc",
        "../escape",
    ],
)
def test_forbidden_embedded_names(name: str) -> None:
    with pytest.raises(ValueError, match="private or development"):
        audit_names([name])


def test_embedded_private_file_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.txt"
    source.write_text("Synthetic private marker.", encoding="utf-8")
    archive = tmp_path / "test.pkg"
    CArchiveWriter(
        str(archive), [("nested/credentials.json", str(source), True, "x")], "python.dll"
    )
    with pytest.raises(ValueError, match="private or development"):
        archive_inventory(archive)


def test_archive_inventory_does_not_execute_members(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.txt"
    source.write_text("raise RuntimeError('never execute')", encoding="utf-8")
    archive = tmp_path / "test.pkg"
    CArchiveWriter(str(archive), [("safe.txt", str(source), True, "x")], "python.dll")
    assert archive_inventory(archive)[0]["name"] == "safe.txt"


def test_corrupt_archive_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "bad.exe"
    archive.write_bytes(b"Not an archive")
    with pytest.raises(ValueError, match="Unable to inspect"):
        archive_inventory(archive)


def resource_names() -> set[str]:
    root = Path(__file__).resolve().parents[2]
    names = {
        str(path.relative_to(root / "src")).replace("\\", "/")
        for path in (root / "src/mailbrief/ui/theme").rglob("*")
        if path.is_file()
    }
    return names | {
        "migrations/env.py",
        "migrations/versions/example.py",
        "certifi/cacert.pem",
        "mailbrief/resources/MailBrief.ico",
        "mailbrief/resources/THIRD_PARTY_NOTICES.txt",
        "PySide6/plugins/platforms/qwindows.dll",
        "tzdata/zoneinfo/America/Toronto",
    }


@pytest.mark.parametrize(
    "missing",
    [
        "migrations/versions/example.py",
        "PySide6/plugins/platforms/qwindows.dll",
        "certifi/cacert.pem",
        "mailbrief/ui/theme/fonts/Inter-Regular.ttf",
        "mailbrief/ui/theme/fonts/Inter-SemiBold.ttf",
        "mailbrief/ui/theme/icons/refresh.svg",
        "mailbrief/ui/theme/icons/calendar-week.svg",
        "mailbrief/ui/theme/icons/sparkles.svg",
        "mailbrief/ui/theme/icons/plus.svg",
        "mailbrief/ui/theme/icons/bookmark.svg",
        "mailbrief/ui/theme/icons/check.svg",
        "mailbrief/ui/theme/icons/trash.svg",
        "tzdata/zoneinfo/America/Toronto",
        "mailbrief/resources/THIRD_PARTY_NOTICES.txt",
    ],
)
def test_required_resources(missing: str) -> None:
    names = resource_names()
    require_resources(names, ["example.py"])
    with pytest.raises(ValueError, match="missing"):
        require_resources(names - {missing}, ["example.py"])


@pytest.mark.parametrize("machine", [b"\x4c\x01", b"\x64\xaa"])
def test_non_x64_pe_refused(tmp_path: Path, machine: bytes) -> None:
    executable = tmp_path / "test.exe"
    executable.write_bytes(b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0" + machine)
    with pytest.raises(ValueError, match="x64"):
        require_x64(executable)


def test_x64_pe_accepted(tmp_path: Path) -> None:
    executable = tmp_path / "test.exe"
    executable.write_bytes(b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0\x64\x86")
    require_x64(executable)


def test_release_version_must_match_runtime(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="9.9.9"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="same stable"):
        project_version(tmp_path)


@pytest.mark.parametrize("changed", ["commit", "executable", "lockfile"])
def test_manifest_verification_cannot_relabel_a_build(tmp_path: Path, changed: str) -> None:
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "Forewit.exe"
    executable.write_bytes(b"Synthetic original build")
    manifest = {
        "format": 1,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "version": project_version(root),
        "architecture": "x64",
        "dirty": True,
        "lockfile_sha256": sha256(root / "uv.lock"),
        "executable_sha256": sha256(executable),
    }
    path = executable.with_suffix(".manifest.json")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = path.read_bytes()
    assert verify_manifest(root, executable) == manifest
    if changed == "executable":
        executable.write_bytes(b"Changed build")
    else:
        manifest["commit" if changed == "commit" else "lockfile_sha256"] = "stale"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        original = path.read_bytes()
    with pytest.raises(ValueError, match="does not match"):
        verify_manifest(root, executable)
    assert path.read_bytes() == original


def test_release_notices_come_from_executable(tmp_path: Path) -> None:
    notices = tmp_path / "notices.txt"
    notices.write_bytes(b"Notices belonging to this build")
    archive = tmp_path / "build.pkg"
    CArchiveWriter(
        str(archive),
        [("mailbrief/resources/THIRD_PARTY_NOTICES.txt", str(notices), True, "x")],
        "python.dll",
    )
    notices.write_bytes(b"Overwritten by a later build")
    assert embedded_notices(archive) == b"Notices belonging to this build"


def test_runtime_notice_dependencies_include_transitives_and_selected_extras() -> None:
    packages = set(runtime_packages())
    assert {"cryptography", "pyjwt", "pydantic-settings", "portalocker", "certifi"} <= packages
    assert not packages & {"pytest", "mypy", "ruff", "pyinstaller-hooks-contrib"}
