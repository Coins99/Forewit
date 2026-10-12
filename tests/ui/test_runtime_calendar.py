"""The desktop runtime's Google Calendar calls: vault-only status, sync, disconnect."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from mailbrief.config import Settings
from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent
from mailbrief.providers.gmail.cache import RefreshCredential
from mailbrief.ui import runtime as runtime_module
from mailbrief.ui.runtime import DesktopRuntime

PRIMARY = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)
TORONTO = ZoneInfo("America/Toronto")


class Store:
    def __init__(self) -> None:
        self.credential: RefreshCredential | None = None
        self.cleared = 0

    def load(self) -> RefreshCredential | None:
        return self.credential

    def clear(self) -> None:
        self.cleared += 1
        self.credential = None


class Auth:
    def __init__(self) -> None:
        self.connects: list[bool] = []

    async def connect(self, *, silent_only: bool = False) -> str:
        self.connects.append(silent_only)
        return "owner@example.com"


class Reader:
    def __init__(self) -> None:
        self.auth = Auth()

    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        return (PRIMARY,)

    async def list_events(self, calendar: GoogleCalendarInfo, *_: Any) -> tuple[GoogleEvent, ...]:
        start = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
        return (
            GoogleEvent(
                calendar_id=calendar.calendar_id,
                calendar_name=calendar.name,
                event_id="e",
                title="Standup",
                all_day=False,
                starts_at_utc=start,
                ends_at_utc=start,
            ),
        )


async def test_calendar_calls_compose_the_reader_and_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, reader = Store(), Reader()
    settings_seen: list[Settings] = []

    @asynccontextmanager
    async def google_calendar(settings: Settings) -> AsyncIterator[Reader]:
        settings_seen.append(settings)
        yield reader

    monkeypatch.setattr(runtime_module, "google_calendar", google_calendar)
    monkeypatch.setattr(runtime_module, "CalendarCredentialStore", lambda: store)
    runtime = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    try:
        await runtime.load_saved()
        assert await runtime.calendar_account() is None  # Read from the vault only.
        assert settings_seen == []
        store.credential = RefreshCredential(
            client_id="c", email_address="owner@example.com", refresh_token=SecretStr("r")
        )
        assert await runtime.calendar_account() == "owner@example.com"

        assert await runtime.connect_calendar() == "owner@example.com"
        result = await runtime.sync_calendar(date(2026, 10, 14), TORONTO)
        assert (result.calendars, result.synced, result.events) == (1, 1, 1)
        assert reader.auth.connects == [False, True]  # Interactive, then silent for the sync.
        shown = await runtime.calendar_range(date(2026, 10, 12), date(2026, 10, 18), TORONTO)
        assert [event.title for event in shown.google_events] == ["Standup"]

        await runtime.disconnect_calendar()
        assert store.cleared == 1 and await runtime.calendar_account() is None
        shown = await runtime.calendar_range(date(2026, 10, 12), date(2026, 10, 18), TORONTO)
        assert shown.google_events == () and shown.google_coverage is None
    finally:
        await runtime.close()
