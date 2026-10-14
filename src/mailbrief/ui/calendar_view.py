"""The Calendar page: the owner's events, with open actions' dates beside them, as a week
of hours, a month of days or a week of rows (``CalendarView``), as the owner chooses.

Event titles are the owner's text and action titles may come from email, so rows are plain
list-item text and the grids draw them with ``drawText``: never tooltips, which Qt may
render as rich text. Deadlines and target dates are only shown here; they change on the
action itself (ADR 0018).
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidgetItem,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.ui.calendar_grid import MonthGrid, WeekGrid
from mailbrief.ui.calendar_layout import (
    WEEK_DAYS,
    CalendarView,
    Entry,
    add_months,
    clock,
    entry_key,
    marker_text,
    month_start,
    shown_month,
)
from mailbrief.ui.deadline_text import MONTH_NAMES, day_text, month_day
from mailbrief.ui.labels import ElidedLabel, outline_button, plain_label
from mailbrief.ui.lists import ActivatingList
from mailbrief.ui.theme import TEXT_PX, TITLE_PX, ui_font

__all__ = [
    "CalendarPanel",
    "CalendarView",
    "day_heading",
    "entry_key",
    "event_when",
    "marker_text",
    "month_title",
    "week_start",
    "week_title",
]

NEW: Final = "new"
OPEN: Final = "open"
DELETE: Final = "delete"
OPEN_GOOGLE: Final = "open-google"
ALLOW: Final = "allow"
REFRESH: Final = "refresh"
GOOGLE_OFF: Final = "off"  # Gmail isn't connected.
GOOGLE_ASK: Final = "ask"  # Connected; the sign-in lacks the calendar permissions.
GOOGLE_ON: Final = "on"
EMPTY: Final = "Nothing this week. Add an event with New event."
EMPTY_MONTH: Final = "Nothing in these six weeks. Add an event with New event."
MARKER_NOTE: Final = "Deadlines and targets come from your open actions; change them there."
# The views, in the switcher's order, and what Previous, This and Next say in each.
VIEW_NAMES: Final = {
    CalendarView.WEEK: "Week",
    CalendarView.MONTH: "Month",
    CalendarView.LIST: "List",
}
_VIEWS: Final = tuple(VIEW_NAMES)
_WEEK_STEPS: Final = ("&Previous week", "This wee&k", "Ne&xt week")
_MONTH_STEPS: Final = ("&Previous month", "This mont&h", "Ne&xt month")

Row = Entry | None  # None is a day's heading.


def week_start(day: date) -> date:
    """The Monday of the week holding ``day``."""
    return day - timedelta(days=day.weekday())


def week_title(first_day: date, today: date) -> str:
    """ "Oct 12 – 18", or with both months when the week spans two: "Sep 28 – Oct 4"."""
    last = first_day + timedelta(days=WEEK_DAYS - 1)
    end = str(last.day) if last.month == first_day.month else month_day(last, today=today)
    return f"{month_day(first_day, today=today)} – {end}"


def month_title(month: date) -> str:
    """ "October 2026"."""
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def day_heading(day: date, today: date) -> str:
    """ "Mon Oct 12", and "Today" on the owner's own day."""
    text = day_text(day, today=today)
    return f"{text} · Today" if day == today else text


def event_when(event: CalendarEvent | GoogleEvent, day: date, zone: ZoneInfo, today: date) -> str:
    """When the event happens, as seen from ``day``: "All day", "09:00–10:00", or with the
    other day for one that crosses midnight: "22:00–Oct 13 01:00". An end at midnight
    belongs to the day before it ("22:00–00:00"), but when that isn't ``day`` the end is
    dated as the clock says: Monday 22:00 to Wednesday 00:00 is "22:00–Oct 14 00:00"."""
    if event.all_day:
        return "All day"
    assert event.starts_at_utc is not None and event.ends_at_utc is not None
    first, last = event.days(zone)
    start, end = clock(event.starts_at_utc, zone), clock(event.ends_at_utc, zone)
    if first != day:
        start = f"{month_day(first, today=today)} {start}"
    if last != day:
        end = f"{month_day(event.ends_at_utc.astimezone(zone).date(), today=today)} {end}"
    return f"{start}–{end}"


