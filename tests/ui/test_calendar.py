"""The Calendar page and event editor: a week's rows, navigation, saving and undo."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialogButtonBox, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.events import (
    ActionMarker,
    CalendarEvent,
    CalendarRange,
    EventEdit,
    MarkerKind,
)
from mailbrief.ui.calendar_view import (
    DELETE,
    EMPTY,
    NEW,
    OPEN,
    CalendarPanel,
    day_heading,
    event_when,
    marker_text,
    week_start,
    week_title,
)
from mailbrief.ui.event_editor import (
    ENDS_FIRST,
    NO_TITLE,
    TOO_LONG,
    EventEditor,
    local_instant,
    suggested_start,
)
from mailbrief.ui.main_window import MainWindow
from tests.ui.test_workflow import FakeBackend

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
TODAY = date(2026, 10, 14)  # A Wednesday.
NOW = datetime(2026, 10, 14, 14, 20, tzinfo=TORONTO)
ACTION_ID = "5b1f3c2e-8d4a-4e6b-9c7d-1a2b3c4d5e6f"


def stored(edit: EventEdit, number: int = 1, revision: int = 1) -> CalendarEvent:
    return CalendarEvent(
        public_id=f"00000000-0000-4000-8000-{number:012d}",
        revision=revision,
        created_at_utc=datetime(2026, 10, 11, 12, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 11, 12, tzinfo=UTC),
        **edit.model_dump(),
    )


def timed(day: date, start: int, end: int, title: str = "Dentist") -> EventEdit:
    return EventEdit(
        title=title,
        all_day=False,
        starts_at_utc=local_instant(day, time(start), TORONTO),
        ends_at_utc=local_instant(day, time(end), TORONTO),
        time_zone="America/Toronto",
    )


def whole_days(first: date, last: date, title: str = "Conference") -> EventEdit:
    return EventEdit(
        title=title, all_day=True, start_date=first, last_date=last, time_zone="America/Toronto"
    )


def marker(kind: MarkerKind, day: date, **extra: object) -> ActionMarker:
    return ActionMarker.model_validate(
        {"kind": kind, "day": day, "action_public_id": ACTION_ID, "action_title": "Pay the invoice"}
        | extra
    )


def week(*events: CalendarEvent, markers: tuple[ActionMarker, ...] = ()) -> CalendarRange:
    return CalendarRange(
        first_day=MONDAY,
        last_day=MONDAY + timedelta(days=6),
        events=tuple(sorted(events, key=CalendarEvent.sort_key)),
        markers=markers,
    )


def rows(panel: CalendarPanel) -> list[str]:
    return [panel.list.item(number).text() for number in range(panel.list.count())]


@pytest.fixture
def panel(qtbot: QtBot) -> CalendarPanel:
    result = CalendarPanel()
    qtbot.addWidget(result)
    return result


def test_text_helpers() -> None:
    assert week_start(TODAY) == MONDAY and week_start(MONDAY) == MONDAY
    assert week_title(MONDAY, TODAY) == "Oct 12 – 18"
    assert week_title(date(2026, 9, 28), TODAY) == "Sep 28 – Oct 4"
    assert week_title(date(2026, 12, 28), TODAY) == "Dec 28 – Jan 3, 2027"
    assert day_heading(TODAY, TODAY) == "Wed Oct 14 · Today"
    assert day_heading(MONDAY, TODAY) == "Mon Oct 12"
    overnight = stored(
        EventEdit(
            title="Flight",
            all_day=False,
            starts_at_utc=local_instant(MONDAY, time(22), TORONTO),
            ends_at_utc=local_instant(MONDAY + timedelta(days=1), time(1), TORONTO),
            time_zone="America/Toronto",
        )
    )
    assert event_when(overnight, MONDAY, TORONTO, TODAY) == "22:00–Oct 13 01:00"
    assert event_when(overnight, MONDAY + timedelta(days=1), TORONTO, TODAY) == "Oct 12 22:00–01:00"
    assert event_when(stored(whole_days(MONDAY, MONDAY)), MONDAY, TORONTO, TODAY) == "All day"
    assert marker_text(marker(MarkerKind.TARGET, TODAY), TORONTO) == "Target · Pay the invoice"
    assert marker_text(marker(MarkerKind.DEADLINE, TODAY), TORONTO) == "Deadline · Pay the invoice"
    due = marker(
        MarkerKind.DEADLINE,
        TODAY,
        due_at_utc=datetime(2026, 10, 14, 21, 0, tzinfo=UTC),
        waiting=True,
    )
    assert marker_text(due, TORONTO) == "Deadline 17:00 · Pay the invoice · waiting for others"


def test_local_instant_follows_dst_like_a_clock() -> None:
    # 02:30 never happens on 14 March 2027 in Toronto: it reads as 03:30 EDT (07:30 UTC).
    skipped = local_instant(date(2027, 3, 14), time(2, 30), TORONTO)
    assert skipped.astimezone(UTC) == datetime(2027, 3, 14, 7, 30, tzinfo=UTC)
    # 01:30 happens twice on 1 November 2026: the first, still EDT (05:30 UTC).
    repeated = local_instant(date(2026, 11, 1), time(1, 30), TORONTO)
    assert repeated.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_mail_derived_titles_stay_plain_text(panel: CalendarPanel) -> None:
    """Action titles come from email; rows never render them as rich text or tooltips."""
    markup = '<b>Pay</b> <a href="https://evil.example">now</a>'
    event = stored(timed(TODAY, 9, 10, "<i>Dentist</i>"))
    panel.show_range(
        week(event, markers=(marker(MarkerKind.TARGET, TODAY, action_title=markup),)),
        TORONTO,
        TODAY,
    )
    texts = rows(panel)
    assert f"Target · {markup}" in texts and "09:00–10:00 · <i>Dentist</i>" in texts
    assert all(panel.list.item(n).toolTip() == "" for n in range(panel.list.count()))


def test_buttons_have_their_own_mnemonics(panel: CalendarPanel) -> None:
    """Letters no window-level control uses: S, C, D, L, R, U, T and M are taken."""
    texts = [
        button.text()
        for button in (
            panel.previous_button,
            panel.this_week_button,
            panel.next_button,
            panel.new_button,
            panel.open_button,
            panel.delete_button,
        )
    ]
    letters = [text[text.index("&") + 1].casefold() for text in texts]
    assert len(set(letters)) == len(letters)
    assert not set(letters) & set("scdlrutm")


def test_week_rows_order_each_day(panel: CalendarPanel) -> None:
    standup = stored(timed(TODAY, 9, 10, "Standup"), 1)
    lunch = stored(timed(TODAY, 12, 13, "Lunch"), 2)
    trip = stored(whole_days(TODAY, TODAY + timedelta(days=1), "Trip"), 3)
    panel.show_range(
        week(lunch, standup, trip, markers=(marker(MarkerKind.DEADLINE, TODAY),)), TORONTO, TODAY
    )
    assert panel.heading.text() == "Calendar · Oct 12 – 18"
    assert rows(panel) == [
        "Mon Oct 12",
        "Tue Oct 13",
        "Wed Oct 14 · Today",
        "All day · Trip",
        "Deadline · Pay the invoice",
        "09:00–10:00 · Standup",
        "12:00–13:00 · Lunch",
        "Thu Oct 15",
        "All day · Trip",
        "Fri Oct 16",
        "Sat Oct 17",
        "Sun Oct 18",
    ]
    assert panel.empty.isHidden() and not panel.note.isHidden()
    # Day headings are read but never selected.
    assert not panel.list.item(0).flags() & Qt.ItemFlag.ItemIsSelectable


def test_empty_week_says_how_to_add(panel: CalendarPanel) -> None:
    assert not panel.new_button.isEnabled()  # Nothing is shown yet.
    panel.show_range(week(), TORONTO, TODAY)
    assert panel.empty.text() == EMPTY and not panel.empty.isHidden()
    assert panel.note.isHidden()
    assert len(rows(panel)) == 7
    assert panel.new_button.isEnabled() and not panel.open_button.isEnabled()


def test_buttons_ask_the_window(panel: CalendarPanel, qtbot: QtBot) -> None:
    event = stored(timed(TODAY, 9, 10))
    panel.show_range(week(event), TORONTO, TODAY)
    asked: list[tuple[str, object]] = []
    weeks: list[object] = []
    panel.event_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.week_requested.connect(weeks.append)

    panel.new_button.click()  # No selection: today, since it is in the week.
    panel.list.setCurrentRow(1)  # Tuesday's heading.
    panel.new_button.click()
    panel.list.setCurrentRow(3)  # The event.
    assert panel.selected() == event
    panel.open_button.click()
    panel.delete_button.click()
    panel.list.itemActivated.emit(panel.list.item(3))
    assert asked == [
        (NEW, TODAY),
        (NEW, MONDAY + timedelta(days=1)),
        (OPEN, event),
        (DELETE, event),
        (OPEN, event),
    ]

    panel.previous_button.click()
    panel.next_button.click()
    panel.this_week_button.click()
    assert weeks == [MONDAY - timedelta(days=7), MONDAY + timedelta(days=7), MONDAY]

    panel.set_busy(True)
    assert not any(
        button.isEnabled()
        for button in (panel.new_button, panel.open_button, panel.previous_button)
    )
    panel.new_button.click()
    assert len(asked) == 5


def test_refresh_keeps_the_selected_event(panel: CalendarPanel) -> None:
    first, second = stored(timed(TODAY, 9, 10), 1), stored(timed(TODAY, 11, 12, "Call"), 2)
    panel.show_range(week(first, second), TORONTO, TODAY)
    panel.list.setCurrentRow(4)
    assert panel.selected() == second
    moved = stored(timed(TODAY, 8, 9, "Call"), 2, revision=2)
    panel.show_range(week(first, moved), TORONTO, TODAY)
    assert panel.selected() == moved


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[EventEditor]:
    parent = QWidget()  # Kept alive here: the dialog goes with its parent.
    qtbot.addWidget(parent)
    yield EventEditor(parent)


def test_new_event_starts_at_the_next_hour_today(editor: EventEditor) -> None:
    assert suggested_start(TODAY, NOW, TORONTO) == local_instant(TODAY, time(15), TORONTO)
    assert suggested_start(MONDAY, NOW, TORONTO) == local_instant(MONDAY, time(9), TORONTO)
    late = datetime(2026, 10, 14, 23, 30, tzinfo=TORONTO)
    assert suggested_start(TODAY, late, TORONTO) == local_instant(TODAY, time(9), TORONTO)
    editor.edit_new(TODAY, NOW, TORONTO)
    assert editor.editing() is None and editor.windowTitle() == "New event"
    assert editor.start_time.time() == QTime(15, 0) and editor.end_time.time() == QTime(16, 0)
    assert editor.built_edit() == NO_TITLE


def test_editor_checks_before_asking(editor: EventEditor) -> None:
    editor.edit_new(TODAY, NOW, TORONTO)
    editor.title.setText("  Dentist  ")
    edit = editor.built_edit()
    assert isinstance(edit, EventEdit)
    assert edit.title == "Dentist" and edit.time_zone == "America/Toronto"
    assert edit.starts_at_utc == local_instant(TODAY, time(15), TORONTO)

    editor.end_time.setTime(QTime(14, 0))
    assert editor.built_edit() == ENDS_FIRST
    editor.end_date.setDate(QDate(2026, 11, 20))
    assert editor.built_edit() == TOO_LONG

    editor.all_day.setChecked(True)
    assert editor.start_time.isHidden() and editor.end_time.isHidden()
    assert editor.built_edit() == TOO_LONG
    editor.end_date.setDate(QDate(2026, 10, 13))
    assert editor.built_edit() == ENDS_FIRST
    editor.end_date.setDate(QDate(2026, 10, 15))
    edit = editor.built_edit()
    assert isinstance(edit, EventEdit)
    assert edit.all_day and (edit.start_date, edit.last_date) == (TODAY, date(2026, 10, 15))


def test_save_locks_until_the_window_answers(editor: EventEditor) -> None:
    event = stored(whole_days(TODAY, TODAY))
    editor.edit_event(event, TORONTO)
    assert editor.editing() == event and editor.all_day.isChecked()
    asked: list[object] = []
    editor.save_requested.connect(asked.append)
    save = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None
    editor.open()
    save.click()
    assert asked == [event.edit()]
    assert not save.isEnabled() and not editor._fields.isEnabled()
    editor.reject()  # Escape waits for the answer.
    assert editor.isVisible()
    editor.keep_open("Couldn't save.")
    assert save.isEnabled() and editor.status.text() == "Couldn't save."
    editor.finish_saved()
    assert not editor.isVisible()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    return result


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


def calls(backend: FakeBackend, name: str) -> list[tuple[object, ...]]:
    return [call for call in backend.event_calls if call[0] == name]


async def test_calendar_page_loads_this_week_and_has_a_shortcut(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    first = week_start(window._local_today())
    assert calls(backend, "calendar_range")[-1] == (
        "calendar_range",
        first,
        first + timedelta(days=6),
    )
    assert window.page_shortcuts["calendar"].key() == QKeySequence("Ctrl+6")
    window.page_shortcuts["calendar"].activated.emit()
    assert window.workspace.current_page() == "calendar"

    window.calendar_panel.next_button.click()
    await finish(window)
    later = first + timedelta(days=7)
    assert calls(backend, "calendar_range")[-1] == (
        "calendar_range",
        later,
        later + timedelta(days=6),
    )
    assert window.calendar_panel.first_day == later


async def test_new_event_is_saved_and_shown(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    window.calendar_panel.new_button.click()
    assert window.event_editor.isVisible()
    window.event_editor.title.setText("Dentist")
    save = window.event_editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None
    save.click()
    await finish(window)
    assert not window.event_editor.isVisible()
    assert [call[0] for call in backend.event_calls].count("create_event") == 1
    assert window.status.text() == "Saved: Dentist."
    assert any(text.endswith("· Dentist") for text in rows(window.calendar_panel))


async def test_stale_save_keeps_the_editor_open(window: MainWindow, backend: FakeBackend) -> None:
    event = stored(timed(window._local_today(), 9, 10))
    backend.events[event.public_id] = event.model_copy(update={"revision": 2})
    await window.initialize()
    window.event_editor.edit_event(event, window.zone)
    window.event_editor.open()
    window.event_editor.title.setText("Dentist, moved")
    save = window.event_editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None
    save.click()
    await finish(window)
    assert window.event_editor.isVisible()
    assert "changed since you opened it" in window.event_editor.status.text()
    assert window.event_editor.title.text() == "Dentist, moved"
    kept = backend.events[event.public_id]
    assert (kept.title, kept.revision) == ("Dentist", 2)  # The newer event is untouched.
    assert not calls(backend, "create_event")
    assert [call[:3] for call in calls(backend, "save_event")] == [
        ("save_event", event.public_id, 1)
    ]


async def test_delete_offers_undo(window: MainWindow, backend: FakeBackend) -> None:
    event = stored(timed(window._local_today(), 9, 10))
    backend.events[event.public_id] = event
    await window.initialize()
    panel = window.calendar_panel
    row = next(n for n, text in enumerate(rows(panel)) if text.endswith("· Dentist"))
    panel.list.setCurrentRow(row)
    panel.delete_button.click()
    await finish(window)
    assert calls(backend, "delete_event") == [("delete_event", event.public_id, 1)]
    assert window.status.text() == "Deleted: Dentist."
    assert window.undo_button.text() == "&Undo delete"
    assert not any(text.endswith("· Dentist") for text in rows(panel))

    window.undo_button.click()
    await finish(window)
    assert calls(backend, "restore_event") == [("restore_event", event.public_id)]
    assert any(text.endswith("· Dentist") for text in rows(panel))


async def test_a_new_day_moves_today_and_the_default_week(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    first = week_start(window._local_today())
    window.now = lambda: NOW + timedelta(days=7)
    await window._refresh_for_new_day()
    later = first + timedelta(days=7)
    assert calls(backend, "calendar_range")[-1] == (
        "calendar_range",
        later,
        later + timedelta(days=6),
    )
    assert window.calendar_panel.first_day == later


async def test_unreadable_calendar_keeps_the_page(window: MainWindow, backend: FakeBackend) -> None:
    backend.event_fail = RuntimeError("private")
    await window.initialize()
    assert "could not be refreshed" in window.status.text()
    assert "private" not in window.status.text()
