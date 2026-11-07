"""The drop-down calendar of every date field, drawn like the rest of the app.

Qt's own is a blue bar with black arrows over a grid with red weekends and a square
selection. `DatePicker` is flat, as the buttons and fields are (docs/ui/studio.md): a
rounded panel with a hairline edge, a plain navigation row with chevrons, every day in the
text colour, the chosen day a rounded `primary_fill` cell and today ringed in the accent.
Every date field gets it through `calendar_popup`; `tests/unit/test_source_rules.py`
refuses `setCalendarPopup` anywhere else.
"""

from typing import Final

from PySide6.QtCore import QDate, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent, QPen, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QDateEdit, QToolButton, QWidget

from mailbrief.ui.theme import RADIUS, current_tokens, follow_theme, icon
from mailbrief.ui.theme.tokens import FIELD_RADIUS

NAV_ICON_PX: Final = 16
# The panel's padding, which keeps the days and the navigation row clear of its corners.
MARGIN: Final = 6
CELL_INSET: Final = 2.0  # A day's rounded cell, inside the grid's cell.
TODAY_RING: Final = 1.5
# The navigation row's buttons, as Qt names them.
PREVIOUS: Final = "qt_calendar_prevmonth"
NEXT: Final = "qt_calendar_nextmonth"
NAVIGATION_BAR: Final = "qt_calendar_navigationbar"


class DatePicker(QCalendarWidget):
    """A date field's calendar: Qt's keyboard and month and year menus, painted flat."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("datePicker")
        self.setAccessibleName("Choose a date")
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        bar = self.findChild(QWidget, NAVIGATION_BAR)
        if bar is not None:
            bar.setAutoFillBackground(False)  # Qt fills it with the highlight colour.
        follow_theme(self, _recolour)

    def paintEvent(self, event: QPaintEvent) -> None:
        """The panel the navigation row and the days lie on: rounded, with a hairline edge,
        so the pop-up's window (translucent, ui/theme/style.py) shows no square corners."""
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tokens.hairline), 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QColor(tokens.panel))
        half = 0.5 / self.devicePixelRatioF()
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(half, half, -half, -half), RADIUS, RADIUS
        )
        painter.end()

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate) -> None:
        """One day: the chosen one a rounded `primary_fill` cell, today ringed in the
        accent, a day of another month or out of range in muted text."""
        tokens = current_tokens()
        selected = date == self.selectedDate()
        shown = date.month() == self.monthShown() and date.year() == self.yearShown()
        allowed = self.minimumDate() <= date <= self.maximumDate()
        cell = QRectF(rect).adjusted(CELL_INSET, CELL_INSET, -CELL_INSET, -CELL_INSET)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.primary_fill))
            painter.drawRoundedRect(cell, FIELD_RADIUS, FIELD_RADIUS)
            text = tokens.on_primary
        else:
            text = tokens.text if shown and allowed else tokens.text_muted
            if date == QDate.currentDate():
                inset = TODAY_RING / 2
                painter.setPen(QPen(QColor(tokens.accent_fg), TODAY_RING))
                painter.setBrush(QBrush())
                painter.drawRoundedRect(
                    cell.adjusted(inset, inset, -inset, -inset),
                    FIELD_RADIUS - inset,
                    FIELD_RADIUS - inset,
                )
        painter.setPen(QColor(text))
        painter.setFont(self.font())
        painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, str(date.day()))
        painter.restore()


def _recolour(picker: DatePicker) -> None:
    """What the picker keeps rather than reads at paint time: the weekday names' colour
    and the chevrons."""
    tokens = current_tokens()
    # The weekday names; Qt applies a weekday's format to its name too (days are painted
    # by paintCell), so the weekends take the same one in place of Qt's red.
    header = QTextCharFormat()
    header.setForeground(QColor(tokens.text_secondary))
    header.setBackground(QColor(Qt.GlobalColor.transparent))
    picker.setHeaderTextFormat(header)
    for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        picker.setWeekdayTextFormat(day, header)
    for name, icon_name, accessible in (
        (PREVIOUS, "chevron-left", "Previous month"),
        (NEXT, "chevron-right", "Next month"),
    ):
        button = picker.findChild(QToolButton, name)
        if button is not None:
            button.setIcon(icon(icon_name, tokens.text_secondary, NAV_ICON_PX))
            button.setAccessibleName(accessible)


def calendar_popup(edit: QDateEdit) -> None:
    """Give ``edit`` a drop-down calendar: a `DatePicker`. Every date field's goes through
    here."""
    edit.setCalendarPopup(True)
    edit.setCalendarWidget(DatePicker())
