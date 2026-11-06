"""The Calendar page: the owner's events, with open actions' dates beside them, as a day or
a week of hours, a month of days or a week of rows (``CalendarView``), as the owner chooses.

Event titles are the owner's text and action titles may come from email, so rows are plain
list-item text and the grids draw them with ``drawText``: never tooltips, which Qt may
render as rich text. Deadlines and target dates are only shown here; they change on the
action itself (ADR 0018). Work blocks (ADR 0028), time set aside for a task, show beside the
events; changing one never changes its task.

Beside the hours, the tasks to schedule (ADR 0029) list the owner's open tasks that still
need time: dragged onto the hours, or with Return for the next free time, they ask for a
block. A line under the hours says how much working time is free.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidgetItem,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.availability import UnscheduledTask, UnscheduledTasks, day_availability
from mailbrief.domain.events import (
    ActionMarker,
    CalendarEvent,
    CalendarRange,
    MarkerKind,
    ScheduledBlock,
)
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.working_hours import WorkingHours
from mailbrief.ui.calendar_grid import MonthGrid, WeekGrid
from mailbrief.ui.calendar_layout import (
    WEEK_DAYS,
    CalendarView,
    Entry,
    Timed,
    add_months,
    block_note,
    clock,
    entry_key,
    has_hours,
    marker_label,
    marker_note,
    marker_text,
    month_start,
    show_as,
    shown_month,
)
from mailbrief.ui.deadline_text import (
    MONTH_NAMES,
    day_text,
    full_day_text,
    length_text,
    month_day,
)
from mailbrief.ui.hours_editor import windows_text
from mailbrief.ui.labels import ElidedLabel, outline_button, plain_label
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, AgendaRowDelegate, DisplayRow
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import TEXT_PX, TITLE_PX, ui_font
from mailbrief.ui.unscheduled_view import UnscheduledPanel

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
# What block_requested asks (ADR 0028).
NEW_BLOCK: Final = "new-block"  # With the day to schedule on.
OPEN_BLOCK: Final = "open-block"  # With the ScheduledBlock.
MOVE_BLOCK: Final = "move-block"  # With (block, days, minutes, resize).
UNSCHEDULE_BLOCK: Final = "unschedule-block"  # With the ScheduledBlock.
GOOGLE_OFF: Final = "off"  # Gmail isn't connected.
GOOGLE_ASK: Final = "ask"  # Connected; the sign-in lacks the calendar permissions.
GOOGLE_ON: Final = "on"
EMPTY: Final = "Nothing this week. Add an event with New event."
EMPTY_DAY: Final = "Nothing this day. Add an event with New event."
EMPTY_MONTH: Final = "Nothing in these six weeks. Add an event with New event."
NO_HOURS: Final = "Set your working hours to see your free time."
MARKER_NOTE: Final = "Deadlines and targets come from your open actions; change them there."
# The views, in the switcher's order, and what Previous, This and Next say in each.
VIEW_NAMES: Final = {
    CalendarView.DAY: "Day",
    CalendarView.WEEK: "Week",
    CalendarView.MONTH: "Month",
    CalendarView.LIST: "List",
}
_VIEWS: Final = tuple(VIEW_NAMES)
_DAY_STEPS: Final = ("&Previous day", "To&day", "Ne&xt day")
_WEEK_STEPS: Final = ("&Previous week", "This wee&k", "Ne&xt week")
_MONTH_STEPS: Final = ("&Previous month", "This mont&h", "Ne&xt month")


def range_kind(view: CalendarView) -> str:
    """The days a view reads: one ("day"), a week ("week": the Week and List views) or a
    month's six weeks ("month")."""
    if view is CalendarView.DAY:
        return "day"
    return "month" if view is CalendarView.MONTH else "week"