def event_text(event: CalendarEvent | GoogleEvent, day: date, zone: ZoneInfo, today: date) -> str:
    """A Google event names its calendar; the owner's own events need no label."""
    text = f"{event_when(event, day, zone, today)} · {event.title}"
    return f"{text} · {event.calendar_name}" if isinstance(event, GoogleEvent) else text


def day_rows(shown: CalendarRange, day: date, zone: ZoneInfo) -> list[Entry]:
    """One day's entries: all-day events, then action dates, then timed events by start.

    The owner's events and Google's are interleaved; at the same start, the owner's first.
    """
    candidates: list[CalendarEvent | GoogleEvent] = [*shown.events, *shown.google_events]
    touching = [event for event in candidates if event.days(zone)[0] <= day <= event.days(zone)[1]]
    whole = [event for event in touching if event.all_day]
    timed = sorted(
        (event for event in touching if not event.all_day),
        key=lambda event: (event.starts_at_utc, isinstance(event, GoogleEvent)),
    )
    return [*whole, *(marker for marker in shown.markers if marker.day == day), *timed]


class CalendarPanel(QWidget):
    """Shows the days asked for and asks the window to act; it changes nothing itself.

    ``event_requested(kind, value)`` carries NEW with the day to start on, or OPEN or DELETE
    with the selected CalendarEvent. ``week_requested(first_day)`` asks for another week
    (the Week and List views), ``month_requested(first)`` for another month (the Month
    view) and ``view_requested(view, keep)`` for the days of a view switched to, around
    ``focus_day()``: ``keep`` when the owner chose it with the tabs, so it is remembered,
    and not when "+N more" opened the List view. The rows of the List view hold the
    selection for every view: the grids show it and choose through it. While another
    operation runs (``set_busy``), reading days, New event, Open, Open in Google Calendar
    and Plan with AI still work, since they change nothing; Delete event, Allow and Refresh
    wait.
    """

    event_requested = Signal(str, object)
    week_requested = Signal(object)
    month_requested = Signal(object)
    view_requested = Signal(str, bool)
    google_requested = Signal(str)
    plan_requested = Signal()

    def __init__(self, view: CalendarView = CalendarView.WEEK) -> None:
        super().__init__()
        self._busy = False
        self._shown: CalendarRange | None = None
        # Whether the days shown are a month's grid: read for the Month view, while the view
        # may already be another, until the window reads that view's days.
        self._shown_month = False
        self._today: date | None = None
        self._rows: list[tuple[date, Row]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        # It elides rather than widen the window: a month's name is long.
        self.heading = ElidedLabel("Calendar", px=TITLE_PX)
        self.heading.setFont(ui_font(TITLE_PX, semibold=True))
        self.heading.setObjectName("calendarHeading")
        top.addWidget(self.heading, 1)
        self.previous_button = outline_button(_WEEK_STEPS[0], "previousWeekButton", mnemonic=True)
        self.this_week_button = outline_button(_WEEK_STEPS[1], "thisWeekButton", mnemonic=True)
        self.next_button = outline_button(_WEEK_STEPS[2], "nextWeekButton", mnemonic=True)
        for button in (self.previous_button, self.this_week_button, self.next_button):
            top.addWidget(button)
        layout.addLayout(top)
        bar = QHBoxLayout()
        self.views = QTabBar()
        self.views.setObjectName("calendarViews")
        self.views.setAccessibleName("Calendar views")
        self.views.setDrawBase(False)
        self.views.setExpanding(False)
        self.views.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for shown_view in _VIEWS:
            self.views.addTab(VIEW_NAMES[shown_view])
        self.views.setCurrentIndex(_VIEWS.index(view))
        bar.addWidget(self.views)
        self.google_status = plain_label("", tone="muted")
        self.google_status.setObjectName("googleStatus")
        bar.addWidget(self.google_status, 1)
        self.allow_button = outline_button(
            "Allow &Google Calendar", "allowCalendarButton", mnemonic=True
        )
        self.refresh_button = outline_button("Re&fresh", "refreshCalendarButton", mnemonic=True)
        for button, kind in ((self.allow_button, ALLOW), (self.refresh_button, REFRESH)):
            bar.addWidget(button)
            button.clicked.connect(lambda _checked=False, chosen=kind: self._request_google(chosen))
        layout.addLayout(bar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("calendarStack")
        self.week_grid = WeekGrid()
        self.month_grid = MonthGrid()
        list_page = QWidget()
        list_page.setObjectName("calendarListPage")
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.list = ActivatingList()
        self.list.setObjectName("calendarList")
        self.list.setAccessibleName("Calendar week")
        self.list.currentRowChanged.connect(lambda _row: self._selection_moved())
        self.list.itemActivated.connect(lambda _item: self._request(OPEN))
        list_layout.addWidget(self.list, 1)
        for page in (self.week_grid, self.month_grid, list_page):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.empty = plain_label(EMPTY, tone="muted")  # Under every view.
        self.empty.setObjectName("calendarEmpty")
        layout.addWidget(self.empty)
        self.note = plain_label(MARKER_NOTE, tone="muted")
        self.note.setObjectName("calendarNote")
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.new_button = outline_button("Ne&w event", "newEventButton", mnemonic=True)
        self.open_button = outline_button("&Open", "openEventButton", mnemonic=True)
        self.delete_button = outline_button("Delete &event", "deleteEventButton", mnemonic=True)
        self.open_google_button = outline_button(
            "Open &in Google Calendar", "openGoogleEventButton", mnemonic=True
        )
        self.plan_button = outline_button("Plan with &AI", "planCalendarButton", mnemonic=True)
        for button in (
            self.new_button,
            self.open_button,
            self.delete_button,
            self.open_google_button,
            self.plan_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)  # Buttons keep their own width.
        layout.addLayout(buttons)
        self.previous_button.clicked.connect(lambda: self._request_period(-1))
        self.next_button.clicked.connect(lambda: self._request_period(1))
        self.this_week_button.clicked.connect(self._request_this_period)
        self.new_button.clicked.connect(self._request_new)
        self.open_button.clicked.connect(lambda: self._request(OPEN))
        self.delete_button.clicked.connect(lambda: self._request(DELETE))
        self.open_google_button.clicked.connect(self._request_open_google)
        self.plan_button.clicked.connect(self._request_plan)
        self.views.currentChanged.connect(self._view_changed)
        for grid in (self.week_grid, self.month_grid):
            grid.chosen.connect(self._choose)
            grid.activated.connect(self._activate)
            grid.more_requested.connect(self._show_more)
        self.month_grid.period_requested.connect(self._request_period)
        self._google_mode = GOOGLE_OFF
        self.set_google(GOOGLE_OFF, "")
        self.empty.hide()
        self.note.hide()
        self._show_view()
        self._update_buttons()

    @property
    def first_day(self) -> date | None:
        """The first day shown (a Monday), or None before anything is shown."""
        return None if self._shown is None else self._shown.first_day

    def view(self) -> CalendarView:
        return _VIEWS[max(0, self.views.currentIndex())]

    def set_view(self, view: CalendarView) -> None:
        """Show ``view`` without asking for its days: the window reads them next."""
        self.views.blockSignals(True)
        try:
            self.views.setCurrentIndex(_VIEWS.index(view))
        finally:
            self.views.blockSignals(False)
        self._switched()

    def set_clock(self, clock_now: Callable[[], datetime]) -> None:
        """Where the week grid reads the time now, for its line across today."""
        self.week_grid.clock = clock_now

    def focus_day(self) -> date | None:
        """The day another view should open on: the selected day, else today when it is
        shown, else the 1st of the month when a month's grid is shown, else the first day
        shown; None before anything is shown, when the window knows the owner's today."""
        row = self.list.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row][0]
        shown, today = self._shown, self._today
        if shown is None or today is None:
            return None
        if shown.first_day <= today <= shown.last_day:
            return today
        return shown_month(shown.first_day) if self._shown_month else shown.first_day

    def selected(self) -> CalendarEvent | None:
        """The selected event when it is the owner's; Google's can't be changed here."""
        item = self._entry()
        return item if isinstance(item, CalendarEvent) else None

    def selected_google(self) -> GoogleEvent | None:
        item = self._entry()
        return item if isinstance(item, GoogleEvent) else None

    def _entry(self) -> Row:
        row = self.list.currentRow()
        return self._rows[row][1] if 0 <= row < len(self._rows) else None

    def set_google(self, mode: str, note: str) -> None:
        """What the one Google sign-in allows, and what the cache holds, in one line:
        GOOGLE_OFF shows no button, GOOGLE_ASK offers Allow and GOOGLE_ON offers Refresh."""
        self._google_mode = mode
        self.google_status.setText(note)
        self.allow_button.setVisible(mode == GOOGLE_ASK)
        self.refresh_button.setVisible(mode == GOOGLE_ON)
        self._update_buttons()

    def show_range(self, shown: CalendarRange, zone: ZoneInfo, today: date) -> None:
        """Replace the days, keeping the selection on the same entry when it remains: on the
        same day's row when the entry is still there, else on its first."""
        row = self.list.currentRow()
        previous = self._rows[row] if 0 <= row < len(self._rows) else None
        self._shown = shown
        # The window reads the days of the view shown, and a read started before a switch
        # never shows: so these are the Month view's days when it is the view now.
        self._shown_month = self.view() is CalendarView.MONTH
        self._today = today
        self._rows = []
        self.list.blockSignals(True)
        try:
            self.list.clear()
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
                        marker_text(entry, zone)
                        if isinstance(entry, ActionMarker)
                        else event_text(entry, day, zone, today)
                    )
                    self.list.addItem(QListWidgetItem(text))
                day += timedelta(days=1)
        finally:
            self.list.blockSignals(False)
        for grid in (self.week_grid, self.month_grid):
            grid.show_range(shown, zone, today)
        if previous is not None:
            if previous[1] is not None:
                self._select(entry_key(previous[1]), previous[0])
            else:
                self._select_day(previous[0])
        self.empty.setText(EMPTY_MONTH if self._shown_month else EMPTY)
        self.empty.setVisible(not shown.events and not shown.markers and not shown.google_events)
        self.note.setVisible(bool(shown.markers))
        self._show_heading()
        self._selection_moved()

    def select_event(self, public_id: str) -> None:
        """Select the owner's event on the first day it shows, when it's shown."""
        self._select(("event", public_id), None)

    def select_entry(self, key: tuple[str, ...], day: date | None = None) -> None:
        """Select the entry with ``key`` (entry_key) on ``day`` if it shows there, else on
        the first day it shows, when it's shown."""
        self._select(key, day)

    def _select(self, key: tuple[str, ...], day: date | None) -> None:
        found = [
            number
            for number, (_day, entry) in enumerate(self._rows)
            if entry is not None and entry_key(entry) == key
        ]
        same_day = [number for number in found if self._rows[number][0] == day]
        if found:
            self.list.setCurrentRow((same_day or found)[0])

    def _select_day(self, day: date) -> None:
        """Make ``day``'s heading the current row: the day chosen, with no entry."""
        number = next(
            (n for n, (shown, entry) in enumerate(self._rows) if shown == day and entry is None),
            None,
        )
        if number is not None:
            self.list.setCurrentRow(number)

    def _selection_moved(self) -> None:
        """The list's current row moved: the grids show it, and the buttons follow."""
        row = self.list.currentRow()
        day, entry = self._rows[row] if 0 <= row < len(self._rows) else (None, None)
        key = None if entry is None else entry_key(entry)
        for grid in (self.week_grid, self.month_grid):
            grid.set_selection(day, key)
        self._update_buttons()

    def _choose(self, day: object, entry: object) -> None:
        """A grid chose a day, or an entry on it."""
        if not isinstance(day, date):
            return
        if isinstance(entry, CalendarEvent | GoogleEvent | ActionMarker):
            self._select(entry_key(entry), day)
        else:
            self._select_day(day)

    def _activate(self, day: object, entry: object) -> None:
        """A double-click or Return in a grid: an event opens (Google's in Google
        Calendar), and an empty day asks for a new event on it."""
        if not isinstance(day, date):
            return
        self._choose(day, entry)
        if entry is None:
            if self._shown is not None:
                self.event_requested.emit(NEW, day)
        elif isinstance(entry, CalendarEvent):
            self._request(OPEN)
        elif isinstance(entry, GoogleEvent):
            self._request_open_google()

    def _show_more(self, day: object) -> None:
        """ "+N more": the List view, on that day's week, shows all of the day's entries.
        It's a look at one day, so the view the owner keeps stays as it was."""
        if isinstance(day, date):
            self._select_day(day)
            self.set_view(CalendarView.LIST)
            self.view_requested.emit(CalendarView.LIST.value, False)

    def _view_changed(self, _index: int) -> None:
        self._switched()
        self.view_requested.emit(self.view().value, True)

    def _switched(self) -> None:
        """Show the view now chosen. Until the window reads its days, its grid shows none
        rather than another view's: a week drawn as a month's grid, or the reverse."""
        month = self.view() is CalendarView.MONTH
        if self._shown is not None and month != self._shown_month:
            (self.month_grid if month else self.week_grid).clear()
        self._show_view()

    def _show_view(self) -> None:
        view = self.view()
        pages = {
            CalendarView.WEEK: self.week_grid,
            CalendarView.MONTH: self.month_grid,
        }
        page = pages.get(view)
        self.stack.setCurrentIndex(2 if page is None else self.stack.indexOf(page))
        steps = _MONTH_STEPS if view is CalendarView.MONTH else _WEEK_STEPS
        for button, text in zip(
            (self.previous_button, self.this_week_button, self.next_button), steps, strict=True
        ):
            button.setText(text)
            button.setAccessibleName(text.replace("&", ""))
        self._show_heading()

    def _show_heading(self) -> None:
        shown, today = self._shown, self._today
        if shown is None or today is None:
            self.heading.setText("Calendar")
        elif self._shown_month:
            self.heading.setText(f"Calendar · {month_title(shown_month(shown.first_day))}")
        else:
            self.heading.setText(f"Calendar · {week_title(shown.first_day, today)}")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        shown = self._shown is not None
        chosen = self.selected() is not None
        for button in (self.previous_button, self.this_week_button, self.next_button):
            button.setEnabled(shown)
        self.new_button.setEnabled(shown)
        self.plan_button.setEnabled(shown)
        self.open_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen and not self._busy)  # A change waits its turn.
        linked = self.selected_google()
        self.open_google_button.setEnabled(linked is not None and linked.web_link is not None)
        for button in (self.allow_button, self.refresh_button):
            button.setEnabled(not self._busy)

    def _request_period(self, step: int) -> None:
        """The week or month ``step`` before (negative) or after the one shown."""
        shown = self._shown
        if shown is None:
            return
        if self.view() is CalendarView.MONTH:
            self.month_requested.emit(add_months(shown_month(shown.first_day), step))
        else:
            self.week_requested.emit(shown.first_day + timedelta(days=WEEK_DAYS * step))

    def _request_this_period(self) -> None:
        if self._today is None:
            return
        if self.view() is CalendarView.MONTH:
            self.month_requested.emit(month_start(self._today))
        else:
            self.week_requested.emit(week_start(self._today))

    def _request_new(self) -> None:
        """A new event starts on the day in focus (``focus_day``): the day selected, else
        today when shown, else the first day shown (the month's 1st in a month's grid)."""
        day = self.focus_day()
        if day is not None:
            self.event_requested.emit(NEW, day)

    def _request_plan(self) -> None:
        if self._shown is not None:
            self.plan_requested.emit()

    def _request_google(self, kind: str) -> None:
        if not self._busy:
            self.google_requested.emit(kind)

    def _request_open_google(self) -> None:
        event = self.selected_google()
        if event is not None and event.web_link is not None:
            self.event_requested.emit(OPEN_GOOGLE, event)

    def _request(self, kind: str) -> None:
        event = self.selected()
        if event is not None and not (kind == DELETE and self._busy):
            self.event_requested.emit(kind, event)
