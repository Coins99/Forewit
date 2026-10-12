"""The event editor's times: lists in 15-minute steps, ends that say how long, and every way
an end could land at or before the start refused (ADR 0018)."""

from collections.abc import Callable, Iterator
from datetime import date, time, timedelta

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.events import EventEdit
from mailbrief.ui.event_editor import (
    BAD_TIME,
    ENDS_FIRST,
    TOO_LONG,
    EventEditor,
    TimeBox,
    day_times,
    length_text,
    local_instant,
    parse_clock,
)
from tests.ui.test_calendar import NOW, TODAY, TORONTO


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[EventEditor]:
    parent = QWidget()  # Kept alive here: the dialog goes with its parent.
    qtbot.addWidget(parent)
    result = EventEditor(parent)
    result.edit_new(TODAY, NOW, TORONTO)  # 15:00 to 16:00 on Wednesday, Oct 14.
    result.title.setText("Dentist")
    yield result


def type_time(box: TimeBox, text: str) -> None:
    """Type into a time box and leave it, as the owner would."""
    box.setEditText(text)
    line = box.lineEdit()
    assert line is not None
    line.editingFinished.emit()


def choose(box: TimeBox, start: str) -> None:
    """Pick the listed time whose text starts with ``start``."""
    index = next(i for i in range(box.count()) if box.itemText(i).startswith(start))
    box.setCurrentIndex(index)
    box.activated.emit(index)


def saves(editor: EventEditor) -> list[EventEdit]:
    """Every edit the editor asks to save from now on."""
    asked: list[EventEdit] = []
    editor.save_requested.connect(asked.append)
    return asked


def press_save(editor: EventEditor) -> None:
    button = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert button is not None
    button.click()


def items(box: TimeBox) -> list[str]:
    return [box.itemText(index) for index in range(box.count())]


def shown(editor: EventEditor) -> tuple[str, str, str, str]:
    return (
        editor.start_date.date().toString("yyyy-MM-dd"),
        editor.start_time.currentText(),
        editor.end_date.date().toString("yyyy-MM-dd"),
        editor.end_time.currentText(),
    )


def test_times_come_in_quarter_hours_and_ends_say_how_long(editor: EventEditor) -> None:
    starts = items(editor.start_time)
    assert len(starts) == 96 and (starts[0], starts[-1]) == ("00:00", "23:45")
    assert editor.start_time.currentIndex() == starts.index("15:00")  # The list opens there.
    ends = items(editor.end_time)
    assert len(ends) == 96
    assert (ends[0], ends[3], ends[-1]) == ("15:15 (15 mins)", "16:00 (1 hr)", "15:00 (24 hrs)")
    assert "00:00 (9 hrs)" in ends  # Past midnight: the next day.
    assert editor.end_time.currentText() == "16:00"  # The field shows the time alone.
    lengths = [length_text(timedelta(minutes=m)) for m in (15, 60, 135, 1440)]
    assert lengths == ["15 mins", "1 hr", "2 hrs 15 mins", "24 hrs"]


def test_an_end_at_or_before_the_start_is_refused(editor: EventEditor) -> None:
    type_time(editor.start_time, "13:00")  # 1 pm.
    assert shown(editor) == ("2026-10-14", "13:00", "2026-10-14", "14:00")
    assert items(editor.end_time)[0] == "13:15 (15 mins)"  # Nothing before the start listed.
    for early in ("12:00", "13:00"):  # Noon, and the start itself.
        type_time(editor.end_time, early)
        assert shown(editor)[3] == "14:00" and editor.status.text() == ENDS_FIRST
    type_time(editor.end_time, "13:30")
    assert shown(editor)[3] == "13:30" and editor.status.text() == ""
    assert not isinstance(editor.built_edit(), str)


