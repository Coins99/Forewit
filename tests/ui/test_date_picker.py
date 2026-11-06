"""Every date field's drop-down calendar is the flat DatePicker: plain navigation with
chevrons, weekday names without Qt's red weekends, the chosen day a rounded primary cell
and today ringed in the accent."""

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QDateEdit,
    QDialog,
    QToolButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

from mailbrief.ui.action_editor import ActionEditor
from mailbrief.ui.block_editor import BlockEditor
from mailbrief.ui.cached_view import CachedMailDialog
from mailbrief.ui.date_picker import NEXT, PREVIOUS, DatePicker, calendar_popup
from mailbrief.ui.event_editor import EventEditor
from mailbrief.ui.focus_view import FocusDialog
from mailbrief.ui.theme import ThemeMode, apply_theme, current_tokens


def picker(qtbot: QtBot) -> tuple[QDateEdit, DatePicker]:
    """A date field and its picker; the caller keeps the field, which owns the picker."""
    edit = QDateEdit()
    qtbot.addWidget(edit)
    calendar_popup(edit)
    calendar = edit.calendarWidget()
    assert isinstance(calendar, DatePicker)
    return edit, calendar


def colours(image: QImage) -> set[str]:
    return {
        image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width())
    }


def test_calendar_popup_gives_a_date_field_the_picker(qtbot: QtBot) -> None:
    edit = QDateEdit()
    qtbot.addWidget(edit)
    calendar_popup(edit)
    assert edit.calendarPopup()
    assert isinstance(edit.calendarWidget(), DatePicker)
    assert edit.calendarWidget().objectName() == "datePicker"


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_weekday_names_are_secondary_and_weekends_are_not_red(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    _edit, calendar = picker(qtbot)
    secondary = current_tokens().text_secondary
    assert calendar.headerTextFormat().foreground().color().name() == secondary
    for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        assert calendar.weekdayTextFormat(day).foreground().color().name() == secondary
    assert not calendar.isGridVisible()
    assert calendar.verticalHeaderFormat() is QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader


def test_a_theme_change_recolours_the_picker(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT)
    _edit, calendar = picker(qtbot)
    light = calendar.headerTextFormat().foreground().color().name()
    apply_theme(qapp, ThemeMode.DARK)
    dark = calendar.headerTextFormat().foreground().color().name()
    assert dark == current_tokens().text_secondary
    assert dark != light


def test_the_month_arrows_are_named_chevrons(qtbot: QtBot) -> None:
    _edit, calendar = picker(qtbot)
    for name, accessible in ((PREVIOUS, "Previous month"), (NEXT, "Next month")):
        button = calendar.findChild(QToolButton, name)
        assert button is not None
        assert not button.icon().isNull()
        assert button.accessibleName() == accessible


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_the_chosen_day_is_a_primary_cell_and_today_is_ringed(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    tokens = current_tokens()
    _edit, calendar = picker(qtbot)
    today = QDate.currentDate()
    other = today.addDays(1 if today.day() < 15 else -1)  # Same month, not today.
    calendar.setSelectedDate(other)
    calendar.resize(calendar.sizeHint())
    calendar.show()
    qtbot.waitExposed(calendar)
    painted = colours(calendar.grab().toImage())
    assert tokens.primary_fill in painted
    assert tokens.accent_fg in painted
    calendar.setSelectedDate(today)  # Chosen, today is a cell, not a ring.
    assert tokens.primary_fill in colours(calendar.grab().toImage())


def test_every_date_field_uses_the_picker(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    dialogs: list[QDialog] = [
        ActionEditor(host),
        BlockEditor(host),
        CachedMailDialog(host),
        EventEditor(host),
        FocusDialog(host),
    ]
    fields = [field for dialog in dialogs for field in dialog.findChildren(QDateEdit)]
    assert len(fields) >= 8
    for field in fields:
        assert field.calendarPopup(), field.objectName()
        assert isinstance(field.calendarWidget(), DatePicker), field.objectName()