def free_text(shown: CalendarRange, zone: ZoneInfo, view: CalendarView) -> str:
    """The line under the hours: how much working time is free, worked out locally from
    the days shown (ADR 0029). "" for the Month and List views."""
    if not has_hours(view):
        return ""
    hours: WorkingHours = shown.hours
    if not hours.is_set:
        return NO_HOURS
    if view is CalendarView.DAY:
        day = shown.first_day
        found = day_availability(shown, hours, day, zone)
        if found.day_off:
            return "Day off"
        if not found.working_minutes:
            return "No working hours this day"
        left = length_text(timedelta(minutes=found.free_minutes))
        return f"Working {windows_text(hours.windows(day.weekday()))} · {left} free"
    days = [shown.first_day + timedelta(days=offset) for offset in range(WEEK_DAYS)]
    found_days = [day_availability(shown, hours, day, zone) for day in days]
    working = sum(day.working_minutes for day in found_days)
    free = sum(day.free_minutes for day in found_days)
    if not working:
        return "No working hours this week"
    return (
        f"{length_text(timedelta(minutes=free))} free of "
        f"{length_text(timedelta(minutes=working))} this week"
    )


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


def event_when(event: Timed, day: date, zone: ZoneInfo, today: date) -> str:
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


def list_time(event: Timed, day: date, zone: ZoneInfo) -> str:
    """The List view's time column for ``day``: "All day", the start on the event's first
    day, "until 06:00" on its last and "All day" on the days between, so an event that began
    on an earlier day never reads as starting at that day's time."""
    if event.all_day:
        return "All day"
    if event.starts_at_utc is None or event.ends_at_utc is None:
        return ""
    first, last = event.days(zone)
    if day == first:
        return clock(event.starts_at_utc, zone)
    return f"until {clock(event.ends_at_utc, zone)}" if day == last else "All day"


def event_text(event: Timed, day: date, zone: ZoneInfo, today: date) -> str:
    """A Google event names its calendar and a work block says it is a task's time, as in
    "10:00–11:00 · Task · Draft report"; the owner's own events need no label. An event that
    isn't what its shape makes it says Free or Busy (ADR 0029)."""
    if isinstance(event, ScheduledBlock):
        return f"{event_when(event, day, zone, today)} · {block_note(event)} · {event.title}"
    text = f"{event_when(event, day, zone, today)} · {event.title}"
    if isinstance(event, GoogleEvent):
        text = f"{text} · {event.calendar_name}"
    return text if (note := show_as(event)) is None else f"{text} · {note}"


