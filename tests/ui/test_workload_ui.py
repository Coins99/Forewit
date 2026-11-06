"""P3-3 on the desktop (ADR 0030): each day's load in its head, blocks in conflict marked,
the line under the hours saying why, and Today's free time."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter, ActionStatus
from mailbrief.domain.day_plans import DayPlan, DayPlanItem
from mailbrief.domain.events import (
    CalendarEvent,
    CalendarRange,
    FocusDay,
    FocusTask,
    ScheduledBlock,
)
from mailbrief.domain.google_calendar import GoogleCoverage
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.domain.work_blocks import BlockSaved, WorkBlock
from mailbrief.domain.working_hours import SUGGESTED_HOURS, WorkingHours
from mailbrief.domain.workload import block_conflicts, workload
from mailbrief.ui.calendar_layout import CalendarView
from mailbrief.ui.calendar_view import GOOGLE_ON, CalendarPanel
from mailbrief.ui.event_editor import local_instant
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow
from mailbrief.ui.main_window import MainWindow, block_status
from mailbrief.ui.upcoming_view import UpcomingDialog
from mailbrief.ui.workload_text import (
    block_reasons,
    day_reasons,
    header_text,
    short_length,
    today_text,
)
from tests.factories import make_action
from tests.ui.test_workflow import FakeBackend

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
TODAY = date(2026, 10, 14)  # A Wednesday.
THURSDAY, FRIDAY, SATURDAY = (TODAY + timedelta(days=n) for n in (1, 2, 3))
NOW = datetime(2026, 10, 14, 14, 20, tzinfo=TORONTO)
HOURS = WorkingHours(days=SUGGESTED_HOURS.days, buffer_minutes=0, revision=1)
ID = "00000000-0000-4000-8000-0000000001{:02d}"
TASK, LONG, LOOSE = ID.format(1), ID.format(2), ID.format(3)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return local_instant(day, time(hour, minute), TORONTO)


def standup(day: date) -> CalendarEvent:
    return CalendarEvent(
        public_id=ID.format(40),
        title="Standup",
        notes="",
        all_day=False,
        starts_at_utc=at(day, 10, 30),
        ends_at_utc=at(day, 11),
        start_date=None,
        last_date=None,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
    )


def block(day: date, start: int, end: int, number: int) -> ScheduledBlock:
    stored = WorkBlock(
        public_id=ID.format(number),
        action_public_id=TASK,
        starts_at_utc=at(day, start),
        ends_at_utc=at(day, end),
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
    )
    return ScheduledBlock(
        block=stored, action_title="Draft report", action_status=ActionStatus.OPEN
    )


WEDNESDAY_BLOCK = block(TODAY, 9, 15, 60)  # Six hours.
THURSDAY_BLOCK = block(THURSDAY, 10, 11, 61)  # Over the standup.


def week() -> CalendarRange:
    return CalendarRange(
        first_day=MONDAY,
        last_day=MONDAY + timedelta(days=6),
        events=(standup(THURSDAY),),
        markers=(),
        blocks=(WEDNESDAY_BLOCK, THURSDAY_BLOCK),
        hours=HOURS,
        focus=(
            FocusDay(day=TODAY, tasks=(FocusTask(action_public_id=LONG, estimate_minutes=180),)),
            FocusDay(day=FRIDAY, tasks=(FocusTask(action_public_id=LOOSE),)),
        ),
    )


def test_a_day_s_head_says_its_load_and_the_line_explains_it() -> None:
    days = {load.day: load for load in workload(week(), TORONTO, NOW, google_on=False)}
    assert header_text(days[MONDAY]) == ("", False)  # Past: shown, never flagged.
    assert header_text(days[TODAY]) == ("Over by 1h", True)
    assert header_text(days[TODAY], wide=True) == ("Over by 1h · 9h planned of 8h", True)
    assert header_text(days[THURSDAY]) == ("6h 30m spare", False)
    assert header_text(days[FRIDAY]) == ("≤ 8h spare", False)  # A task has no estimate.
    assert header_text(days[SATURDAY]) == ("", False)  # No working time, nothing planned.
    assert day_reasons(days[TODAY], "Wed Oct 14") == (
        "Wed Oct 14: 8 hrs available, 9 hrs planned (6 hrs in blocks, 3 hrs for 1 focus task"
        " without time) · over by 1 hr"
    )
    assert day_reasons(days[FRIDAY], "Fri Oct 16") == (
        "Fri Oct 16: 8 hrs available, 0 mins planned (0 mins for 1 focus task without time)"
        " · at most 8 hrs spare · provisional: 1 focus task has no estimate"
    )
    assert day_reasons(days[THURSDAY], "Thu Oct 15").endswith(" · 1 block in conflict")
    unset = workload(
        week().model_copy(update={"hours": WorkingHours.unset()}), TORONTO, NOW, google_on=False
    )
    assert header_text(unset[2]) == ("", False)
    assert day_reasons(unset[2], "Wed Oct 14").endswith("Set your working hours to see what fits.")
    (conflict,) = block_conflicts(week(), TORONTO, NOW)[THURSDAY_BLOCK.public_id]
    assert (
        block_reasons("Draft report", (conflict,), None) == "Task · Draft report: overlaps Standup"
    )
    # Today: free time from 14:20 (15:00–17:00), and how far the day is over.
    assert today_text(days[TODAY], NOW) == "2 hrs free today · planned 9 hrs, over by 1 hr"
    assert today_text(unset[2], NOW) == ""
    assert [short_length(n) for n in (45, 60, 90)] == ["45m", "1h", "1h 30m"]


def test_google_on_makes_an_unsynced_day_provisional(qtbot: QtBot) -> None:
    days = {load.day: load for load in workload(week(), TORONTO, NOW, google_on=True)}
    assert header_text(days[THURSDAY]) == ("≤ 6h 30m spare", False)
    assert header_text(days[TODAY]) == ("Over by 1h+", True)
    assert "Google Calendar isn't synced for this day" in day_reasons(days[TODAY], "Wed")


@pytest.fixture
def panel(qtbot: QtBot) -> CalendarPanel:
    result = CalendarPanel()
    qtbot.addWidget(result)
    result.set_clock(lambda: NOW)
    result.show_range(week(), TORONTO, TODAY)
    return result


def test_the_week_marks_conflicts_and_explains_the_choice(panel: CalendarPanel) -> None:
    grid = panel.week_grid
    assert grid.load_text(TODAY) == "Over by 1h" and grid.load_text(MONDAY) == ""
    assert grid.conflicted() == frozenset({THURSDAY_BLOCK.public_id})
    assert panel.day_grid.conflicted() == grid.conflicted()
    notes = [
        item.data(DISPLAY_ROW_ROLE).preview
        for item in (panel.list.item(row) for row in range(panel.list.count()))
        if isinstance(item.data(DISPLAY_ROW_ROLE), DisplayRow)
        and item.data(DISPLAY_ROW_ROLE).title == "Draft report"
    ]
    assert notes == ["Task", "Task · conflict"]  # Wednesday's, then Thursday's.
    assert panel.reasons_text() == ""  # Nothing chosen yet.
    panel.select_entry(("block", THURSDAY_BLOCK.public_id), THURSDAY)
    assert panel.reasons_text() == "Task · Draft report: overlaps Standup"
    grid.chosen.emit(TODAY, None)
    assert panel.reasons_text().startswith("Wed Oct 14: 8 hrs available, 9 hrs planned")
    grid.chosen.emit(SATURDAY, None)
    assert panel.reasons_text() == ""  # No working time and nothing planned.
    panel.set_google(GOOGLE_ON, "")
    assert grid.load_text(THURSDAY) == "≤ 6h 30m spare"
    grid.chosen.emit(TODAY, None)
    panel.set_view(CalendarView.MONTH)
    assert panel.reasons_text() == ""  # Day and Week only.


def test_a_save_in_a_buffer_says_so() -> None:
    saved = BlockSaved(block=WEDNESDAY_BLOCK.block, buffered=("Standup",))
    assert block_status("Scheduled", "Draft", saved) == (
        "Scheduled: Draft. Within the buffer of Standup."
    )


def test_upcoming_points_to_the_calendar(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    dialog = UpcomingDialog(host)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("Free time and workload are on the Calendar page." in text for text in texts)
    assert not any("does not estimate" in text for text in texts)


async def test_today_says_how_much_time_is_left(qtbot: QtBot) -> None:
    backend = FakeBackend()
    task = make_action(public_id=LONG, title="Plan the offsite", estimate_minutes=600)
    backend.actions[ActionFilter.OPEN] = (task,)
    backend.hours = HOURS
    backend.focus_plans[TODAY] = DayPlan(
        day=TODAY,
        public_id=ID.format(90),
        revision=1,
        items=(DayPlanItem(public_id=ID.format(91), task_public_id=LONG, position=0),),
    )
    window = MainWindow(backend)
    window.now = lambda: NOW
    qtbot.addWidget(window)
    window.show()
    await window.initialize()
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    window._read_calendar()
    await window.drain_calendar_reads()
    # Google Calendar is on and its cache misses today: the figures may be wrong.
    assert window.focus_panel.capacity.text() == (
        "At most 2 hrs 40 mins free today · planned 10 hrs, over by at least 2 hrs"
    )
    backend.google_coverage = GoogleCoverage(
        first_day=MONDAY, last_day=MONDAY + timedelta(days=27), synced_at_utc=NOW
    )
    window._read_calendar()
    await window.drain_calendar_reads()
    assert window.focus_panel.capacity.text() == (
        "2 hrs 40 mins free today · planned 10 hrs, over by 2 hrs"
    )
    backend.hours = WorkingHours.unset()
    window._read_calendar()
    await window.drain_calendar_reads()
    assert window.focus_panel.capacity.isHidden()
