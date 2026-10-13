"""The Today page's "Your day": what needs attention, today's agenda and new replies, and
opening each one on its own page."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import Action, ActionFilter, ThreadActivity
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision
from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange, MarkerKind
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.domain.today import AttentionItem, AttentionReason, DaySummary
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from mailbrief.ui.today_view import (
    NO_ATTENTION,
    NO_EVENTS,
    NO_REPLIES,
    VISIBLE_ROWS,
    DayPanel,
    DayRow,
    agenda_row,
    attention_row,
    reply_row,
)
from tests.factories import make_action
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend, finish

ZONE = ZoneInfo("America/Toronto")
TODAY = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 13, tzinfo=UTC)  # 09:00 in Toronto.


def task(key: int, title: str, **fields: object) -> Action:
    return make_action(public_id=f"00000000-0000-4000-8000-{key:012d}", title=title, **fields)


def event(key: int, title: str, start_hour: int, hours: int = 1) -> CalendarEvent:
    start = datetime(2026, 10, 13, start_hour, tzinfo=ZONE).astimezone(UTC)
    return CalendarEvent(
        public_id=f"00000000-0000-4000-9000-{key:012d}",
        title=title,
        notes="",
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=start + timedelta(hours=hours),
        start_date=None,
        last_date=None,
        time_zone=ZONE.key,
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
    )


GOOGLE = GoogleEvent(
    calendar_id="work@example.com",
    calendar_name="Work",
    event_id="standup",
    title="Stand-up",
    all_day=False,
    starts_at_utc=datetime(2026, 10, 13, 14, tzinfo=UTC),  # 10:00 in Toronto.
    ends_at_utc=datetime(2026, 10, 13, 14, 15, tzinfo=UTC),
)
OVERDUE = task(
    1,
    "Pay the invoice",
    deadline_text="Oct 12",
    deadline_precision=DeadlinePrecision.DATE,
    deadline_date=date(2026, 10, 12),
    deadline_timezone=ZONE.key,
)
DUE = task(
    2,
    "Send the slides",
    deadline_text="today at 5",
    deadline_precision=DeadlinePrecision.DATETIME,
    deadline_date=TODAY,
    deadline_at_utc=datetime(2026, 10, 13, 21, tzinfo=UTC),
    deadline_timezone=ZONE.key,
)
PLANNED = task(3, "Book the room", target_date=TODAY)
SLIPPED = task(
    4, "Hear back from Sam", target_date=date(2026, 10, 9), ownership=ActionOwnership.WAITING_FOR
)
REPLIED = task(
    5,
    "Finance review prep",
    thread=ThreadActivity(
        new_messages=2, latest_at_utc=NOW - timedelta(hours=1), latest_sender="Priya Shah"
    ),
)
MARKER = ActionMarker(
    day=TODAY,
    kind=MarkerKind.TARGET,
    action_public_id=PLANNED.public_id,
    action_title=PLANNED.title,
    waiting=False,
)


def summary(**fields: object) -> DaySummary:
    values: dict[str, object] = {
        "day": TODAY,
        "attention": (
            AttentionItem(action=OVERDUE, reason=AttentionReason.OVERDUE),
            AttentionItem(action=DUE, reason=AttentionReason.DUE_TODAY),
            AttentionItem(action=PLANNED, reason=AttentionReason.PLANNED_TODAY),
            AttentionItem(action=SLIPPED, reason=AttentionReason.PLANNED_EARLIER),
        ),
        "replies": (REPLIED,),
        "calendar": CalendarRange(
            first_day=TODAY,
            last_day=TODAY,
            events=(event(1, "Dentist", 15),),
            markers=(MARKER,),
            google_events=(GOOGLE,),
        ),
    }
    values.update(fields)
    return DaySummary.model_validate(values)


def rows(panel: DayPanel, card: str) -> list[str]:
    widget = getattr(panel, card).list
    return [widget.item(number).text() for number in range(widget.count())]


def test_each_reason_reads_as_a_title_and_a_short_note() -> None:
    rows = [attention_row(item, ZONE, TODAY) for item in summary().attention]
    assert rows == [
        DayRow("Pay the invoice", "Overdue · Mon Oct 12", warning=True),
        DayRow("Send the slides", "Due 17:00", warning=True),
        DayRow("Book the room", "Today"),
        DayRow("Hear back from Sam", "Planned Fri Oct 9 · waiting"),
    ]
    assert reply_row(REPLIED) == DayRow("Finance review prep", "2 new · Priya Shah")
    assert reply_row(REPLIED).text() == "Finance review prep, 2 new · Priya Shah"
    undated = AttentionItem(action=PLANNED, reason=AttentionReason.OVERDUE)
    assert attention_row(undated, ZONE, TODAY).note == "Overdue"
    date_only = AttentionItem(action=OVERDUE, reason=AttentionReason.DUE_TODAY)
    assert attention_row(date_only, ZONE, TODAY).note == "Due today"
    assert agenda_row(GOOGLE, ZONE, TODAY) == DayRow("10:00–10:15 · Stand-up", "Work")
    own = agenda_row(event(1, "Dentist", 15), ZONE, TODAY)
    assert own == DayRow("15:00–16:00 · Dentist", "") and own.text() == own.title


def test_the_panel_shows_three_cards_with_counts(qtbot: QtBot) -> None:
    panel = DayPanel()
    qtbot.addWidget(panel)

    panel.show_summary(summary(), ZONE)

    assert len(rows(panel, "attention")) == 4
    assert panel.attention.heading.text() == "Needs attention · 4"
    # The agenda lists events in time order; the actions' dates are in Needs attention.
    assert rows(panel, "agenda") == ["10:00–10:15 · Stand-up, Work", "15:00–16:00 · Dentist"]
    assert rows(panel, "replies") == ["Finance review prep, 2 new · Priya Shah"]
    assert not panel.attention.empty.isVisibleTo(panel)
    # At most VISIBLE_ROWS rows show; the rest scroll.
    shown = panel.attention.list.height()
    row = panel.attention.list.sizeHintForRow(0)
    assert row * (VISIBLE_ROWS - 1) < shown <= row * VISIBLE_ROWS + 4


def test_empty_cards_say_so(qtbot: QtBot) -> None:
    panel = DayPanel()
    qtbot.addWidget(panel)
    panel.show()

    panel.show_summary(
        summary(
            attention=(),
            replies=(),
            calendar=CalendarRange(first_day=TODAY, last_day=TODAY, events=(), markers=()),
        ),
        ZONE,
    )

    for card, text in (("attention", NO_ATTENTION), ("agenda", NO_EVENTS), ("replies", NO_REPLIES)):
        widget = getattr(panel, card)
        assert widget.empty.isVisible() and widget.empty.text() == text
        assert not widget.list.isVisible()
    panel.show_summary(None, ZONE)
    assert panel.summary is None and panel.attention.heading.text() == "Needs attention"


def test_activating_a_row_asks_to_open_its_record(qtbot: QtBot) -> None:
    panel = DayPanel()
    qtbot.addWidget(panel)
    panel.show_summary(summary(), ZONE)
    actions: list[tuple[str, bool]] = []
    events: list[object] = []
    panel.action_requested.connect(lambda key, waiting: actions.append((key, waiting)))
    panel.event_requested.connect(events.append)

    for card, row in (("attention", 0), ("attention", 3), ("agenda", 0), ("agenda", 1)):
        widget = getattr(panel, card).list
        widget.itemActivated.emit(widget.item(row))
    panel.replies.list.itemActivated.emit(panel.replies.list.item(0))

    assert actions == [
        (OVERDUE.public_id, False),
        (SLIPPED.public_id, True),
        (REPLIED.public_id, False),
    ]
    assert events == [
        ("google", "work@example.com", "standup"),
        ("event", "00000000-0000-4000-9000-000000000001"),
    ]


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.actions = {
        ActionFilter.OPEN: (OVERDUE, DUE, PLANNED, REPLIED),
        ActionFilter.WAITING: (SLIPPED,),
    }
    fake.events = {event(1, "Dentist", 15).public_id: event(1, "Dentist", 15)}
    fake.google_events = [GOOGLE]
    return fake


def window_for(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.now = lambda: NOW
    window._apply_owner_preferences(OwnerPreferences(revision=1, time_zone=ZONE.key))
    return window


async def test_the_window_reads_the_day_with_the_actions(
    qtbot: QtBot, backend: FakeBackend
) -> None:
    window = window_for(qtbot, backend)

    await window._refresh_actions()

    assert backend.day_calls[-1] == TODAY
    assert window.day_panel.attention.heading.text() == "Needs attention · 4"
    assert rows(window.day_panel, "replies") == [reply_row(REPLIED).text()]


async def test_a_failed_read_keeps_what_was_shown(qtbot: QtBot, backend: FakeBackend) -> None:
    window = window_for(qtbot, backend)
    await window._refresh_day()
    backend.day_fail = RuntimeError("synthetic")

    await window._refresh_day()

    assert window.day_panel.attention.heading.text() == "Needs attention · 4"
    assert window.status.text()


async def test_opening_an_action_shows_it_on_its_page(qtbot: QtBot, backend: FakeBackend) -> None:
    window = window_for(qtbot, backend)
    await window._refresh_actions()

    window.day_panel.action_requested.emit(SLIPPED.public_id, True)

    assert window.workspace.current_page() == "actions"
    assert window.actions_panel.view() is ActionFilter.WAITING
    selected = window.actions_panel.selected()
    assert selected is not None and selected.public_id == SLIPPED.public_id


async def test_an_action_a_filter_hides_is_shown_after_clearing_it(
    qtbot: QtBot, backend: FakeBackend
) -> None:
    window = window_for(qtbot, backend)
    await window._refresh_actions()
    window.actions_panel.show_actions(
        ActionFilter.OPEN, (DUE,), today=TODAY, zone=ZONE, now=NOW
    )  # As a project filter would leave it.

    window.day_panel.action_requested.emit(OVERDUE.public_id, False)
    await finish(window)

    selected = window.actions_panel.selected()
    assert selected is not None and selected.public_id == OVERDUE.public_id


async def test_opening_an_event_shows_this_week_with_it_selected(
    qtbot: QtBot, backend: FakeBackend
) -> None:
    window = window_for(qtbot, backend)
    window._calendar_week = date(2026, 9, 7)  # Another week shown.

    window.day_panel.event_requested.emit(("google", "work@example.com", "standup"))
    await window.drain_calendar_reads()

    assert window.workspace.current_page() == "calendar"
    shown: date | None = window._calendar_week
    assert shown is None  # This week again.
    assert window.calendar_panel.selected_google() == GOOGLE
    window.day_panel.event_requested.emit("not a key")  # Ignored.
    await window.drain_calendar_reads()


@pytest.mark.parametrize(
    ("mode", "size"),
    [
        (ThemeMode.DARK, (1100, 720)),
        (ThemeMode.LIGHT, (1100, 720)),
        (ThemeMode.DARK, (680, 520)),
    ],
)
async def test_today_gallery(
    qtbot: QtBot,
    qapp: QApplication,
    themed: None,
    backend: FakeBackend,
    mode: ThemeMode,
    size: tuple[int, int],
) -> None:
    apply_theme(qapp, mode=mode)
    window = window_for(qtbot, backend)
    window.start(window.initialize)
    await finish(window)
    window._apply_owner_preferences(OwnerPreferences(revision=1, time_zone=ZONE.key))
    await window._refresh_actions()
    window._show_page("today")
    window.resize(*size)
    window.show()
    qtbot.waitExposed(window)
    assert window.day_panel.isVisible()
    # However narrow, the cards never push the brief off the page.
    assert window.workspace.brief_list.height() > window.day_panel.height()
    save_shot(window.grab(), f"today-day-{size[0]}-{mode}")


async def test_a_finished_run_reads_the_day_again(qtbot: QtBot, backend: FakeBackend) -> None:
    window = window_for(qtbot, backend)
    window.start(window.initialize)
    await finish(window)
    before = len(backend.day_calls)

    window.generate_button.click()
    await asyncio.sleep(0)
    window.review_button.click()
    await asyncio.sleep(0)
    window.approve_button.click()
    await finish(window)

    assert "Brief saved" in window.status.text()
    assert len(backend.day_calls) > before
