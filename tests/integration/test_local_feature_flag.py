"""Flag-off entrypoints stop before downloads, mailbox access or inference."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest

from mailbrief.diagnostics import gmail
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.errors import ConfigurationError
from mailbrief.ui import runtime
from mailbrief.ui.runtime import DesktopRuntime


def test_cli_setup_is_blocked_before_setup_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup = Mock(side_effect=AssertionError("Disabled feature constructed setup"))
    monkeypatch.setattr(gmail, "LocalSetup", setup)
    assert gmail.main(["local-ai", "setup", "--folder", str(tmp_path), "--yes"]) != 0
    output = capsys.readouterr()
    assert "experimental and disabled" in output.out + output.err
    setup.assert_not_called()


@pytest.mark.parametrize("automatic", [False, True])
async def test_desktop_disabled_local_stops_before_mailbox_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    mailbox = Mock(side_effect=AssertionError("Disabled feature accessed mailbox"))
    monkeypatch.setattr(runtime, "gmail_provider", mailbox)
    backend = DesktopRuntime(tmp_path / "local.sqlite3")
    try:
        await backend.load_saved()
        await backend.save_owner_preferences(PreferencesEdit(analysis_mode="local"), 0)
        with pytest.raises(ConfigurationError, match="experimental and disabled"):
            if automatic:
                await backend.generate_automatic(asyncio.Event(), lambda update: None)
            else:
                await backend.generate(Mock(), Mock(), asyncio.Event(), lambda update: None)
        mailbox.assert_not_called()
        assert (await backend.get_owner_preferences()).analysis_mode == "local"
    finally:
        await backend.close()
