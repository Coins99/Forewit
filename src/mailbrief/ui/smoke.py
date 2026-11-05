"""Opt-in package smoke check with no real account, vault, network or profile access."""

import asyncio
import importlib
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from PySide6.QtCore import QLibraryInfo
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QApplication

from mailbrief.domain.actions import Action
from mailbrief.domain.capture import CaptureRequest
from mailbrief.domain.drafts import Draft
from mailbrief.domain.messages import AccountIdentity, EmailContact, NormalizedMessage, ProviderKind
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.infra.data_lock import create_data_lock
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.drafts import DraftService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config, upgrade_database
from mailbrief.storage.recovery import inspect_backup, restore_backup
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.preferences_view import region_zones
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import FONT_FAMILY, current_tokens, icon_pixmap

SMOKE_ZONE = "America/New_York"
SMOKE_NOW = datetime(2026, 11, 3, 15, tzinfo=UTC)
SMOKE_TASK = CaptureRequest(
    command_id="12345678-1234-4234-8234-123456789abd",
    kind="task",
    title="Package focus task",
)
SMOKE_CAPTURE = CaptureRequest(
    command_id="12345678-1234-4234-8234-123456789abc",
    kind="note",
    title="Package note",
    body="Synthetic owner writing.",
)


async def verify_writing(runtime: DesktopRuntime) -> None:
    """Check both current writing and its saved checkpoint, without reseeding either."""
    notes = await runtime.list_drafts()
    if len(notes) != 1:
        raise RuntimeError("Previous writing was not preserved.")
    note = await runtime.get_draft(notes[0].public_id)
    versions = await runtime.draft_versions(note.public_id)
    if note.body != "Synthetic owner writing." or not versions:
        raise RuntimeError("Previous writing versions were not preserved.")
    version = await runtime.draft_version(note.public_id, versions[0].number)
    if version.body != note.body or version.title != note.title:
        raise RuntimeError("Previous writing checkpoint was not preserved.")


async def seed_focus(runtime: DesktopRuntime) -> None:
    task = await runtime.capture(SMOKE_TASK)
    if not isinstance(task, Action):
        raise RuntimeError("Capture did not save a task.")
    zone = ZoneInfo(SMOKE_ZONE)
    summary = await runtime.today_summary(SMOKE_NOW.astimezone(zone).date(), zone, SMOKE_NOW)
    revision = 0 if summary.focus is None else summary.focus.revision
    await runtime.select_day_task(summary.day, zone, task.public_id, revision)


async def verify_focus(runtime: DesktopRuntime) -> None:
    """Read previous selection and task without recreating either record."""
    zone = ZoneInfo(SMOKE_ZONE)
    summary = await runtime.today_summary(SMOKE_NOW.astimezone(zone).date(), zone, SMOKE_NOW)
    plan = summary.focus
    if plan is None or plan.revision != 1 or len(plan.items) != 1:
        raise RuntimeError("Previous daily focus was not preserved.")
    task = plan.items[0].action
    if task is None or task.title != SMOKE_TASK.title or plan.items[0].position != 0:
        raise RuntimeError("Previous focus task was not preserved.")


async def verify_persistence(directory: Path) -> None:
    """Inspect the previous process's records before performing any seed or save."""
    if not (directory / "smoke.sqlite3").is_file():
        raise RuntimeError("Missing previous package profile.")
    runtime = DesktopRuntime(directory / "smoke.sqlite3")
    try:
        await runtime.load_saved()
        if (await runtime.get_preferences()).groq_model != "smoke-test-model":
            raise RuntimeError("Previous settings were not preserved.")
        if (await runtime.get_owner_preferences()).time_zone != SMOKE_ZONE:
            raise RuntimeError("Previous preferences were not preserved.")
        await verify_writing(runtime)
        await verify_focus(runtime)
    finally:
        await runtime.close()


async def check_upgrade(directory: Path) -> None:
    """Migrate an old disposable profile and reject an unknown schema without writes."""
    from alembic import command

    path = directory / "upgrade.sqlite3"
    if path.exists():
        raise RuntimeError("Upgrade scenario requires a new test profile.")
    await asyncio.to_thread(command.upgrade, migration_config(path), "20260929_0009")
    # Written as that revision holds a note: today's ORM has columns it lacks.
    note_id = str(uuid4())
    await asyncio.to_thread(_old_note, path, note_id)
    await asyncio.to_thread(upgrade_database, path)
    backups = await asyncio.to_thread(
        lambda: list(directory.glob("upgrade.sqlite3.pre-upgrade-*.sqlite3"))
    )
    if not backups:
        raise RuntimeError("Upgrade did not retain its original database.")
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            restored = await DraftService(session).get(note_id)
            if restored.body != "Synthetic upgrade writing.":
                raise RuntimeError("Upgrade lost writing.")
    finally:
        await database.dispose()
    await asyncio.to_thread(check_unknown_schema, directory / "unknown.sqlite3")