def test_moving_the_start_keeps_the_length(editor: EventEditor) -> None:
    choose(editor.end_time, "16:30")  # An hour and a half.
    type_time(editor.start_time, "23:00")
    assert shown(editor) == ("2026-10-14", "23:00", "2026-10-15", "00:30")
    editor.start_date.setDate(QDate(2026, 10, 20))
    assert shown(editor) == ("2026-10-20", "23:00", "2026-10-21", "00:30")
    assert editor.end_date.minimumDate() == QDate(2026, 10, 20)


def test_overnight_ends_come_from_the_list_or_the_end_date(editor: EventEditor) -> None:
    type_time(editor.start_time, "22:00")
    choose(editor.end_time, "01:00")  # "01:00 (3 hrs)": the next day.
    assert shown(editor) == ("2026-10-14", "22:00", "2026-10-15", "01:00")

    type_time(editor.start_time, "23:00")  # Two hours later, still three hours long.
    assert shown(editor) == ("2026-10-14", "23:00", "2026-10-15", "02:00")
    choose(editor.end_time, "00:00")  # Midnight is the next day's start.
    assert shown(editor) == ("2026-10-14", "23:00", "2026-10-15", "00:00")

    editor.end_date.setDate(QDate(2026, 10, 16))  # Moving the end day keeps its time.
    type_time(editor.end_time, "01:00")
    assert shown(editor) == ("2026-10-14", "23:00", "2026-10-16", "01:00")
    assert editor.status.text() == ""


def test_the_end_date_stays_between_the_start_and_31_days(editor: EventEditor) -> None:
    assert editor.end_date.minimumDate() == QDate(2026, 10, 14)
    assert editor.end_date.maximumDate() == QDate(2026, 11, 14)
    type_time(editor.start_time, "13:00")
    choose(editor.end_time, "12:00")  # Noon the next day: 23 hours.
    assert shown(editor) == ("2026-10-14", "13:00", "2026-10-15", "12:00")
    editor.end_date.setDate(QDate(2026, 10, 14))  # Noon on the start's day: before it.
    assert shown(editor)[2] == "2026-10-15" and editor.status.text() == ENDS_FIRST
    editor.end_date.setDate(QDate(2026, 11, 14))  # Exactly 31 days, across the clock change.
    assert shown(editor)[2:] == ("2026-11-14", "12:00") and editor.status.text() == ""
    type_time(editor.end_time, "14:00")  # Two hours too long.
    assert shown(editor)[3] == "12:00" and editor.status.text() == TOO_LONG


def test_all_day_events_keep_their_days(editor: EventEditor) -> None:
    editor.all_day.setChecked(True)
    assert editor.end_date.maximumDate() == QDate(2026, 11, 13)
    editor.end_date.setDate(QDate(2026, 10, 16))  # Three days.
    editor.start_date.setDate(QDate(2026, 10, 20))
    assert (editor.start_date.date(), editor.end_date.date()) == (
        QDate(2026, 10, 20),
        QDate(2026, 10, 22),
    )
    assert editor.end_date.minimumDate() == QDate(2026, 10, 20)

    editor.all_day.setChecked(False)
    type_time(editor.start_time, "13:00")
    choose(editor.end_time, "12:00")
    editor.all_day.setChecked(True)
    editor.end_date.setDate(QDate(2026, 10, 20))  # One day, with noon before 13:00.
    editor.all_day.setChecked(False)  # Back to times: the end is put after the start.
    assert shown(editor) == ("2026-10-20", "13:00", "2026-10-20", "14:00")


def test_typing_that_isnt_a_time_is_refused(editor: EventEditor) -> None:
    for bad in ("25:00", "12:60", "noon", ""):
        type_time(editor.start_time, bad)
        assert shown(editor)[1] == "15:00" and editor.status.text() == BAD_TIME
    type_time(editor.end_time, "4pm")
    assert shown(editor)[3] == "16:00" and editor.status.text() == BAD_TIME
    type_time(editor.start_time, "9:30")  # One digit for the hour is fine.
    assert shown(editor) == ("2026-10-14", "09:30", "2026-10-14", "10:30")
    type_time(editor.end_time, "10:07")  # Any minute, not only the listed ones.
    assert shown(editor)[3] == "10:07" and editor.status.text() == ""
    assert parse_clock(" 23:59 ") == time(23, 59) and parse_clock("24:00") is None


