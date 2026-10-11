"""Package verification must not overwrite records to make a persistence check pass."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mailbrief.domain.drafts import DraftEdit, DraftKind
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.smoke import SMOKE_ZONE, check_upgrade, verify_persistence


async def seed_profile(directory: Path) -> None:
    runtime = DesktopRuntime(directory / "smoke.sqlite3")
    try:
        await runtime.load_saved()
        await runtime.save_preferences(DesktopPreferences(groq_model="smoke-test-model"))
        await runtime.save_owner_preferences(PreferencesEdit(time_zone=SMOKE_ZONE), 0)
        note = await runtime.create_draft(DraftKind.NOTE)
        note = await runtime.autosave_draft(
            note.public_id,
            note.revision,
            DraftEdit(title="Package note", body="Synthetic owner writing."),
        )
        await runtime.checkpoint_draft(note.public_id, note.revision)
    finally:
        await runtime.close()


async def test_persistence_checks_previous_process_values(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    await seed_profile(tmp_path)
    await verify_persistence(tmp_path)
    settings = tmp_path / "desktop-settings.json"
    original = settings.read_text("utf-8").replace("smoke-test-model", "unexpected-model")
    settings.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="settings were not preserved"):
        await verify_persistence(tmp_path)
    assert settings.read_text("utf-8") == original


async def test_persistence_refuses_empty_profile(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Missing previous"):
        await verify_persistence(tmp_path)
    assert not await asyncio.to_thread(lambda: list(tmp_path.iterdir()))


async def test_persistence_refuses_lost_checkpoint_contents(
    tmp_path: Path, qapp: QApplication
) -> None:
    await seed_profile(tmp_path)
    with closing(sqlite3.connect(tmp_path / "smoke.sqlite3")) as connection:
        connection.execute("UPDATE draft_versions SET body = 'Synthetic lost checkpoint'")
        connection.commit()
    with pytest.raises(RuntimeError, match="checkpoint was not preserved"):
        await verify_persistence(tmp_path)


async def test_upgrade_scenario_preserves_writing_and_original(tmp_path: Path) -> None:
    await check_upgrade(tmp_path)
    assert await asyncio.to_thread(
        lambda: list(tmp_path.glob("upgrade.sqlite3.pre-upgrade-*.sqlite3"))
    )
    with pytest.raises(RuntimeError, match="new test profile"):
        await check_upgrade(tmp_path)
