"""Google Calendar on the Calendar page: read-only rows, connect, sync and disconnect."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import HttpUrl
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from pytestqt.qtbot import QtBot

from mailbrief.domain.google_calendar import CalendarSyncResult, GoogleCoverage, GoogleEvent
from mailbrief.errors import ConfigurationError
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.services.google_calendar import STALE_AFTER, sync_window
from mailbrief.ui.calendar_view import CONNECT, DISCONNECT, OPEN_GOOGLE, REFRESH, CalendarPanel
from mailbrief.ui.main_window import MainWindow, calendar_sync_text
from tests.ui.test_calendar import MONDAY, NOW, TODAY, TORONTO, rows, stored, timed, week
from tests.ui.test_workflow import FakeBackend

LINK = "https://www.google.com/calendar/event?eid=abc"


def google(title: str = "Team sync", *, hour: int = 9, link: str | None = LINK) -> GoogleEvent:
    start = datetime(TODAY.year, TODAY.month, TODAY.day, hour, tzinfo=TORONTO)
    return GoogleEvent(
        calendar_id="team@group.calendar.google.com",
        calendar_name="Work",
        event_id=f"{title}-{hour}",
        title=title,
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=start + timedelta(minutes=30),
        web_link=None if link is None else HttpUrl(link),
    )


@pytest.fixture
def panel(qtbot: QtBot) -> CalendarPanel:
    result = CalendarPanel()
    qtbot.addWidget(result)
    return result


def test_google_rows_name_their_calendar_and_are_read_only(panel: CalendarPanel) -> None:
    local = stored(timed(TODAY, 9, 10, "Dentist"))
    team, unlinked = google(), google("Lunch", hour=12, link=None)
    panel.show_range(
        week(local).model_copy(update={"google_events": (team, unlinked)}), TORONTO, TODAY
    )
    texts = rows(panel)
    first = texts.index("09:00–10:00 · Dentist")  # At the same start, the owner's first.
    assert texts[first + 1] == "09:00–09:30 · Team sync · Work"
    asked: list[tuple[str, object]] = []
    panel.event_requested.connect(lambda kind, value: asked.append((kind, value)))

    panel.list.setCurrentRow(first + 1)
    assert panel.selected() is None and panel.selected_google() == team
    assert not panel.open_button.isEnabled() and not panel.delete_button.isEnabled()
    assert panel.open_google_button.isEnabled()
    panel.open_google_button.click()
    panel.open_button.click()  # Disabled: nothing is asked.
    assert asked == [(OPEN_GOOGLE, team)]

    panel.list.setCurrentRow(texts.index("12:00–12:30 · Lunch · Work"))
    assert not panel.open_google_button.isEnabled()


def test_google_controls_follow_the_connection(panel: CalendarPanel) -> None:
    panel.show_range(week(), TORONTO, TODAY)
    kinds: list[str] = []
    panel.google_requested.connect(kinds.append)
    panel.set_google(None, "Google Calendar isn't connected.")
    assert panel.google_status.text() == "Google Calendar isn't connected."
    assert panel.connect_button.isVisibleTo(panel)
    assert not panel.refresh_button.isVisibleTo(panel)
    panel.connect_button.click()
    panel.set_google("owner@example.com", "Google Calendar: owner@example.com · updated 09:14")
    assert not panel.connect_button.isVisibleTo(panel)
    panel.refresh_button.click()
    panel.disconnect_button.click()
    assert kinds == [CONNECT, REFRESH, DISCONNECT]
    panel.set_busy(True)
    assert not panel.refresh_button.isEnabled()
    panel.refresh_button.click()
    assert kinds == [CONNECT, REFRESH, DISCONNECT]


@pytest.mark.parametrize("account", [None, "owner@example.com"])
def test_visible_mnemonics_stay_unique(panel: CalendarPanel, account: str | None) -> None:
    """Connect and Disconnect share G because only one of them ever shows."""
    panel.set_google(account, "")
    buttons = [
        button
        for button in (
            panel.previous_button,
            panel.this_week_button,
            panel.next_button,
            panel.connect_button,
            panel.refresh_button,
            panel.disconnect_button,
            panel.new_button,
            panel.open_button,
            panel.delete_button,
            panel.open_google_button,
        )
        if button.isVisibleTo(panel)
    ]
    letters = [b.text()[b.text().index("&") + 1].casefold() for b in buttons]
    assert len(set(letters)) == len(letters)
    assert not set(letters) & set("scdlrutm")


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    result.calendar_now = NOW.astimezone(UTC)
    return result


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    return result


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


def connected(backend: FakeBackend, *, synced: datetime = NOW) -> None:
    backend.calendar_saved = "owner@example.com"
    first, last = sync_window(TODAY)
    backend.google_coverage = GoogleCoverage(first_day=first, last_day=last, synced_at_utc=synced)
    backend.google_events = [google()]


async def test_not_connected_offers_connect(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    panel = window.calendar_panel
    assert panel.google_status.text() == "Google Calendar isn't connected."
    assert panel.connect_button.isVisibleTo(panel)
    assert backend.calendar_calls == []


async def test_connect_reads_google_and_shows_its_events(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    backend.google_events = [google()]
    window.calendar_panel.connect_button.click()
    await finish(window)
    monday = MONDAY  # The week shown follows today.
    assert backend.calendar_calls == [("connect_calendar",), ("sync_calendar", monday)]
    assert window.status.text() == "Google Calendar updated: 0 events from 1 calendar."
    assert window.calendar_panel.google_status.text().startswith(
        "Google Calendar: owner@example.com · updated "
    )
    # Times show in the window's zone, which follows the machine running the tests.
    assert any(text.endswith("· Team sync · Work") for text in rows(window.calendar_panel))


async def test_fresh_cache_needs_no_sync_and_a_stale_one_refreshes_on_opening(
    window: MainWindow, backend: FakeBackend
) -> None:
    connected(backend)
    await window.initialize()
    window.page_shortcuts["calendar"].activated.emit()
    assert window.task is None or window.task.done()
    assert backend.calendar_calls == []
    updated = f"· updated {NOW.astimezone(window.zone):%H:%M}"
    assert window.calendar_panel.google_status.text().endswith(updated)

    assert backend.google_coverage is not None
    backend.google_coverage = backend.google_coverage.model_copy(
        update={"synced_at_utc": NOW - STALE_AFTER - timedelta(minutes=1)}
    )
    await window._refresh_calendar()
    window.page_shortcuts["calendar"].activated.emit()
    await finish(window)
    assert backend.calendar_calls == [("sync_calendar", MONDAY)]


async def test_moving_past_the_cache_reads_that_week(
    window: MainWindow, backend: FakeBackend
) -> None:
    connected(backend)
    await window.initialize()
    later = MONDAY + timedelta(weeks=9)
    window.calendar_panel.week_requested.emit(later)
    await finish(window)
    assert backend.calendar_calls == [("sync_calendar", later)]


async def test_sign_in_failure_offers_connect_and_leaves_gmail_alone(
    window: MainWindow, backend: FakeBackend
) -> None:
    connected(backend)
    await window.initialize()
    gmail = window.connection.text()
    backend.calendar_fail = AuthenticationRequiredError("private detail")
    window.calendar_panel.refresh_button.click()
    await finish(window)
    assert window.status.text() == "Google Calendar needs you to connect it again."
    assert window.connection.text() == gmail
    assert window.calendar_panel.connect_button.isVisibleTo(window.calendar_panel)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            ProviderPermissionError("Google Calendar denied access; reconnect."),
            "Google Calendar denied access; reconnect.",
        ),
        (ProviderRateLimitError("private"), "Google Calendar is busy; try again in a minute."),
        (
            ProviderError("private"),
            "Couldn't reach Google Calendar; the Calendar page shows what was saved.",
        ),
        (
            ConfigurationError("Choose the Google Desktop OAuth client JSON in Settings first."),
            "Choose the Google Desktop OAuth client JSON in Settings first.",
        ),
        (
            RuntimeError("private"),
            "Couldn't reach Google Calendar; the Calendar page shows what was saved.",
        ),
    ],
)
async def test_failures_are_told_in_fixed_words(
    window: MainWindow, backend: FakeBackend, error: Exception, message: str
) -> None:
    await window.initialize()
    backend.calendar_fail = error
    window.calendar_panel.connect_button.click()
    await finish(window)
    assert window.status.text() == message


async def test_disconnect_forgets_google_events(window: MainWindow, backend: FakeBackend) -> None:
    connected(backend)
    await window.initialize()
    assert any(text.endswith("· Work") for text in rows(window.calendar_panel))
    window.calendar_panel.disconnect_button.click()
    await finish(window)
    assert backend.calendar_calls == [("disconnect_calendar",)]
    assert window.status.text().startswith("Google Calendar disconnected")
    assert not any(text.endswith("· Work") for text in rows(window.calendar_panel))
    assert window.calendar_panel.google_status.text() == "Google Calendar isn't connected."


async def test_a_failed_disconnect_keeps_the_connection(
    window: MainWindow, backend: FakeBackend
) -> None:
    connected(backend)
    await window.initialize()
    backend.calendar_fail = ProviderError("private")
    window.calendar_panel.disconnect_button.click()
    await finish(window)
    assert window.calendar_panel.refresh_button.isVisibleTo(window.calendar_panel)


def test_only_google_calendar_links_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    window._open_calendar_link(google())
    window._open_calendar_link(google(link=None))
    window._open_calendar_link(google(link="https://evil.example/calendar"))
    assert opened == [LINK]


def test_calendar_sync_text() -> None:
    def result(**fields: object) -> CalendarSyncResult:
        return CalendarSyncResult.model_validate(
            {"calendars": 2, "synced": 2, "failed": 0, "events": 1} | fields
        )

    assert calendar_sync_text(result()) == "Google Calendar updated: 1 event from 2 calendars."
    assert calendar_sync_text(result(synced=1, failed=1)) == (
        "Google Calendar updated 1 of 2 calendars; the others show what was saved."
    )
    assert calendar_sync_text(result(stopped_code="RATE_LIMITED")).startswith(
        "Google Calendar stopped"
    )
    assert calendar_sync_text(result(stopped_code="SOMETHING")) == (
        "Couldn't reach Google Calendar; the Calendar page shows what was saved."
    )
