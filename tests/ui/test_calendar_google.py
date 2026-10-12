"""Google Calendar on the Calendar page over the one Google sign-in (ADR 0019): read-only
rows, Allow Google Calendar, and syncing with Gmail."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import HttpUrl
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from pytestqt.qtbot import QtBot

from mailbrief.domain.google_calendar import CalendarSyncResult, GoogleCoverage, GoogleEvent
from mailbrief.errors import ConfigurationError
from mailbrief.ports.calendar import CalendarNotAllowedError
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.services.google_calendar import STALE_AFTER, sync_window
from mailbrief.ui.calendar_view import (
    ALLOW,
    GOOGLE_ASK,
    GOOGLE_OFF,
    GOOGLE_ON,
    OPEN_GOOGLE,
    REFRESH,
    CalendarPanel,
)
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


def test_google_controls_follow_the_sign_in(panel: CalendarPanel) -> None:
    panel.show_range(week(), TORONTO, TODAY)
    kinds: list[str] = []
    panel.google_requested.connect(kinds.append)
    panel.set_google(GOOGLE_OFF, "Connect Gmail to see your Google Calendar here.")
    assert panel.google_status.text() == "Connect Gmail to see your Google Calendar here."
    assert not panel.allow_button.isVisibleTo(panel)
    assert not panel.refresh_button.isVisibleTo(panel)
    panel.set_google(GOOGLE_ASK, "Allow it.")
    assert panel.allow_button.isVisibleTo(panel) and not panel.refresh_button.isVisibleTo(panel)
    panel.allow_button.click()
    panel.set_google(GOOGLE_ON, "Updated.")
    assert panel.refresh_button.isVisibleTo(panel) and not panel.allow_button.isVisibleTo(panel)
    panel.refresh_button.click()
    assert kinds == [ALLOW, REFRESH]
    panel.set_busy(True)
    assert not panel.refresh_button.isEnabled()
    panel.refresh_button.click()
    assert kinds == [ALLOW, REFRESH]


@pytest.mark.parametrize("mode", [GOOGLE_OFF, GOOGLE_ASK, GOOGLE_ON])
def test_visible_mnemonics_stay_unique(panel: CalendarPanel, mode: str) -> None:
    panel.set_google(mode, "")
    buttons = [
        button
        for button in (
            panel.previous_button,
            panel.this_week_button,
            panel.next_button,
            panel.allow_button,
            panel.refresh_button,
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


def synced(backend: FakeBackend, *, at: datetime = NOW) -> None:
    backend.calendar_permission = True
    first, last = sync_window(TODAY)
    backend.google_coverage = GoogleCoverage(first_day=first, last_day=last, synced_at_utc=at)
    backend.google_events = [google()]


def google_rows(window: MainWindow) -> list[str]:
    return [text for text in rows(window.calendar_panel) if text.endswith("· Work")]


async def test_without_gmail_the_page_says_connect_gmail(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.connect_fail = AuthenticationRequiredError("none saved")
    await window.initialize()
    panel = window.calendar_panel
    assert panel.google_status.text() == "Connect Gmail to see your Google Calendar here."
    assert not panel.allow_button.isVisibleTo(panel)
    assert not panel.refresh_button.isVisibleTo(panel)
    assert backend.calendar_calls == []


async def test_an_older_sign_in_offers_allow_then_syncs(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.calendar_permission = False  # Gmail connected before Calendar was asked for.
    backend.google_events = [google()]
    await window.initialize()
    panel = window.calendar_panel
    assert panel.google_status.text() == (
        "Google Calendar isn't allowed yet. Choose Allow Google Calendar."
    )
    assert panel.allow_button.isVisibleTo(panel)
    panel.allow_button.click()
    await finish(window)
    assert backend.calendar_calls == [("allow_calendar",), ("sync_calendar", MONDAY)]
    assert window.status.text() == "Google Calendar updated: 0 events from 1 calendar."
    assert panel.refresh_button.isVisibleTo(panel) and not panel.allow_button.isVisibleTo(panel)
    assert google_rows(window)


async def test_unticked_calendar_boxes_keep_allow(window: MainWindow, backend: FakeBackend) -> None:
    backend.calendar_permission = False
    backend.calendar_grants = False
    await window.initialize()
    window.calendar_panel.allow_button.click()
    await finish(window)
    assert "tick both calendar boxes" in window.status.text()
    assert backend.calendar_calls == [("allow_calendar",)]
    assert window.calendar_panel.allow_button.isVisibleTo(window.calendar_panel)


async def test_connecting_gmail_brings_calendar_along(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.connect_fail = AuthenticationRequiredError("none saved")
    await window.initialize()
    backend.connect_fail = None
    backend.calendar_permission = True
    backend.google_events = [google()]
    window.start(window._connect)
    await finish(window)
    assert ("sync_calendar", MONDAY) in backend.calendar_calls
    assert google_rows(window)


async def test_disconnecting_gmail_forgets_google_events(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
    await window.initialize()
    assert google_rows(window)
    window.start(window._disconnect)
    await finish(window)
    assert not google_rows(window)
    assert window.calendar_panel.google_status.text() == (
        "Connect Gmail to see your Google Calendar here."
    )


async def test_sync_and_review_also_refreshes_google_calendar(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
    window.start(window.initialize)
    await finish(window)
    window.generate_button.click()
    await asyncio.sleep(0)
    window.review_button.click()
    await asyncio.sleep(0)
    window.approve_button.click()
    await finish(window)
    assert "Brief saved" in window.status.text()
    assert backend.calendar_calls == [("sync_calendar", MONDAY)]


async def test_a_calendar_problem_after_a_brief_only_adds_to_the_status(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
    window.start(window.initialize)
    await finish(window)
    backend.calendar_fail = ProviderError("private")
    window.generate_button.click()
    await asyncio.sleep(0)
    window.review_button.click()
    await asyncio.sleep(0)
    window.approve_button.click()
    await finish(window)
    text = window.status.text()
    assert text.startswith("Brief saved") and text.endswith(
        "Couldn't reach Google Calendar; the Calendar page shows what was saved."
    )
    assert "private" not in text


async def test_fresh_cache_needs_no_sync_and_a_stale_one_refreshes_on_opening(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
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
    synced(backend)
    await window.initialize()
    later = MONDAY + timedelta(weeks=9)
    window.calendar_panel.week_requested.emit(later)
    await finish(window)
    assert backend.calendar_calls == [("sync_calendar", later)]


async def test_a_rejected_sign_in_is_reported_as_gmails(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
    await window.initialize()
    backend.calendar_fail = AuthenticationRequiredError("private detail")
    window.calendar_panel.refresh_button.click()
    await finish(window)
    assert window.connection.text().startswith("Gmail: session expired")
    assert "private" not in window.status.text()


async def test_losing_calendar_access_offers_allow_again(
    window: MainWindow, backend: FakeBackend
) -> None:
    synced(backend)
    await window.initialize()
    backend.calendar_fail = CalendarNotAllowedError("not allowed")
    window.calendar_panel.refresh_button.click()
    await finish(window)
    assert (
        window.status.text() == "Google Calendar isn't allowed yet. Choose Allow Google Calendar."
    )
    assert window.calendar_panel.allow_button.isVisibleTo(window.calendar_panel)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            ProviderPermissionError("Google Calendar denied access; retry."),
            "Google Calendar denied access; retry.",
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
    synced(backend)
    await window.initialize()
    backend.calendar_fail = error
    window.calendar_panel.refresh_button.click()
    await finish(window)
    assert window.status.text() == message


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
