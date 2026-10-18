"""Public branding must not change persisted identity or installed command behavior."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtWidgets import QApplication

from mailbrief.app import create_application
from mailbrief.paths import configure_qt_identity


def test_display_name_preserves_profile_and_survives_reinitialization(qapp: QApplication) -> None:
    configure_qt_identity()
    location = QStandardPaths.StandardLocation.AppLocalDataLocation
    before = QStandardPaths.writableLocation(location)
    qapp.setApplicationDisplayName("Previous display name")
    assert create_application([]) is qapp
    assert qapp.applicationDisplayName() == "Forewit"
    configure_qt_identity()
    assert create_application([]) is qapp
    assert qapp.applicationDisplayName() == "Forewit"
    assert QCoreApplication.applicationName() == "MailBrief"
    assert QCoreApplication.organizationName() == "MailBrief"
    assert QStandardPaths.writableLocation(location) == before


@pytest.mark.parametrize(
    ("command", "canonical"),
    [
        ("forewit", "forewit"),
        ("mailbrief", "forewit"),
        ("forewit-gmail-diagnostic", "forewit-gmail-diagnostic"),
        ("mailbrief-gmail-diagnostic", "forewit-gmail-diagnostic"),
        ("forewit-ms-diagnostic", "forewit-ms-diagnostic"),
        ("mailbrief-ms-diagnostic", "forewit-ms-diagnostic"),
    ],
)
def test_installed_commands_outside_checkout(tmp_path: Path, command: str, canonical: str) -> None:
    scripts = Path(sys.executable).parent
    executable = scripts / (command + (".exe" if sys.platform == "win32" else ""))
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    for arguments, code in [(["--help"], 0), (["--unknown-rename-test-option"], 2)]:
        result = subprocess.run(
            [str(executable), *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == code, result.stdout + result.stderr
        assert f"usage: {canonical}" in result.stdout + result.stderr
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("module", ["mailbrief", "mailbrief.diagnostics.backup"])
def test_module_help_remains_available(tmp_path: Path, module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout


def test_gmail_aliases_show_the_same_synthetic_preferences(tmp_path: Path) -> None:
    outputs = []
    scripts = Path(sys.executable).parent
    suffix = ".exe" if sys.platform == "win32" else ""
    database = tmp_path / "missing.sqlite3"
    for name in ("forewit-gmail-diagnostic", "mailbrief-gmail-diagnostic"):
        for arguments in (
            ["preferences", "show", "--help"],
            ["preferences", "show", "--database", str(database)],
        ):
            result = subprocess.run(
                [str(scripts / (name + suffix)), *arguments],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            outputs.append(result.stdout)
    assert outputs[0] == outputs[2]
    assert outputs[1] == outputs[3]
    assert "Refresh when Forewit starts:" in outputs[1]
    assert not database.exists()
