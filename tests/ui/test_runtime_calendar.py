"""The desktop runtime's Google Calendar calls over the one Google sign-in (ADR 0019)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from mailbrief.config import Settings
from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent
from mailbrief.ports.calendar import CalendarNotAllowedError
from mailbrief.ui import runtime as runtime_module
from mailbrief.ui.runtime import DesktopRuntime

PRIMARY = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)
TORONTO = ZoneInfo("America/Toronto")
WEEK = (date(2026, 10, 12), date(2026, 10, 18))


class Auth:
    def __init__(self) -> None:
        self.calendar_allowed = False
        self.grants = True  # What asking again gets from Google.
        self.connects: list[tuple[bool, bool]] = []

    async def connect(self, *, silent_only: bool = False, ask_again: bool = False) -> object:
        self.connects.append((silent_only, ask_again))
        if ask_again:
            self.calendar_allowed = self.grants
        return SimpleNamespace(email_address="owner@example.com")


class Reader:
    def __init__(self, auth: Auth) -> None:
        self.auth = auth

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


class Provider:
    def __init__(self, auth: Auth) -> None:
        self._auth = auth

    async def connect(self) -> object:
        return await self._auth.connect(silent_only=True)

    @property
    def calendar_allowed(self) -> bool:
        return self._auth.calendar_allowed


async def test_calendar_follows_the_one_google_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = Auth()
    legacy: list[None] = []
    cleared: list[None] = []

    @asynccontextmanager
    async def gmail_provider(settings: Settings, silent_only: bool) -> AsyncIterator[Provider]:
        yield Provider(auth)

    @asynccontextmanager
    async def gmail_auth(settings: Settings) -> AsyncIterator[Auth]:
        yield auth

    @asynccontextmanager
    async def google_calendar(settings: Settings) -> AsyncIterator[Reader]:
        yield Reader(auth)

    class Store:
        def clear(self) -> None:
            cleared.append(None)

    monkeypatch.setattr(runtime_module, "gmail_provider", gmail_provider)
    monkeypatch.setattr(runtime_module, "gmail_auth", gmail_auth)
    monkeypatch.setattr(runtime_module, "google_calendar", google_calendar)
    monkeypatch.setattr(runtime_module, "GmailCredentialStore", Store)
    monkeypatch.setattr(runtime_module, "forget_legacy_sign_in", lambda: legacy.append(None))
    runtime = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    try:
        await runtime.load_saved()
        assert await runtime.calendar_allowed() is None  # Nothing known before a sign-in.
        assert await runtime.connect(silent_only=True) == "owner@example.com"
        assert await runtime.calendar_allowed() is False  # The sign-in predates Calendar.
        with pytest.raises(CalendarNotAllowedError):
            await runtime.sync_calendar(date(2026, 10, 14), TORONTO)

        assert await runtime.allow_calendar() is True
        assert auth.connects[-1] == (False, True)  # Asked again, in the browser.
        result = await runtime.sync_calendar(date(2026, 10, 14), TORONTO)
        assert (result.calendars, result.synced, result.events) == (1, 1, 1)
        shown = await runtime.calendar_range(*WEEK, TORONTO)
        assert [event.title for event in shown.google_events] == ["Standup"]
        assert legacy == [None]  # The old calendar sign-in is removed once per start.

        await runtime.disconnect()
        assert cleared == [None] and await runtime.calendar_allowed() is None
        shown = await runtime.calendar_range(*WEEK, TORONTO)
        assert shown.google_events == () and shown.google_coverage is None
    finally:
        await runtime.close()


async def test_a_failed_cleanup_never_fails_a_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = Auth()

    @asynccontextmanager
    async def gmail_provider(settings: Settings, silent_only: bool) -> AsyncIterator[Provider]:
        yield Provider(auth)

    def broken() -> None:
        raise RuntimeError("vault locked")

    monkeypatch.setattr(runtime_module, "gmail_provider", gmail_provider)
    monkeypatch.setattr(runtime_module, "forget_legacy_sign_in", broken)
    runtime = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    try:
        assert await runtime.connect(silent_only=True) == "owner@example.com"
    finally:
        await runtime.close()