def test_clock_changes(editor: EventEditor) -> None:
    spring = day_times(date(2027, 3, 14), TORONTO)  # 02:00 to 02:59 never happens.
    assert len(spring) == 92 and time(2, 0) not in spring and time(3, 0) in spring
    editor.edit_new(date(2026, 11, 1), NOW, TORONTO)  # 01:00 to 01:59 happens twice.
    type_time(editor.start_time, "00:30")
    ends = items(editor.end_time)
    assert "01:45 (1 hr 15 mins)" in ends and "02:00 (2 hrs 30 mins)" in ends


def test_a_save_that_comes_with_a_refusal_waits(editor: EventEditor) -> None:
    asked = saves(editor)
    type_time(editor.end_time, "14:00")  # Before the 15:00 start: refused, back to 16:00.
    press_save(editor)  # The click that took focus from the field.
    assert asked == [] and editor.status.text() == ENDS_FIRST
    press_save(editor)  # The owner has seen why: what the fields show is saved.
    assert len(asked) == 1
    assert asked[0].ends_at_utc == local_instant(TODAY, time(16), TORONTO)


def test_return_after_a_refused_time_keeps_the_dialog_open(
    editor: EventEditor, qtbot: QtBot
) -> None:
    asked = saves(editor)
    editor.show()  # Return reaches Save, the dialog's default button, only when shown.
    qtbot.waitExposed(editor)
    for box, typed, reason in (
        (editor.end_time, "14:00", ENDS_FIRST),
        (editor.start_time, "25:00", BAD_TIME),
    ):
        box.setEditText(typed)
        QTest.keyClick(box, Qt.Key.Key_Return)
        assert asked == [] and editor.status.text() == reason
        assert shown(editor) == ("2026-10-14", "15:00", "2026-10-14", "16:00")
    QTest.keyClick(editor.end_time, Qt.Key.Key_Return)  # Nothing new typed: Return saves.
    assert len(asked) == 1


def test_moving_on_after_a_refusal_lets_save_go_ahead(editor: EventEditor, qtbot: QtBot) -> None:
    asked = saves(editor)
    editor.show()
    qtbot.waitExposed(editor)
    moves: tuple[Callable[[], object], ...] = (
        lambda: QTest.keyClicks(editor.title, " check-up"),
        lambda: QTest.keyClicks(editor.notes, "Bring the form."),
        lambda: type_time(editor.end_time, "16:30"),
        lambda: editor.all_day.setChecked(True),
    )
    for number, move_on in enumerate(moves, start=1):
        type_time(editor.end_time, "14:00")
        move_on()
        press_save(editor)
        assert len(asked) == number
        editor.keep_open("")  # As the window does when a save can't happen.


def test_a_typed_time_counts_as_unsaved_before_it_is_taken(editor: EventEditor) -> None:
    editor.edit_new(TODAY, NOW, TORONTO)
    assert not editor.has_unsaved_input()
    editor.end_time.setEditText("17:30")  # Typed, still in the field.
    assert editor.end_time.time().toString("HH:mm") == "16:00"
    assert editor.has_unsaved_input()  # A quit would ask first.
    type_time(editor.end_time, "16:00")  # Back to what was loaded.
    assert not editor.has_unsaved_input()


def test_save_takes_a_time_still_being_typed(editor: EventEditor) -> None:
    asked = saves(editor)
    editor.end_time.setEditText("14:00")  # Still in the field; Save comes without leaving it.
    press_save(editor)
    assert asked == [] and editor.status.text() == ENDS_FIRST
    editor.end_time.setEditText("17:30")
    press_save(editor)
    assert len(asked) == 1
    assert asked[0].ends_at_utc == local_instant(TODAY, time(17, 30), TORONTO)
