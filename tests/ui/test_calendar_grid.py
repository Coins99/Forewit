"""The Calendar's Week and Month grids: their layout arithmetic, what they show, how they
choose and open entries, and the window reading the days of the view chosen."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange, MarkerKind
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.ui.appearance import Appearance
from mailbrief.ui.calendar_grid import HOUR_PX, HOURS_PAD
from mailbrief.ui.calendar_layout import (
    CalendarView,
    Part,
    add_months,
    bars,
    entry_key,
    month_days,
    pack_columns,
    pack_lanes,
    shown_month,
    timed_part,
)
from mailbrief.ui.calendar_view import NEW, OPEN, CalendarPanel, month_title
from mailbrief.ui.event_editor import local_instant
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import apply_theme
from mailbrief.ui.theme.tokens import ThemeMode
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
TODAY = date(2026, 10, 14)  # A Wednesday.
NOW = datetime(2026, 10, 14, 14, 20, tzinfo=TORONTO)
ACTION = "5b1f3c2e-8d4a-4e6b-9c7d-1a2b3c4d5e6f"
_numbers = iter(range(1, 10_000))


def event(
    title: str, day: date, start: time, end: time, *, last: date | None = None
) -> CalendarEvent:
    number = next(_numbers)
    return CalendarEvent(
        public_id=f"00000000-0000-4000-8000-{number:012d}",
        title=title,
        notes="",
        all_day=False,
        starts_at_utc=local_instant(day, start, TORONTO),
        ends_at_utc=local_instant(last or day, end, TORONTO),
        start_date=None,
        last_date=None,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
    )


def whole(title: str, first: date, last: date) -> CalendarEvent:
    number = next(_numbers)
    return CalendarEvent(
        public_id=f"00000000-0000-4000-8000-{number:012d}",
        title=title,
        notes="",
        all_day=True,
        starts_at_utc=None,
        ends_at_utc=None,
        start_date=first,
        last_date=last,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
    )


def google(title: str, day: date, start: time | None, end: time | None, name: str) -> GoogleEvent:
    number = next(_numbers)
    if start is None or end is None:
        return GoogleEvent(
            calendar_id=f"{name.casefold()}@example.com",
            calendar_name=name,
            event_id=f"g{number}",
            title=title,
            all_day=True,
            start_date=day,
            last_date=day,
            web_link="https://calendar.google.com/calendar/event?eid=x",
        )
    return GoogleEvent(
        calendar_id=f"{name.casefold()}@example.com",
        calendar_name=name,
        event_id=f"g{number}",
        title=title,
        all_day=False,
        starts_at_utc=local_instant(day, start, TORONTO),
        ends_at_utc=local_instant(day, end, TORONTO),
        web_link="https://calendar.google.com/calendar/event?eid=x",
    )


def marker(kind: MarkerKind, day: date, title: str, **extra: object) -> ActionMarker:
    return ActionMarker.model_validate(
        {"kind": kind, "day": day, "action_public_id": ACTION, "action_title": title} | extra
    )


def fill(backend: FakeBackend) -> None:
    """A full month around Wednesday Oct 14, 2026: overlaps, a flight past midnight, a
    conference over two days, Google's calendars, the actions' dates and a busy day."""
    owned = [
        event("Team standup", MONDAY, time(9), time(9, 30)),
        event("Design review", MONDAY + timedelta(days=1), time(10), time(11, 30)),
        event("Dentist", MONDAY + timedelta(days=1), time(10, 30), time(11)),
        event("Morning run", TODAY, time(7), time(7, 45)),
        event("Standup", TODAY, time(9), time(9, 30)),
        event("Call with Lena", TODAY, time(11), time(11, 30)),
        event("Write the proposal", TODAY, time(15), time(16, 30)),
        whole("Conference in Montréal", TODAY + timedelta(days=1), TODAY + timedelta(days=2)),
        event(
            "Flight home",
            TODAY + timedelta(days=2),
            time(22),
            time(1, 15),
            last=TODAY + timedelta(days=3),
        ),
        event("Yoga", date(2026, 10, 6), time(18), time(19)),
        event("Quarterly planning", date(2026, 10, 20), time(9), time(12)),
        event("Dinner with Sam", date(2026, 10, 22), time(19), time(21)),
        whole("Trip to Ottawa", date(2026, 10, 27), date(2026, 10, 29)),
        event("Doctor", date(2026, 11, 3), time(8, 30), time(9)),
    ]
    backend.events = {item.public_id: item for item in owned}
    backend.google_events = [
        google("1:1 with Priya", TODAY, time(14), time(14, 30), "Work"),
        google("Lunch with Sam", MONDAY, time(12), time(13), "Family"),
        google("School holiday", MONDAY + timedelta(days=6), None, None, "Family"),
    ]
    backend.markers = [
        marker(
            MarkerKind.DEADLINE,
            TODAY,
            "Approve the Q3 budget",
            due_at_utc=NOW.replace(hour=17, minute=0),
        ),
        marker(
            MarkerKind.TARGET, TODAY + timedelta(days=3), "Send the conference deck", waiting=True
        ),
        marker(MarkerKind.DEADLINE, date(2026, 10, 2), "Pay the rent"),
    ]


async def shown_window(qtbot: QtBot, view: CalendarView) -> MainWindow:
    backend = FakeBackend()
    fill(backend)
    window = MainWindow(backend, saved_appearance=Appearance(calendar_view=view))
    window.now = lambda: NOW
    qtbot.addWidget(window)
    await window.initialize()
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    window._show_page("calendar")
    window._read_calendar()
    await window.drain_calendar_reads()
    return window


@pytest.mark.parametrize("view", list(CalendarView))
@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
async def test_calendar_views_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, view: CalendarView
) -> None:
    apply_theme(qapp, mode=mode)
    window = await shown_window(qtbot, view)
    panel = window.calendar_panel
    assert panel.view() is view
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    if view is not CalendarView.LIST:  # The grids show a selected block.
        proposal = next(
            entry
            for _day, entry in panel._rows
            if isinstance(entry, CalendarEvent) and entry.title == "Write the proposal"
        )
        panel.select_entry(entry_key(proposal), TODAY)
    save_shot(window.grab(), f"calendar-{view}-{mode}")


# The layout arithmetic.


def test_a_month_grid_is_six_weeks_from_a_monday() -> None:
    first, last = month_days(date(2026, 10, 14))
    assert first == date(2026, 9, 28) and first.weekday() == 0
    assert (last - first).days == 41
    assert shown_month(first) == date(2026, 10, 1)
    # A month starting on a Monday starts its grid on the 1st.
    assert month_days(date(2026, 6, 20))[0] == date(2026, 6, 1)
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert month_title(date(2026, 10, 1)) == "October 2026"


def test_a_timed_event_is_cut_at_midnight() -> None:
    flight = event("Flight", TODAY, time(22), time(1, 15), last=TODAY + timedelta(days=1))
    first = timed_part(flight, TODAY, TORONTO)
    second = timed_part(flight, TODAY + timedelta(days=1), TORONTO)
    assert first is not None and (first.start, first.end) == (22 * 60, 24 * 60)
    assert second is not None and (second.start, second.end) == (0, 75)
    assert timed_part(flight, TODAY + timedelta(days=2), TORONTO) is None
    short = event("Ping", TODAY, time(23, 55), time(23, 58))
    part = timed_part(short, TODAY, TORONTO)
    assert part is not None and part.end == 24 * 60 and part.end - part.start == 20


def test_overlapping_blocks_share_their_day() -> None:
    def part(start: int, end: int) -> Part:
        return Part(event("x", TODAY, time(0), time(1)), TODAY, start, end)

    placed = pack_columns([part(600, 690), part(630, 660), part(700, 760), part(540, 570)])
    columns = {(p.start, p.end): (p.column, p.columns) for p in placed}
    assert columns[(600, 690)] == (0, 2) and columns[(630, 660)] == (1, 2)
    assert columns[(700, 760)] == (0, 1) and columns[(540, 570)] == (0, 1)


def test_bars_take_the_first_free_lane() -> None:
    shown = CalendarRange(
        first_day=MONDAY,
        last_day=MONDAY + timedelta(days=6),
        events=(
            whole("Trip", MONDAY + timedelta(days=1), MONDAY + timedelta(days=3)),
            whole("Holiday", MONDAY + timedelta(days=3), MONDAY + timedelta(days=3)),
            whole("Earlier", MONDAY - timedelta(days=2), MONDAY),
        ),
        markers=(marker(MarkerKind.DEADLINE, MONDAY + timedelta(days=2), "Pay"),),
    )
    found = pack_lanes(bars(shown, MONDAY, MONDAY + timedelta(days=6), TORONTO))
    lanes = {
        bar.entry.title if not isinstance(bar.entry, ActionMarker) else "Pay": bar for bar in found
    }
    assert (lanes["Earlier"].first, lanes["Earlier"].last, lanes["Earlier"].starts_before) == (
        0,
        0,
        True,
    )
    assert lanes["Trip"].lane == 0 and lanes["Earlier"].lane == 0
    assert lanes["Pay"].lane == 1 and lanes["Holiday"].lane == 1


# The grids, through the page.


@pytest.fixture
def panel(qtbot: QtBot) -> CalendarPanel:
    result = CalendarPanel()
    qtbot.addWidget(result)
    result.resize(1000, 700)
    result.show()
    qtbot.waitExposed(result)
    return result


def week_range() -> CalendarRange:
    backend = FakeBackend()
    fill(backend)
    return backend._range(MONDAY, MONDAY + timedelta(days=6), TORONTO)


def test_the_week_grid_chooses_and_opens_through_the_page(
    panel: CalendarPanel, qtbot: QtBot
) -> None:
    asked: list[tuple[str, object]] = []
    panel.event_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week_range(), TORONTO, TODAY)
    grid = panel.week_grid
    assert panel.view() is CalendarView.WEEK and grid.isVisible()
    hits = grid.hits(grid.hours)
    proposal = next(hit for hit in hits if getattr(hit.entry, "title", "") == "Write the proposal")
    QTest.mouseClick(
        grid.hours,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        proposal.rect.center().toPoint(),
    )
    selected = panel.selected()
    assert selected is not None and selected.title == "Write the proposal"
    assert grid.selection()[0] == TODAY
    QTest.mouseDClick(
        grid.hours,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        proposal.rect.center().toPoint(),
    )
    # A double-click on an empty hour asks for a new event that day: Saturday at 05:00, clear
    # of the flight that ran past midnight into it.
    empty = QPointF(grid.column_left(5) + 10, HOURS_PAD + 5 * HOUR_PX)
    QTest.mouseDClick(
        grid.hours, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty.toPoint()
    )
    assert asked == [(OPEN, selected), (NEW, MONDAY + timedelta(days=5))]
    # Overlapping blocks sit side by side.
    tuesday = [hit for hit in hits if hit.day == MONDAY + timedelta(days=1)]
    review, dentist = sorted(tuesday, key=lambda hit: hit.rect.left())
    assert review.rect.right() <= dentist.rect.left()


def test_the_week_grid_s_keyboard(panel: CalendarPanel) -> None:
    panel.show_range(week_range(), TORONTO, TODAY)
    grid = panel.week_grid
    grid.setFocus()
    QTest.keyClick(grid, Qt.Key.Key_Down)  # Today's first entry: its bar, the deadline.
    assert isinstance(panel._entry(), ActionMarker)
    QTest.keyClick(grid, Qt.Key.Key_Down)  # Then the blocks by start: the morning run.
    entry = panel._entry()
    assert isinstance(entry, CalendarEvent) and entry.title == "Morning run"
    QTest.keyClick(grid, Qt.Key.Key_Right)
    assert grid.selection() == (TODAY + timedelta(days=1), None)
    asked: list[tuple[str, object]] = []
    panel.event_requested.connect(lambda kind, value: asked.append((kind, value)))
    QTest.keyClick(grid, Qt.Key.Key_Return)  # An empty day: a new event on it.
    assert asked == [(NEW, TODAY + timedelta(days=1))]


def test_the_month_grid_shows_more_and_moves_months(panel: CalendarPanel, qtbot: QtBot) -> None:
    months: list[object] = []
    panel.month_requested.connect(months.append)
    panel.views.setCurrentIndex(1)
    assert panel.view() is CalendarView.MONTH
    backend = FakeBackend()
    fill(backend)
    panel.show_range(backend._range(*month_days(TODAY), TORONTO), TORONTO, TODAY)
    assert panel.heading.text() == "Calendar · October 2026"
    assert panel.this_week_button.text() == "This mont&h"
    grid = panel.month_grid
    busy = [hit for hit in grid.hits() if hit.day == TODAY]
    assert any(hit.more for hit in busy)  # Today holds more than its tile shows.
    more = next(hit for hit in busy if hit.more)
    QTest.mouseClick(
        grid,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        more.rect.center().toPoint(),
    )
    assert panel.view() is CalendarView.LIST and panel.focus_day() == TODAY
    panel.views.setCurrentIndex(1)
    panel.previous_button.click()
    panel.this_week_button.click()
    panel.next_button.click()
    grid.setFocus()
    QTest.keyClick(grid, Qt.Key.Key_PageDown)
    assert months == [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1), date(2026, 11, 1)]


async def test_the_window_reads_the_days_of_the_view_and_keeps_it(qtbot: QtBot) -> None:
    window = await shown_window(qtbot, CalendarView.WEEK)
    backend = window.backend
    assert isinstance(backend, FakeBackend)
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == (MONDAY, MONDAY + timedelta(days=6))
    window.calendar_panel.views.setCurrentIndex(1)  # Month.
    await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == month_days(TODAY)
    assert window.appearance.calendar_view is CalendarView.MONTH
    window.calendar_panel.next_button.click()
    await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == month_days(date(2026, 11, 1))
    # Switching back opens the week of the day in focus: the 1st of November.
    window.calendar_panel.views.setCurrentIndex(0)
    await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    first = date(2026, 10, 26)
    assert reads[-1][1:] == (first, first + timedelta(days=6))


def test_the_view_is_kept_and_an_unknown_one_opens_the_week() -> None:
    assert Appearance().calendar_view is CalendarView.WEEK
    kept = Appearance.model_validate_json('{"calendar_view": "month", "mode": "dark"}')
    assert kept.calendar_view is CalendarView.MONTH
    # A view a newer build added gives the default, keeping the other choices.
    newer = Appearance.model_validate_json('{"calendar_view": "year", "mode": "dark"}')
    assert newer.calendar_view is CalendarView.WEEK and newer.mode.value == "dark"
