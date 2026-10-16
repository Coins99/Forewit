"""Global pytest configuration and environment hooks."""

import os
import socket
import sqlite3
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.config import Settings
from mailbrief.storage.database import Database

# Ensure Qt runs in offscreen mode in headless test environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's own MAILBRIEF_ settings, such as an OAuth client path exported
    in their shell, so every test starts from the defaults; a test sets what it needs."""
    for name in Settings.model_fields:
        monkeypatch.delenv(f"MAILBRIEF_{name.upper()}", raising=False)


REAL_CONNECT = sqlite3.connect

NETWORK_BLOCKED = "Tests must not use the network."
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "", None})
_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})


def _check_destination(sock: socket.socket, address: Any) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host = address[0] if isinstance(address, tuple) and address else None
    if host not in _LOOPBACK_ADDRESSES:
        raise RuntimeError(NETWORK_BLOCKED)


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any non-loopback lookup or connection.

    A missing HTTP mock must never reach real mail or AI services. On Windows, asyncio's
    proactor bypasses socket.connect; the getaddrinfo guard still covers hostnames there.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host: bytes | str | None, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name not in _LOOPBACK_NAMES:
            raise RuntimeError(NETWORK_BLOCKED)
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> None:
        _check_destination(self, address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check_destination(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def sqlite_without_disk_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop SQLite flushing each commit to disk on every connection a test opens.

    This applies to tests only: the app keeps SQLite's default of flushing every commit to
    disk. It wraps `sqlite3.connect`, which every SQLite connection goes through: the app's
    engine (by way of aiosqlite), Alembic, migrations, backups and recovery. Test databases
    are thrown away, so a crash can't lose anything they need. `journal_mode` is never
    changed, because it is stored in the database file.
    """

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = REAL_CONNECT(*args, **kwargs)
        # Setting the pragma reads the file. One that isn't a usable database is left for the
        # code under test to meet, at the point where it would in the app.
        with suppress(sqlite3.DatabaseError):
            connection.execute("PRAGMA synchronous=OFF")
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Where ``database`` keeps its file."""
    return tmp_path / "mailbrief.sqlite3"


@pytest.fixture
async def database(database_path: Path) -> AsyncIterator[Database]:
    """A fresh database with the current schema, disposed after the test. It is a file, so a
    test can open several sessions on it and see what another one committed."""
    database = Database.from_path(database_path)
    await database.create_schema_for_tests()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """One session on ``database``; nothing is committed for the test."""
    async with database.session() as active:
        yield active


def delete_windows() -> None:
    """Delete every window still open or awaiting deletion, with its widgets."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    from mailbrief.ui.theme import paused_collection

    with paused_collection():
        for window in app.topLevelWidgets():
            # Its child windows go with it. Not closed: a window's closeEvent can start work
            # (a cancel, an autosave) on an event loop the test has already closed.
            if window.parentWidget() is None:
                window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def free_widgets() -> Iterator[None]:
    """Delete every window a test leaves behind, once the test and its fixtures are done.

    pytest-qt closes the widgets ``qtbot.addWidget`` registered and calls ``deleteLater()``
    on them, but with no event loop running nothing ever deletes them. Left alive, they pile
    up test after test, and every later change of the style or palette walks them all.
    pytest-qt closes its widgets before any fixture tears down, and this autouse fixture
    tears down after the test's own fixtures, so nothing it deletes is still in use. It also
    deletes windows a test never registered; still register them, so they are closed too.
    """
    yield
    if "PySide6.QtWidgets" in sys.modules:  # Otherwise the test never used Qt.
        delete_windows()