def day_rows(shown: CalendarRange, day: date, zone: ZoneInfo) -> list[Entry]:
    """One day's entries: all-day events, then action dates, then timed events and work
    blocks by start.

    The owner's events, Google's and the blocks are interleaved; at the same start, the
    owner's events first, then Google's, then the blocks.
    """
    candidates: list[Timed] = [*shown.events, *shown.google_events, *shown.blocks]
    touching = [event for event in candidates if event.days(zone)[0] <= day <= event.days(zone)[1]]
    whole = [event for event in touching if event.all_day]
    timed = sorted(
        (event for event in touching if not event.all_day),
        key=lambda event: (
            event.starts_at_utc,
            isinstance(event, GoogleEvent),
            isinstance(event, ScheduledBlock),
        ),
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

    ``block_requested(kind, value)`` asks about work blocks: NEW_BLOCK with the day to
    schedule on (Schedule task), OPEN_BLOCK with a block to open, MOVE_BLOCK with
    ``(block, days, minutes, resize)`` from a drag or a key in the week grid, and
    UNSCHEDULE_BLOCK with a block. The window decides when a change can be saved.
    """

    event_requested = Signal(str, object)
    week_requested = Signal(object)
    month_requested = Signal(object)
    view_requested = Signal(str, bool)
    google_requested = Signal(str)
    plan_requested = Signal()
    google_preview_requested = Signal(object)
    block_requested = Signal(str, object)
    day_requested = Signal(object)
    hours_requested = Signal()
    task_schedule_requested = Signal(object)
    task_place_requested = Signal(object, object, int)
    tasks_toggled = Signal(bool)

    def __init__(self, view: CalendarView = CalendarView.WEEK, *, tasks: bool = False) -> None:
        super().__init__()
        self._busy = False
        self._shown: CalendarRange | None = None
        # Whether the days shown are a month's grid: read for the Month view, while the view
        # may already be another, until the window reads that view's days.
        self._shown_month = False
        self._shown_kind = "week"  # range_kind() of the view the days were read for.
        self._zone = ZoneInfo("UTC")
        self._tasks_shown = tasks  # The tasks to schedule beside the hours.
        self._tasks_total = 0
        self._today: date | None = None
        self._rows: list[tuple[date, Row]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.setContentsMargins(24, 8, 24, 8)
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
        toolbar = QWidget()
        toolbar.setObjectName("calendarToolbar")
        toolbar.setFixedHeight(50)
        toolbar.setLayout(top)
        layout.addWidget(toolbar)
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
        self.heading.hide()
        top.removeWidget(self.heading)
        top.insertLayout(0, bar, 1)
        self.views.setFixedWidth(232)
        self.google_status.setFont(ui_font(9))
        self.stack = QStackedWidget()
        self.stack.setObjectName("calendarStack")
        self.week_grid = WeekGrid()
        self.day_grid = WeekGrid(days=1)
        self.month_grid = MonthGrid()
        list_page = QWidget()
        list_page.setObjectName("calendarListPage")
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.list = ActivatingList()
        self.list.setObjectName("calendarList")
        self.list.setAccessibleName("Calendar week")
        self.list.currentRowChanged.connect(lambda _row: self._selection_moved())
        self.agenda_delegate = AgendaRowDelegate(self.list)
        self.list.setItemDelegate(self.agenda_delegate)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(lambda _item: self._preview_selected())
        self.list.itemClicked.connect(lambda _item: self._preview_selected())
        list_layout.addWidget(self.list, 1)
        for page in (self.week_grid, self.month_grid, list_page, self.day_grid):
            self.stack.addWidget(page)
        # The tasks to schedule sit beside the hours, in the Day and Week views.
        self.unscheduled = UnscheduledPanel()
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stack, 1)
        body.addWidget(self.unscheduled)
        layout.addLayout(body, 1)
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
        self.new_button.setText("New event")
        self.new_button.setProperty("variant", "primary")
        # Beside New event in the header: time for a task, chosen next (ADR 0028).
        self.schedule_button = outline_button("Schedule task", "scheduleTaskButton")
        self.schedule_button.setAccessibleName("Schedule time for a task")
        self.schedule_button.clicked.connect(self._request_schedule)
        for button in (
            self.new_button,
            self.open_button,
            self.delete_button,
            self.open_google_button,
            self.plan_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)  # Buttons keep their own width.
        buttons.setContentsMargins(24, 12, 24, 12)
        self.plan_button.setText("Plan with AI")
        self.plan_button.setFont(ui_font(10))
        self.hours_button = outline_button("Working hours", "workingHoursButton")
        self.hours_button.setAccessibleName("Set your working hours")
        self.hours_button.setFont(ui_font(10))
        self.hours_button.clicked.connect(self.hours_requested.emit)
        self.tasks_button = outline_button("Tasks to schedule", "unscheduledToggle")
        self.tasks_button.setFont(ui_font(10))
        self.tasks_button.clicked.connect(lambda: self.set_tasks_shown(not self._tasks_shown))
        self.free_line = plain_label("", tone="muted", px=10)
        self.free_line.setObjectName("calendarFreeTime")
        # Beside Plan with AI, before the stretch: the buttons, then how much time is free.
        for place, widget in enumerate((self.hours_button, self.tasks_button, self.free_line)):
            stretch = 1 if widget is self.free_line else 0
            buttons.insertWidget(buttons.indexOf(self.plan_button) + 1 + place, widget, stretch)
        for button in (
            self.new_button,
            self.open_button,
            self.delete_button,
            self.open_google_button,
        ):
            buttons.removeWidget(button)
            button.hide()
        legend = plain_label(
            "Your events    ? Google Calendar    ? Action dates", tone="muted", px=9
        )
        legend.setObjectName("calendarLegend")
        legend.setAccessibleName(MARKER_NOTE)
        buttons.addWidget(legend)
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
        for grid in (self.week_grid, self.day_grid, self.month_grid):
            grid.chosen.connect(self._choose)
            grid.activated.connect(self._activate)
            grid.more_requested.connect(self._show_more)
        for hours in (self.week_grid, self.day_grid):
            hours.block_change_requested.connect(
                lambda block, days, minutes, resize: self.block_requested.emit(
                    MOVE_BLOCK, (block, days, minutes, resize)
                )
            )
            hours.block_unschedule_requested.connect(
                lambda block: self.block_requested.emit(UNSCHEDULE_BLOCK, block)
            )
        self.month_grid.period_requested.connect(self._request_period)
        self.day_grid.period_requested.connect(self._request_period)
        self.unscheduled.schedule_requested.connect(self.task_schedule_requested.emit)
        self.unscheduled.dragged.connect(self._task_dragged)
        self.unscheduled.dropped.connect(self._task_dropped)
        self.unscheduled.drag_ended.connect(self._task_drag_ended)
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
        if self._shown_kind == "day":
            return shown.first_day
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

    def selected_block(self) -> ScheduledBlock | None:
        item = self._entry()
        return item if isinstance(item, ScheduledBlock) else None

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
        self._shown_kind = range_kind(self.view())
        self._zone = zone
        self._today = today
        self._rows = []
        self.list.blockSignals(True)
        try:
            clear_list(self.list)
            day = shown.first_day
            while day <= shown.last_day:
                self._rows.append((day, None))
                heading = QListWidgetItem(day_heading(day, today))
                heading.setFlags(Qt.ItemFlag.ItemIsEnabled)  # Read, but never selected.
                heading.setFont(ui_font(TEXT_PX, medium=True))
                self.list.addItem(heading)
                heading.setHidden(True)
                first_entry = True
                for entry in day_rows(shown, day, zone):
                    self._rows.append((day, entry))
                    text = (
                        marker_text(entry, zone)
                        if isinstance(entry, ActionMarker)
                        else event_text(entry, day, zone, today)
                    )
                    item = QListWidgetItem(text)
                    if isinstance(entry, ActionMarker):
                        # A deadline's time goes in the time column; the second line
                        # says what it is, and who it waits on or is for.
                        when = (
                            clock(entry.due_at_utc, zone)
                            if entry.kind is MarkerKind.DEADLINE and entry.due_at_utc
                            else marker_label(entry, zone)
                        )
                        preview = marker_note(entry, zone)
                    else:
                        when = list_time(entry, day, zone)
                        preview = (
                            entry.calendar_name
                            if isinstance(entry, GoogleEvent)
                            else block_note(entry)
                            if isinstance(entry, ScheduledBlock)
                            else "Your calendar"
                        )
                        if not isinstance(entry, ScheduledBlock) and (note := show_as(entry)):
                            preview = f"{preview} · {note}"
                        first, last = entry.days(zone)
                        if not entry.all_day and first != last:  # Its whole span, each day.
                            preview = f"{event_when(entry, day, zone, today)} · {preview}"
                    item.setData(
                        DISPLAY_ROW_ROLE,
                        DisplayRow(
                            title=entry.action_title
                            if isinstance(entry, ActionMarker)
                            else entry.title,
                            meta=when,
                            preview=preview,
                            date_tile=full_day_text(day, today=today) if first_entry else "",
                            accent=(
                                "google"
                                if isinstance(entry, GoogleEvent)
                                else "marker"
                                if isinstance(entry, ActionMarker)
                                else "event"
                            ),
                        ),
                    )
                    first_entry = False
                    self.list.addItem(item)
                day += timedelta(days=1)
        finally:
            self.list.blockSignals(False)
        for grid in (self.week_grid, self.day_grid, self.month_grid):
            grid.show_range(shown, zone, today)
        if previous is not None:
            if previous[1] is not None:
                self._select(entry_key(previous[1]), previous[0])
            else:
                self._select_day(previous[0])
        self.empty.setText(
            EMPTY_MONTH if self._shown_month else EMPTY_DAY if self._shown_kind == "day" else EMPTY
        )
        self.empty.setVisible(
            not shown.events and not shown.markers and not shown.google_events and not shown.blocks
        )
        self.note.hide()
        self._show_heading()
        self._show_free_line()
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
        for grid in (self.week_grid, self.day_grid, self.month_grid):
            grid.set_selection(day, key)
        self._update_buttons()

    def _choose(self, day: object, entry: object) -> None:
        """A grid chose a day, or an entry on it."""
        if not isinstance(day, date):
            return
        if isinstance(entry, CalendarEvent | GoogleEvent | ActionMarker | ScheduledBlock):
            self._select(entry_key(entry), day)
        else:
            self._select_day(day)

    def _preview_selected(self) -> None:
        entry = self._entry()
        if isinstance(entry, CalendarEvent | GoogleEvent):
            self.google_preview_requested.emit(entry)
        elif isinstance(entry, ScheduledBlock):
            self.block_requested.emit(OPEN_BLOCK, entry)

    def _activate(self, day: object, entry: object) -> None:
        """A double-click or Return in a grid: an event opens (Google's in Google
        Calendar), and an empty day asks for a new event on it."""
        if not isinstance(day, date):
            return
        self._choose(day, entry)
        if entry is None:
            if self._shown is not None:
                self.event_requested.emit(NEW, day)
        elif isinstance(entry, CalendarEvent | GoogleEvent | ScheduledBlock):
            self._preview_selected()

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
        rather than another view's: a week drawn as a month's grid, a day as a week, or the
        reverse."""
        kind = range_kind(self.view())
        if self._shown is not None and kind != self._shown_kind:
            grids: dict[str, WeekGrid | MonthGrid] = {
                "day": self.day_grid,
                "week": self.week_grid,
                "month": self.month_grid,
            }
            grids[kind].clear()
        self._show_view()

    def _show_view(self) -> None:
        view = self.view()
        pages: dict[CalendarView, QWidget] = {
            CalendarView.DAY: self.day_grid,
            CalendarView.WEEK: self.week_grid,
            CalendarView.MONTH: self.month_grid,
        }
        page = pages.get(view)
        self.stack.setCurrentIndex(2 if page is None else self.stack.indexOf(page))
        steps = (
            _MONTH_STEPS
            if view is CalendarView.MONTH
            else _DAY_STEPS
            if view is CalendarView.DAY
            else _WEEK_STEPS
        )
        for button, text in zip(
            (self.previous_button, self.this_week_button, self.next_button), steps, strict=True
        ):
            button.setText(
                "Today"
                if button is self.this_week_button
                else "\u2039"
                if button is self.previous_button
                else "\u203a"
            )
            button.setAccessibleName(text.replace("&", ""))
            button.setFixedWidth(52 if button is self.this_week_button else 30)
        self._show_heading()
        self._show_tasks()
        self._show_free_line()

    def _show_heading(self) -> None:
        shown, today = self._shown, self._today
        if shown is None or today is None:
            self.heading.setText("Calendar")
        elif self._shown_month:
            self.heading.setText(f"Calendar · {month_title(shown_month(shown.first_day))}")
        elif self._shown_kind == "day":
            self.heading.setText(f"Calendar · {full_day_text(shown.first_day, today=today)}")
        else:
            self.heading.setText(f"Calendar · {week_title(shown.first_day, today)}")

    def _show_free_line(self) -> None:
        """How much working time the days shown leave free, in the Day and Week views,
        once the view's own days are read."""
        view, shown = self.view(), self._shown
        fits = shown is not None and range_kind(view) == self._shown_kind
        text = free_text(shown, self._zone, view) if shown is not None and fits else ""
        self.free_line.setText(text)
        self.free_line.setVisible(bool(text))

    # The tasks to schedule (ADR 0029).

    def show_unscheduled(self, found: UnscheduledTasks, today: date) -> None:
        self.unscheduled.show_tasks(found, today)
        self._tasks_total = found.total
        self._show_tasks()

    def tasks_shown(self) -> bool:
        return self._tasks_shown

    def set_tasks_shown(self, shown: bool) -> None:
        """Show or hide the tasks to schedule; the window keeps the choice."""
        if shown != self._tasks_shown:
            self._tasks_shown = shown
            self._show_tasks()
            self.tasks_toggled.emit(shown)

    def _show_tasks(self) -> None:
        hours = has_hours(self.view())
        self.unscheduled.setVisible(self._tasks_shown and hours)
        self.tasks_button.setVisible(hours)
        if self._tasks_shown:
            text = "Hide tasks to schedule"
        elif self._tasks_total:
            text = f"Tasks to schedule · {self._tasks_total}"
        else:
            text = "Tasks to schedule"
        self.tasks_button.setText(text)
        self.tasks_button.setAccessibleName(text)

    def hours_grid(self) -> WeekGrid | None:
        """The grid of hours shown, if any: the Day or Week view's."""
        view = self.view()
        if view is CalendarView.DAY:
            return self.day_grid
        return self.week_grid if view is CalendarView.WEEK else None

    def _task_dragged(self, task: object, screen: object) -> None:
        grid = self.hours_grid()
        if grid is None or not isinstance(task, UnscheduledTask):
            return
        place = grid.placement_at(screen) if isinstance(screen, QPointF) else None
        if place is None:
            grid.clear_placement()
        else:
            grid.show_placement(place[0], place[1], task.suggested_minutes, task.title)

    def _task_dropped(self, task: object, screen: object) -> None:
        grid = self.hours_grid()
        if grid is None or not isinstance(task, UnscheduledTask):
            return
        place = grid.placement_at(screen) if isinstance(screen, QPointF) else None
        if place is not None:
            self.task_place_requested.emit(task, place[0], place[1])

    def _task_drag_ended(self) -> None:
        for grid in (self.week_grid, self.day_grid):
            grid.clear_placement()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        shown = self._shown is not None
        chosen = self.selected() is not None
        for button in (self.previous_button, self.this_week_button, self.next_button):
            button.setEnabled(shown)
        self.new_button.setEnabled(shown)
        self.schedule_button.setEnabled(shown)
        self.plan_button.setEnabled(shown)
        self.open_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen and not self._busy)  # A change waits its turn.
        linked = self.selected_google()
        self.open_google_button.setEnabled(linked is not None and linked.web_link is not None)
        for button in (self.allow_button, self.refresh_button):
            button.setEnabled(not self._busy)

    def _request_period(self, step: int) -> None:
        """The day, week or month ``step`` before (negative) or after the one shown."""
        shown = self._shown
        if shown is None:
            return
        if self.view() is CalendarView.MONTH:
            self.month_requested.emit(add_months(shown_month(shown.first_day), step))
        elif self.view() is CalendarView.DAY:
            day = self.focus_day() if self._shown_kind == "day" else None
            self.day_requested.emit((day or shown.first_day) + timedelta(days=step))
        else:
            self.week_requested.emit(shown.first_day + timedelta(days=WEEK_DAYS * step))

    def _request_this_period(self) -> None:
        if self._today is None:
            return
        if self.view() is CalendarView.MONTH:
            self.month_requested.emit(month_start(self._today))
        elif self.view() is CalendarView.DAY:
            self.day_requested.emit(self._today)
        else:
            self.week_requested.emit(week_start(self._today))

    def _request_new(self) -> None:
        """A new event starts on the day in focus (``focus_day``): the day selected, else
        today when shown, else the first day shown (the month's 1st in a month's grid)."""
        day = self.focus_day()
        if day is not None:
            self.event_requested.emit(NEW, day)

    def _request_schedule(self) -> None:
        """Time for a task starts on the day in focus, as a new event does."""
        day = self.focus_day()
        if day is not None:
            self.block_requested.emit(NEW_BLOCK, day)

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
