"""The Calendar page: a week of the owner's events, with open actions' dates beside them.

Event titles are the owner's text and action titles may come from email, so rows are plain
list-item text and never tooltips, which Qt may render as rich text. Deadlines and target
dates are only shown here; they change on the action itself (ADR 0018).
"""

from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget

from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange, MarkerKind
from mailbrief.ui.deadline_text import day_text, month_day
from mailbrief.ui.labels import outline_button, plain_label
from mailbrief.ui.lists import ActivatingList
from mailbrief.ui.theme import TEXT_PX, TITLE_PX, ui_font

NEW: Final = "new"
OPEN: Final = "open"
DELETE: Final = "delete"
WEEK_DAYS: Final = 7
EMPTY: Final = "Nothing this week. Add an event with New event."
MARKER_NOTE: Final = "Deadlines and targets come from your open actions; change them there."

Row = CalendarEvent | ActionMarker | None  # None is a day's heading.


def week_start(day: date) -> date:
    """The Monday of the week holding ``day``."""
    return day - timedelta(days=day.weekday())


def week_title(first_day: date, today: date) -> str:
    """ "Oct 12 – 18", or with both months when the week spans two: "Sep 28 – Oct 4"."""
    last = first_day + timedelta(days=WEEK_DAYS - 1)
    end = str(last.day) if last.month == first_day.month else month_day(last, today=today)
    return f"{month_day(first_day, today=today)} – {end}"


def day_heading(day: date, today: date) -> str:
    """ "Mon Oct 12", and "Today" on the owner's own day."""
    text = day_text(day, today=today)
    return f"{text} · Today" if day == today else text


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    return f"{moment.astimezone(zone):%H:%M}"


def event_when(event: CalendarEvent, day: date, zone: ZoneInfo, today: date) -> str:
    """When the event happens, as seen from ``day``: "All day", "09:00–10:00", or with the
    other day for one that crosses midnight: "22:00–Oct 13 01:00"."""
    if event.all_day:
        return "All day"
    assert event.starts_at_utc is not None and event.ends_at_utc is not None
    first, last = event.days(zone)
    start, end = _clock(event.starts_at_utc, zone), _clock(event.ends_at_utc, zone)
    if first != day:
        start = f"{month_day(first, today=today)} {start}"
    if last != day:
        end = f"{month_day(last, today=today)} {end}"
    return f"{start}–{end}"


def event_text(event: CalendarEvent, day: date, zone: ZoneInfo, today: date) -> str:
    return f"{event_when(event, day, zone, today)} · {event.title}"


def marker_text(marker: ActionMarker, zone: ZoneInfo) -> str:
    """ "Deadline · Pay the invoice", "Deadline 17:00 · …" or "Target · …", and who does it
    when someone else does."""
    if marker.kind is MarkerKind.TARGET:
        label = "Target"
    elif marker.due_at_utc is None:
        label = "Deadline"
    else:
        label = f"Deadline {_clock(marker.due_at_utc, zone)}"
    waiting = " · waiting for others" if marker.waiting else ""
    return f"{label} · {marker.action_title}{waiting}"


def day_rows(shown: CalendarRange, day: date, zone: ZoneInfo) -> list[CalendarEvent | ActionMarker]:
    """One day's entries: all-day events, then action dates, then timed events by start."""
    touching = [
        event for event in shown.events if event.days(zone)[0] <= day <= event.days(zone)[1]
    ]
    return [
        *(event for event in touching if event.all_day),
        *(marker for marker in shown.markers if marker.day == day),
        *(event for event in touching if not event.all_day),
    ]


