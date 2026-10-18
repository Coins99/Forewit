"""The public package name must agree with native metadata and manifest targets."""

import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import build_desktop, windows_metadata


@pytest.mark.parametrize(
    ("platform", "options", "layout"),
    [
        ("win32", [], ""),
        ("win32", ["--onefile"], "onefile"),
        ("win32", ["--onefile", "--console"], "onefile-console"),
        ("darwin", [], ""),
    ],
)
def test_native_build_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    options: list[str],
    layout: str,
) -> None:
    monkeypatch.setattr(build_desktop, "__file__", str(tmp_path / "scripts/build_desktop.py"))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "argv", ["build_desktop.py", *options])
    monkeypatch.setattr(build_desktop, "project_version", lambda root: "0.1.0")
    monkeypatch.setattr(build_desktop, "stage_google_client", lambda *args: None)
    monkeypatch.setattr(
        windows_metadata,
        "write_resources",
        lambda root, directory: (directory / "MailBrief.ico", directory / "windows-version.txt"),
    )
    calls: list[list[str]] = []
    manifests: list[Path] = []

    def build(command: list[str], *, cwd: Path) -> int:
        calls.append(command)
        assert cwd == tmp_path
        if platform == "darwin":
            plist = tmp_path / "out/dist/Forewit.app/Contents/Info.plist"
            plist.parent.mkdir(parents=True)
            with plist.open("wb") as stream:
                plistlib.dump({"CFBundleIdentifier": "com.mailbrief.desktop"}, stream)
        return 0

    def sign(command: list[str], **kwargs: Any) -> None:
        calls.append(command)

    monkeypatch.setattr(subprocess, "call", build)
    monkeypatch.setattr(subprocess, "run", sign)
    monkeypatch.setattr(
        build_desktop, "write_manifest", lambda root, executable: manifests.append(executable)
    )
    assert build_desktop.main() == 0
    command = calls[0]
    assert command[command.index("--name") + 1] == "Forewit"
    assert Path(command[command.index("--distpath") + 1]) == tmp_path / "out/dist" / layout
    if "--onefile" in options:
        assert manifests == [tmp_path / "out/dist" / layout / "Forewit.exe"]
    if platform == "darwin":
        with (tmp_path / "out/dist/Forewit.app/Contents/Info.plist").open("rb") as stream:
            metadata = plistlib.load(stream)
        assert metadata["CFBundleName"] == metadata["CFBundleDisplayName"] == "Forewit"
        assert metadata["CFBundleIdentifier"] == "com.mailbrief.desktop"
        assert calls[-1][-1] == str(tmp_path / "out/dist/Forewit.app")