def _old_note(path: Path, public_id: str) -> None:
    """A synthetic note and its first version, in the columns revision 0009 has."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    with closing(sqlite3.connect(path)) as connection:
        draft_id = connection.execute(
            "INSERT INTO drafts(public_id,kind,title,to_text,cc_text,body,created_at_utc,"
            "updated_at_utc,revision) VALUES(?,'note','Before upgrade','','',"
            "'Synthetic upgrade writing.',?,?,1)",
            (public_id, now, now),
        ).lastrowid
        connection.execute(
            "INSERT INTO draft_versions(draft_id,number,origin,title,to_text,cc_text,body,"
            "created_at_utc) VALUES(?,1,'created','Before upgrade','','',"
            "'Synthetic upgrade writing.',?)",
            (draft_id, now),
        )
        connection.commit()


def check_unknown_schema(unknown: Path) -> None:
    """An unsupported synthetic database must be refused before modification."""
    with closing(sqlite3.connect(unknown)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('unknown_future_schema')")
        connection.commit()
    original = unknown.read_bytes()
    try:
        upgrade_database(unknown)
    except Exception:
        if unknown.read_bytes() != original:
            raise RuntimeError("Unsupported database was modified.") from None
    else:
        raise RuntimeError("Unsupported database was accepted.")


async def run_scenario(directory: Path, scenario: str) -> None:
    """All scenarios use the explicitly selected disposable folder; none read the vault."""
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    # The one-file bootloader owns the parent process; the checker needs the child's PID
    # to terminate only its own test process if a scenario stalls or is forcibly stopped.
    (directory / f"child-{os.getppid()}.json").write_text(
        json.dumps({"pid": os.getpid()}),
        encoding="utf-8",
    )
    if scenario == "full":
        await check_package(directory)
        return
    result: dict[str, object] = {"ok": True, "scenario": scenario}
    if scenario == "verify":
        await verify_persistence(directory)
    elif scenario == "upgrade":
        await check_upgrade(directory)
    else:
        lock = create_data_lock(directory)
        acquired = lock.tryLock(0)
        result["acquired"] = acquired
        try:
            if scenario == "hold-lock":
                if not acquired:
                    raise RuntimeError("Test lock unavailable.")
                await verify_persistence(directory)
                (directory / "lock-ready.json").write_text(
                    json.dumps({"pid": os.getpid()}),
                    encoding="utf-8",
                )
                deadline = time.monotonic() + 60
                while not (directory / "stop-lock").exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Lock scenario timed out.")
                    await asyncio.sleep(0.1)
        finally:
            if acquired:
                lock.unlock()
    (directory / "smoke-result.json").write_text(json.dumps(result), encoding="utf-8")


async def seed_metadata(path: Path) -> None:
    """Populate the explicit disposable database with a synthetic cached message."""
    database = await asyncio.to_thread(Database.from_path, path)
    try:
        async with database.transaction() as session:
            account = await AccountRepository(session).upsert(
                AccountIdentity(
                    provider=ProviderKind.GMAIL,
                    provider_account_id="package-smoke",
                    email_address="smoke@example.invalid",
                )
            )
            await MessageRepository(session).upsert_messages(
                account.id,
                [
                    NormalizedMessage(
                        provider=ProviderKind.GMAIL,
                        provider_account_id="package-smoke",
                        provider_message_id="synthetic-message",
                        subject="Package check",
                        sender=EmailContact(address="sender@example.invalid"),
                        received_at_utc=datetime.now(UTC),
                        is_read=False,
                        has_attachments=False,
                        body_preview="Synthetic metadata preview.",
                        web_link="https://mail.google.com/mail/u/?authuser=smoke%40example.invalid#all/test",
                    )
                ],
            )
    finally:
        await database.dispose()


async def check_package(directory: Path) -> None:
    """Exercise two launches, settings and preferences, bundled migrations, Qt, TLS and
    time zone data."""
    # Class priority checks dependencies without constructing a vault or reading secrets.
    backend = "Windows" if sys.platform == "win32" else "macOS"
    module = importlib.import_module(f"keyring.backends.{backend}")
    backend_class = module.WinVaultKeyring if sys.platform == "win32" else module.Keyring
    if backend_class.priority <= 0:
        raise RuntimeError("Native vault backend unavailable.")
    plugin = "qwindows.dll" if sys.platform == "win32" else "libqcocoa.dylib"
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    if not (plugins / "platforms" / plugin).is_file():
        raise RuntimeError("Native Qt platform plugin unavailable.")
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        raise RuntimeError("Qt application unavailable.")
    if sys.platform == "win32" and QGuiApplication.platformName() == "offscreen":
        # The Windows offscreen plugin has no system font discovery. Use an OS font
        # for the rendering check without bundling it or touching a user profile.
        font_path = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        identifier = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(identifier)
        if not families:
            raise RuntimeError("Smoke font unavailable.")
        application.setFont(QFont(families[0], 9))
    if not QFontMetrics(application.font()).inFontUcs4(ord("A")):
        raise RuntimeError("Qt text rendering unavailable.")
    # Creating the HTTP client loads the bundled TLS trust data without sending a request.
    async with httpx.AsyncClient(trust_env=False):
        pass
    ZoneInfo("America/Toronto")
    if SMOKE_ZONE not in region_zones():
        raise RuntimeError("Time zone list unavailable.")
    if FONT_FAMILY not in QFontDatabase.families():
        raise RuntimeError("Bundled font unavailable.")
    from mailbrief.providers.local.notices import local_notices

    if not local_notices():
        raise RuntimeError("Local AI notices unavailable.")
    if icon_pixmap("refresh", current_tokens().text, 16).isNull():
        raise RuntimeError("Bundled icons unavailable.")
    if (
        sys.platform == "win32"
        and getattr(sys, "frozen", False)
        and application.windowIcon().isNull()
    ):
        raise RuntimeError("Application icon unavailable.")
    for _ in range(2):
        runtime = DesktopRuntime(directory / "smoke.sqlite3")
        try:
            await runtime.load_saved()
            await seed_metadata(directory / "smoke.sqlite3")
            await runtime.save_preferences(DesktopPreferences(groq_model="smoke-test-model"))
            if (await runtime.get_preferences()).groq_model != "smoke-test-model":
                raise RuntimeError("Settings restoration failed.")
            owner = await runtime.get_owner_preferences()
            if owner.revision == 0:
                owner = await runtime.save_owner_preferences(
                    PreferencesEdit(time_zone=SMOKE_ZONE), 0
                )
            if owner.time_zone != SMOKE_ZONE:
                raise RuntimeError("Preferences restoration failed.")
            if not await runtime.list_drafts():
                note = await runtime.capture(SMOKE_CAPTURE)
                if not isinstance(note, Draft):
                    raise RuntimeError("Capture did not save a note.")
                await runtime.checkpoint_draft(note.public_id, note.revision)
            await verify_writing(runtime)
            if _ == 0:
                await seed_focus(runtime)
            await verify_focus(runtime)
            window = MainWindow(runtime, database_path=directory / "smoke.sqlite3")
            window.now = lambda: SMOKE_NOW
            window.zone = ZoneInfo(SMOKE_ZONE)
            window.show()
            await window._refresh_day()
            QApplication.processEvents()
            if window.grab().isNull():
                raise RuntimeError("Qt rendering failed.")
            await window._open_data()
            if window.data_dialog is None or window.data_dialog.grab().isNull():
                raise RuntimeError("Recovery controls failed to render.")
            window.data_dialog.reject()
            accounts = await runtime.cached_accounts()
            window.cached_dialog.configure(accounts)
            # The saved zone decides the day, whatever the system zone is.
            page = await runtime.cached_messages(
                accounts[0].account_id, datetime.now(UTC).astimezone(ZoneInfo(SMOKE_ZONE)).date()
            )
            if len(page.messages) != 1:
                raise RuntimeError("Offline metadata restoration failed.")
            window.cached_dialog.show_page(page)
            window.dialog_guard.show_dialog(window.cached_dialog)
            QApplication.processEvents()
            if "Synthetic metadata preview." not in window.cached_dialog.details.toPlainText():
                raise RuntimeError("Cached metadata display failed.")
            window.cached_dialog.reject()
            window.close()
            await window.shutdown()
        finally:
            await runtime.close()
    suffix = uuid4().hex
    archive = directory / f"smoke-backup-{suffix}.zip"
    export = directory / f"smoke-writing-{suffix}.json"
    restored = directory / f"smoke-restored-{suffix}.sqlite3"
    await asyncio.to_thread(create_backup, directory / "smoke.sqlite3", archive)
    await asyncio.to_thread(inspect_backup, archive)
    await asyncio.to_thread(restore_backup, archive, restored)
    database = Database.from_path(restored)
    try:
        await export_writing(database, export)
        payload = json.loads(await asyncio.to_thread(export.read_text, encoding="utf-8"))
        if payload["records"]["drafts"][0]["body"] != "Synthetic owner writing.":
            raise RuntimeError("Portable writing export failed.")
        if len(payload["records"]["day_plan_items"]) != 1:
            raise RuntimeError("Portable focus export failed.")
        preview = await preview_cleanup(database, CleanupRequest(CleanupKind.CACHE, 1))
        await apply_cleanup(database, preview)
    finally:
        await database.dispose()
    recovered = DesktopRuntime(restored)
    try:
        await recovered.load_saved()
        await verify_focus(recovered)
        await recovered.capture(SMOKE_CAPTURE)
        if len(await recovered.list_drafts()) != 1:
            raise RuntimeError("Recovery lost owner writing or duplicated a captured note.")
    finally:
        await recovered.close()
    report = json.dumps({"ok": True, "platform": sys.platform, "launches": 2})
    await asyncio.to_thread((directory / "smoke-result.json").write_text, report, encoding="utf-8")