class CalendarPanel(QWidget):
    """Shows one week and asks the window to act; it changes nothing itself.

    ``event_requested(kind, value)`` carries NEW with the day to start on, or OPEN or DELETE
    with the selected CalendarEvent. ``week_requested(first_day)`` asks for another week.
    """

    event_requested = Signal(str, object)
    week_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self._shown: CalendarRange | None = None
        self._today: date | None = None
        self._rows: list[tuple[date, Row]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.heading = plain_label("Calendar")
        self.heading.setFont(ui_font(TITLE_PX, medium=True))
        self.heading.setObjectName("calendarHeading")
        self.heading.setWordWrap(False)
        top.addWidget(self.heading, 1)
        self.previous_button = outline_button("&Previous week", "previousWeekButton", mnemonic=True)
        self.this_week_button = outline_button("This wee&k", "thisWeekButton", mnemonic=True)
        self.next_button = outline_button("Ne&xt week", "nextWeekButton", mnemonic=True)
        for button in (self.previous_button, self.this_week_button, self.next_button):
            top.addWidget(button)
        layout.addLayout(top)
        self.list = ActivatingList()
        self.list.setObjectName("calendarList")
        self.list.setAccessibleName("Calendar week")
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.list.itemActivated.connect(lambda _item: self._request(OPEN))
        layout.addWidget(self.list, 1)
        self.empty = plain_label(EMPTY, tone="muted")
        self.empty.setObjectName("calendarEmpty")
        layout.addWidget(self.empty)
        self.note = plain_label(MARKER_NOTE, tone="muted")
        self.note.setObjectName("calendarNote")
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.new_button = outline_button("Ne&w event", "newEventButton", mnemonic=True)
        self.open_button = outline_button("&Open", "openEventButton", mnemonic=True)
        self.delete_button = outline_button("Delete &event", "deleteEventButton", mnemonic=True)
        for button in (self.new_button, self.open_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)  # Buttons keep their own width.
        layout.addLayout(buttons)
        self.previous_button.clicked.connect(lambda: self._request_week(-WEEK_DAYS))
        self.next_button.clicked.connect(lambda: self._request_week(WEEK_DAYS))
        self.this_week_button.clicked.connect(self._request_this_week)
        self.new_button.clicked.connect(self._request_new)
        self.open_button.clicked.connect(lambda: self._request(OPEN))
        self.delete_button.clicked.connect(lambda: self._request(DELETE))
        self.empty.hide()
        self.note.hide()
        self._update_buttons()

    @property
    def first_day(self) -> date | None:
        """The Monday of the week shown, or None before anything is shown."""
        return None if self._shown is None else self._shown.first_day

    def selected(self) -> CalendarEvent | None:
        row = self.list.currentRow()
        if 0 <= row < len(self._rows):
            item = self._rows[row][1]
            return item if isinstance(item, CalendarEvent) else None
        return None

    def show_range(self, shown: CalendarRange, zone: ZoneInfo, today: date) -> None:
        """Replace the week, keeping the selection on the same event when it remains."""
        previous = self.selected()
        self._shown = shown
        self._today = today
        self._rows = []
        self.list.clear()
        self.heading.setText(f"Calendar · {week_title(shown.first_day, today)}")
        day = shown.first_day
        while day <= shown.last_day:
            self._rows.append((day, None))
            heading = QListWidgetItem(day_heading(day, today))
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)  # Read, but never selected.
            heading.setFont(ui_font(TEXT_PX, medium=True))
            self.list.addItem(heading)
            for entry in day_rows(shown, day, zone):
                self._rows.append((day, entry))
                text = (
                    event_text(entry, day, zone, today)
                    if isinstance(entry, CalendarEvent)
                    else marker_text(entry, zone)
                )
                self.list.addItem(QListWidgetItem(text))
            day += timedelta(days=1)
        if previous is not None:
            for number, (_day, kept) in enumerate(self._rows):
                if isinstance(kept, CalendarEvent) and kept.public_id == previous.public_id:
                    self.list.setCurrentRow(number)
                    break
        self.empty.setVisible(not shown.events and not shown.markers)
        self.note.setVisible(bool(shown.markers))
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        shown = self._shown is not None and not self._busy
        chosen = self.selected() is not None and not self._busy
        for button in (self.previous_button, self.this_week_button, self.next_button):
            button.setEnabled(shown)
        self.new_button.setEnabled(shown)
        self.open_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    def _request_week(self, offset: int) -> None:
        if self._shown is not None and not self._busy:
            self.week_requested.emit(self._shown.first_day + timedelta(days=offset))

    def _request_this_week(self) -> None:
        if self._today is not None and not self._busy:
            self.week_requested.emit(week_start(self._today))

    def _request_new(self) -> None:
        """A new event starts on the day selected, else today when shown, else Monday."""
        if self._shown is None or self._busy:
            return
        row = self.list.currentRow()
        if 0 <= row < len(self._rows):
            day = self._rows[row][0]
        elif (
            self._today is not None and self._shown.first_day <= self._today <= self._shown.last_day
        ):
            day = self._today
        else:
            day = self._shown.first_day
        self.event_requested.emit(NEW, day)

    def _request(self, kind: str) -> None:
        event = self.selected()
        if event is not None and not self._busy:
            self.event_requested.emit(kind, event)
