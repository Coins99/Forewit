"""The Calendar's grids, as calendar apps show days: ``WeekGrid`` (a week of hours) and
``MonthGrid`` (six weeks of days around a month).

They only show and report: ``chosen(day, entry)`` when a day or an entry is clicked or
reached by the keyboard, ``activated(day, entry)`` on a double-click or Return (an entry
opens; an empty day asks for a new event) and ``more_requested(day)`` from "+N more". The
Calendar page keeps the selection and gives it back with ``set_selection``.

Titles are the owner's text, Google's or (for the actions' dates) mail-derived, so they are
drawn with ``drawText`` only: never rich text, a link or a tooltip. Colours are the theme's
proven pairs: the owner's events in accent on its tint, Google's in text on a card, the
actions' dates in warning on its tint, today in white on the primary fill.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.ui.calendar_layout import (
    WEEK_DAYS,
    Bar,
    Entry,
    Part,
    bars,
    clock,
    day_parts,
    entry_key,
    is_bar,
    marker_text,
    pack_lanes,
    shown_month,
)
from mailbrief.ui.deadline_text import WEEKDAYS
from mailbrief.ui.hairline import hairline_pen
from mailbrief.ui.theme import (
    CAPTION_PX,
    SMALL_PX,
    Tokens,
    current_tokens,
    ui_font,
    ui_metrics,
)

Key = tuple[str, ...]

# The week grid (logical px).
GUTTER: Final = 52  # The hours' labels.
HOUR_PX: Final = 48
HOURS_PAD: Final = 8  # Above 00:00 and below 24:00, so their labels show whole.
HEADER_H: Final = 52
TODAY_DIAMETER: Final = 26
LANE_H: Final = 20
LANE_GAP: Final = 2
STRIP_PAD: Final = 4
STRIP_LANES: Final = 3  # Lanes shown at most; with more, the last says "+N more".
SCROLL_TO_HOUR: Final = 7.5  # Where a week first opens, unless something starts earlier.
BLOCK_RADIUS: Final = 5
EDGE_W: Final = 3
# The month grid.
MONTH_HEADER_H: Final = 24
TILE_GAP: Final = 2  # Each side of a day's tile.
TILE_RADIUS: Final = 8
NUMBER_H: Final = 18
LINE_H: Final = 16
LINE_GAP: Final = 1
DOT_PX: Final = 6
RING_W: Final = 1.5
_ALIGN_LEFT: Final = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) | int(
    Qt.TextFlag.TextSingleLine
)
_ALIGN_RIGHT: Final = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) | int(
    Qt.TextFlag.TextSingleLine
)
_ALIGN_CENTER: Final = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextSingleLine)


def entry_style(entry: Entry, tokens: Tokens) -> tuple[str, str, str]:
    """An entry's fill, text and edge colours: the owner's events in accent on its tint,
    Google's in text on a card, the actions' dates in warning on its tint."""
    if isinstance(entry, ActionMarker):
        return tokens.warning_bg, tokens.warning_fg, tokens.warning_fg
    if isinstance(entry, GoogleEvent):
        return tokens.card, tokens.text, tokens.border_strong
    return tokens.accent_bg, tokens.accent_fg, tokens.accent_fg


def entry_label(entry: Entry, zone: ZoneInfo) -> str:
    """What an entry's bar or block says: an event's title, or the action's date in words."""
    return marker_text(entry, zone) if isinstance(entry, ActionMarker) else entry.title


def more_text(count: int) -> str:
    return f"+{count} more"


def _elided(text: str, metrics: QFontMetrics, width: float) -> str:
    flat = " ".join(text.split())
    return metrics.elidedText(flat, Qt.TextElideMode.ElideRight, max(0, int(width)))


def paint_ring(painter: QPainter, rect: QRectF, radius: float, color: str) -> None:
    """The selection's ring just outside ``rect``."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), RING_W))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outer = rect.adjusted(-RING_W / 2, -RING_W / 2, RING_W / 2, RING_W / 2)
    painter.drawRoundedRect(outer, radius + RING_W / 2, radius + RING_W / 2)
    painter.restore()


def paint_bar(
    painter: QPainter, rect: QRectF, entry: Entry, zone: ZoneInfo, tokens: Tokens, *, px: int
) -> None:
    """A filled, rounded bar with a coloured edge at its left and the entry's label."""
    fill, text, edge = entry_style(entry, tokens)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    radius = min(BLOCK_RADIUS, rect.height() / 2)
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, QColor(fill))
    painter.setClipPath(path)
    painter.fillRect(QRectF(rect.left(), rect.top(), EDGE_W, rect.height()), QColor(edge))
    painter.setClipping(False)
    metrics = ui_metrics(px, semibold=True)
    painter.setFont(ui_font(px, semibold=True))
    painter.setPen(QColor(text))
    area = rect.adjusted(EDGE_W + 5, 0, -4, 0)
    painter.drawText(area, _ALIGN_LEFT, _elided(entry_label(entry, zone), metrics, area.width()))
    painter.restore()


@dataclass(frozen=True)
class Hit:
    """Something painted that a click can land on: an entry on a day, or "+N more"."""

    rect: QRectF
    day: date
    entry: Entry | None = None
    more: bool = False
    lane: int = 0  # In the month grid, the item's lane in its week.
    last_day: date | None = None  # A bar merged across days: its last day.


class _Grid(QWidget):
    chosen = Signal(object, object)
    activated = Signal(object, object)
    more_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shown: CalendarRange | None = None
        self._zone = ZoneInfo("UTC")
        self._today: date | None = None
        self._day: date | None = None
        self._key: Key | None = None
        self.clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def show_range(self, shown: CalendarRange, zone: ZoneInfo, today: date) -> None:
        self._shown, self._zone, self._today = shown, zone, today
        self.relayout()

    def set_selection(self, day: date | None, key: Key | None) -> None:
        """Show ``day`` and the entry ``key`` on it as selected (None for neither)."""
        self._day, self._key = day, key
        self.relayout()

    def selection(self) -> tuple[date | None, Key | None]:
        return self._day, self._key

    def relayout(self) -> None:
        self.update()

    def _choose(self, hit: Hit | None, day: date | None) -> None:
        if hit is not None and hit.more:
            self.more_requested.emit(hit.day)
        elif hit is not None:
            self.chosen.emit(hit.day, hit.entry)
        elif day is not None:
            self.chosen.emit(day, None)

    def _activate(self, hit: Hit | None, day: date | None) -> None:
        if hit is not None and not hit.more:
            self.activated.emit(hit.day, hit.entry)
        elif hit is None and day is not None:
            self.activated.emit(day, None)


class _Part(QWidget):
    """A painted part of the week grid; it paints and hits through the grid."""

    def __init__(self, grid: "WeekGrid", name: str) -> None:
        super().__init__()
        self.grid = grid
        self.setObjectName(name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        self.grid.paint_part(self, painter)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.grid.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        self.grid._choose(self.grid.hit(self, point), self.grid.day_at(point.x()))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        self.grid._activate(self.grid.hit(self, point), self.grid.day_at(point.x()))


class WeekGrid(_Grid):
    """A week of hours: the days' heads, a strip of bars (all-day events, events of a day or
    more and the actions' dates) and the hours, scrolling, with timed events as blocks in
    them. Today's column is shaded and carries the time now as a line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calendarWeekGrid")
        self.setAccessibleName("Calendar week")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = _Part(self, "calendarWeekHeader")
        self.header.setFixedHeight(HEADER_H)
        layout.addWidget(self.header)
        self.strip = _Part(self, "calendarAllDay")
        layout.addWidget(self.strip)
        self.hours = _Part(self, "calendarHours")
        self.hours.setFixedHeight(2 * HOURS_PAD + 24 * HOUR_PX)
        self.hours_area = QScrollArea()
        self.hours_area.setObjectName("calendarHoursScroll")
        self.hours_area.setFrameShape(QFrame.Shape.NoFrame)
        self.hours_area.setWidgetResizable(True)
        self.hours_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hours_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hours_area.setWidget(self.hours)
        layout.addWidget(self.hours_area, 1)
        self._bars: list[Bar] = []
        self._lanes = 0
        self._placed = False  # Whether the hours were first scrolled for a week shown.
        # The line for the time now moves each minute while the grid shows.
        self.minute = QTimer(self)
        self.minute.setInterval(60_000)
        self.minute.timeout.connect(self.hours.update)

    # Geometry. The heads and the strip line their columns up with the hours, whose
    # scroll bar takes room at the right.

    def days(self) -> list[date]:
        if self._shown is None:
            return []
        return [self._shown.first_day + timedelta(days=offset) for offset in range(WEEK_DAYS)]

    def content_width(self) -> int:
        return self.hours_area.viewport().width()

    def column_width(self) -> float:
        return max(0.0, (self.content_width() - GUTTER) / WEEK_DAYS)

    def column_left(self, index: int) -> float:
        return GUTTER + index * self.column_width()

    def day_at(self, x: float) -> date | None:
        days = self.days()
        width = self.column_width()
        if not days or width <= 0 or x < GUTTER or x >= GUTTER + WEEK_DAYS * width:
            return None
        return days[min(WEEK_DAYS - 1, int((x - GUTTER) // width))]

    def relayout(self) -> None:
        shown = self._shown
        if shown is None:
            self._bars, self._lanes = [], 0
        else:
            last = shown.first_day + timedelta(days=WEEK_DAYS - 1)
            self._bars = pack_lanes(bars(shown, shown.first_day, last, self._zone))
            self._lanes = 1 + max((bar.lane for bar in self._bars), default=-1)
        rows = min(self._lanes, STRIP_LANES)
        self.strip.setFixedHeight(rows * (LANE_H + LANE_GAP) + 2 * STRIP_PAD if rows else 0)
        self.strip.setVisible(bool(rows))
        self._place_hours()
        self.update_parts()

    def update_parts(self) -> None:
        for part in (self.header, self.strip, self.hours):
            part.update()
        self.setAccessibleDescription(self._described())

    def _described(self) -> str:
        """The selection in words, for screen readers."""
        if self._day is None:
            return ""
        text = f"{WEEKDAYS[self._day.weekday()]} {self._day.isoformat()}"
        entry = self._selected_entry()
        return text if entry is None else f"{text}: {entry_label(entry, self._zone)}"

    def _selected_entry(self) -> Entry | None:
        if self._key is None or self._shown is None:
            return None
        shown = self._shown
        entries: list[Entry] = [*shown.events, *shown.google_events, *shown.markers]
        return next((entry for entry in entries if entry_key(entry) == self._key), None)

    def _place_hours(self) -> None:
        """The first time a week shows, open its hours at 07:30, or earlier when something
        starts earlier; later weeks keep where the owner scrolled."""
        if self._placed or self._shown is None or not self.isVisible():
            return
        starts = [
            part.start
            for day in self.days()
            for part in day_parts(self._shown, day, self._zone)
            if part.start > 0
        ]
        hour = min([SCROLL_TO_HOUR, *(start / 60 - 0.5 for start in starts)])
        self.hours_area.verticalScrollBar().setValue(max(0, round(hour * HOUR_PX)))
        self._placed = True

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.minute.start()
        self._place_hours()

    def hideEvent(self, event: QHideEvent) -> None:
        self.minute.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_parts()

    # What there is to hit, by part.

    def hits(self, part: QWidget) -> list[Hit]:
        if part is self.strip:
            return self._strip_hits()
        if part is self.hours:
            return self._hour_hits()
        return []

    def hit(self, part: QWidget, point: QPointF) -> Hit | None:
        """The topmost thing painted under ``point`` in ``part``, if any."""
        return next((hit for hit in reversed(self.hits(part)) if hit.rect.contains(point)), None)

    def _strip_hits(self) -> list[Hit]:
        days = self.days()
        shown_lanes = self._lanes if self._lanes <= STRIP_LANES else STRIP_LANES - 1
        found: list[Hit] = []
        width = self.column_width()
        for bar in self._bars:
            if bar.lane >= shown_lanes:
                continue
            top = STRIP_PAD + bar.lane * (LANE_H + LANE_GAP)
            left = self.column_left(bar.first) + 2
            right = self.column_left(bar.last + 1) - 2
            rect = QRectF(left, top, max(4.0, right - left), LANE_H)
            # The bar is hit as the day under the pointer, so split it by day.
            for index in range(bar.first, bar.last + 1):
                piece = rect.intersected(QRectF(self.column_left(index), top, width, LANE_H))
                found.append(Hit(piece, days[index], bar.entry))
        if self._lanes > STRIP_LANES:
            top = STRIP_PAD + shown_lanes * (LANE_H + LANE_GAP)
            for index, day in enumerate(days):
                hidden = sum(
                    1
                    for bar in self._bars
                    if bar.lane >= shown_lanes and bar.first <= index <= bar.last
                )
                if hidden:
                    rect = QRectF(self.column_left(index) + 2, top, width - 4, LANE_H)
                    found.append(Hit(rect, day, more=True))
        return found

    def block_rect(self, part: Part, index: int) -> QRectF:
        """Where a timed block sits: in its day's column, its share of the width beside the
        blocks it overlaps, from its start to its end."""
        inner = self.column_width() - 4
        share = inner / part.columns
        left = self.column_left(index) + 2 + part.column * share
        top = HOURS_PAD + part.start * HOUR_PX / 60 + 1
        height = (part.end - part.start) * HOUR_PX / 60 - 2
        return QRectF(left, top, max(4.0, share - 2), max(8.0, height))

    def _hour_hits(self) -> list[Hit]:
        if self._shown is None:
            return []
        return [
            Hit(self.block_rect(part, index), day, part.event)
            for index, day in enumerate(self.days())
            for part in day_parts(self._shown, day, self._zone)
        ]

    # Painting.

    def paint_part(self, part: QWidget, painter: QPainter) -> None:
        tokens = current_tokens()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if part is self.header:
            self._paint_header(painter, tokens)
        elif part is self.strip:
            self._paint_strip(painter, tokens)
        else:
            self._paint_hours(painter, tokens)

    def _paint_header(self, painter: QPainter, tokens: Tokens) -> None:
        width = self.column_width()
        for index, day in enumerate(self.days()):
            left = self.column_left(index)
            painter.setFont(ui_font(CAPTION_PX, semibold=True))
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(QRectF(left, 6, width, 14), _ALIGN_CENTER, WEEKDAYS[day.weekday()])
            centre = QPointF(left + width / 2, 6 + 14 + 4 + TODAY_DIAMETER / 2)
            radius = TODAY_DIAMETER / 2
            circle = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
            color = tokens.text
            if day == self._today:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.primary_fill))
                painter.drawEllipse(circle)
                color = tokens.on_primary
            elif day == self._day:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.selection))
                painter.drawEllipse(circle)
            painter.setFont(ui_font(SMALL_PX + 2, semibold=True, tabular=True))
            painter.setPen(QColor(color))
            painter.drawText(circle, _ALIGN_CENTER, str(day.day))

    def _paint_strip(self, painter: QPainter, tokens: Tokens) -> None:
        painter.setFont(ui_font(CAPTION_PX))
        painter.setPen(QColor(tokens.text_secondary))
        painter.drawText(QRectF(0, STRIP_PAD, GUTTER - 8, LANE_H), _ALIGN_RIGHT, "all-day")
        drawn: set[tuple[Key, int]] = set()
        for hit in self._strip_hits():
            if hit.more:
                painter.setFont(ui_font(CAPTION_PX, semibold=True))
                painter.setPen(QColor(tokens.text_secondary))
                hidden = self._hidden_on(hit.day)
                painter.drawText(hit.rect.adjusted(6, 0, 0, 0), _ALIGN_LEFT, more_text(hidden))
                continue
            assert hit.entry is not None
            bar = next(bar for bar in self._bars if bar.entry is hit.entry)
            key = (entry_key(hit.entry), bar.lane)
            if key in drawn:
                continue  # A bar is painted once, whole; its pieces are only for hitting.
            drawn.add(key)
            top = STRIP_PAD + bar.lane * (LANE_H + LANE_GAP)
            left = self.column_left(bar.first) + 2
            right = self.column_left(bar.last + 1) - 2
            rect = QRectF(left, top, max(4.0, right - left), LANE_H)
            paint_bar(painter, rect, bar.entry, self._zone, tokens, px=SMALL_PX)
            if entry_key(bar.entry) == self._key:
                paint_ring(painter, rect, BLOCK_RADIUS, tokens.accent_fg)

    def _hidden_on(self, day: date) -> int:
        days = self.days()
        if day not in days:
            return 0
        index = days.index(day)
        return sum(
            1
            for bar in self._bars
            if bar.lane >= STRIP_LANES - 1 and bar.first <= index <= bar.last
        )

    def _paint_hours(self, painter: QPainter, tokens: Tokens) -> None:
        width = self.column_width()
        right = GUTTER + WEEK_DAYS * width
        days = self.days()
        if self._today in days:
            index = days.index(self._today)
            painter.fillRect(
                QRectF(self.column_left(index), 0, width, self.hours.height()), QColor(tokens.card)
            )
        painter.setFont(ui_font(CAPTION_PX, tabular=True))
        for hour in range(25):
            y = HOURS_PAD + hour * HOUR_PX
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(hairline_pen(tokens.hairline))
            painter.drawLine(QPointF(GUTTER, y), QPointF(right, y))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if hour < 24:
                painter.setPen(QColor(tokens.text_secondary))
                painter.drawText(QRectF(0, y - 8, GUTTER - 8, 16), _ALIGN_RIGHT, f"{hour:02d}:00")
        if self._shown is not None:
            for index, day in enumerate(days):
                for part in day_parts(self._shown, day, self._zone):
                    self._paint_block(painter, self.block_rect(part, index), part, tokens)
        if self._today in days:
            self._paint_now(painter, days.index(self._today), tokens)

    def _paint_block(self, painter: QPainter, rect: QRectF, part: Part, tokens: Tokens) -> None:
        event = part.event
        fill, text, edge = entry_style(event, tokens)
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, BLOCK_RADIUS, BLOCK_RADIUS)
        painter.fillPath(path, QColor(fill))
        painter.setClipPath(path)
        painter.fillRect(QRectF(rect.left(), rect.top(), EDGE_W, rect.height()), QColor(edge))
        area = rect.adjusted(EDGE_W + 4, 2, -3, -2)
        title = ui_metrics(SMALL_PX, semibold=True)
        painter.setFont(ui_font(SMALL_PX, semibold=True))
        painter.setPen(QColor(text))
        line = QRectF(area.left(), area.top(), area.width(), title.height())
        painter.drawText(line, _ALIGN_LEFT, _elided(event.title, title, area.width()))
        assert event.starts_at_utc is not None and event.ends_at_utc is not None
        when_metrics = ui_metrics(CAPTION_PX, tabular=True)
        if area.height() >= title.height() + when_metrics.height():
            when = (
                f"{clock(event.starts_at_utc, self._zone)}–{clock(event.ends_at_utc, self._zone)}"
            )
            painter.setFont(ui_font(CAPTION_PX, tabular=True))
            # Google's text is secondary on its card; the owner's stays accent on its tint.
            secondary = tokens.text_secondary if isinstance(event, GoogleEvent) else text
            painter.setPen(QColor(secondary))
            below = QRectF(area.left(), line.bottom(), area.width(), when_metrics.height())
            painter.drawText(below, _ALIGN_LEFT, _elided(when, when_metrics, area.width()))
        painter.restore()
        if entry_key(event) == self._key and part.day == self._day:
            paint_ring(painter, rect, BLOCK_RADIUS, tokens.accent_fg)

    def _paint_now(self, painter: QPainter, index: int, tokens: Tokens) -> None:
        now = self.clock().astimezone(self._zone)
        y = HOURS_PAD + (now.hour * 60 + now.minute) * HOUR_PX / 60
        left = self.column_left(index)
        painter.save()
        painter.setPen(QPen(QColor(tokens.accent_fg), 1.5))
        painter.drawLine(QPointF(left, y), QPointF(left + self.column_width(), y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.accent_fg))
        painter.drawEllipse(QPointF(left, y), DOT_PX / 2, DOT_PX / 2)
        painter.restore()

    # The keyboard: Left and Right move by a day, Up and Down through the day's entries,
    # Return opens the entry (or asks for a new event on an empty day), and Page Up and
    # Page Down scroll the hours.

    def day_entries(self, day: date) -> list[Entry]:
        """The day's entries in the order Up and Down visit them: its bars by lane, then
        its blocks by start."""
        days = self.days()
        if self._shown is None or day not in days:
            return []
        index = days.index(day)
        across = sorted(
            (bar for bar in self._bars if bar.first <= index <= bar.last), key=lambda bar: bar.lane
        )
        timed = sorted(day_parts(self._shown, day, self._zone), key=lambda part: part.start)
        return [*(bar.entry for bar in across), *(part.event for part in timed)]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        days = self.days()
        key = event.key()
        if not days:
            super().keyPressEvent(event)
            return
        day = self._day if self._day in days else (self._today if self._today in days else days[0])
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if key == Qt.Key.Key_Left else 1
            index = min(len(days) - 1, max(0, days.index(day) + step))
            self.chosen.emit(days[index], None)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._step_entry(day, -1 if key == Qt.Key.Key_Up else 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(day, self._entry_on(day))
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            action = (
                self.hours_area.verticalScrollBar().SliderAction.SliderPageStepSub
                if key == Qt.Key.Key_PageUp
                else self.hours_area.verticalScrollBar().SliderAction.SliderPageStepAdd
            )
            self.hours_area.verticalScrollBar().triggerAction(action)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _entry_on(self, day: date) -> Entry | None:
        return next(
            (entry for entry in self.day_entries(day) if entry_key(entry) == self._key), None
        )

    def _step_entry(self, day: date, step: int) -> None:
        entries = self.day_entries(day)
        if not entries:
            self.chosen.emit(day, None)
            return
        keys = [entry_key(entry) for entry in entries]
        if self._key in keys:
            index = min(len(entries) - 1, max(0, keys.index(self._key) + step))
        else:
            index = 0 if step > 0 else len(entries) - 1
        self.chosen.emit(day, entries[index])

    def sizeHint(self) -> QSize:
        return QSize(640, HEADER_H + 12 * HOUR_PX)


@dataclass(frozen=True)
class _Cell:
    day: date
    rect: QRectF  # The day's tile.


class MonthGrid(_Grid):
    """Six weeks of days around a month, as calendar apps show a month: a tile for each
    day, the month's own days filled; events of a day or more and the actions' dates as
    bars across their days, timed events as a line each, and "+N more" when a day holds more
    than its tile shows. Page Up and Page Down ask for the month before and after."""

    period_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calendarMonthGrid")
        self.setAccessibleName("Calendar month")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(MONTH_HEADER_H + 6 * (NUMBER_H + LINE_H + 2 * TILE_GAP + 4))

    def first_day(self) -> date | None:
        return None if self._shown is None else self._shown.first_day

    def month(self) -> date | None:
        """The 1st of the month shown."""
        return None if self._shown is None else shown_month(self._shown.first_day)

    def cells(self) -> list[_Cell]:
        first = self.first_day()
        if first is None:
            return []
        width = self.width() / WEEK_DAYS
        height = (self.height() - MONTH_HEADER_H) / 6
        found: list[_Cell] = []
        for number in range(6 * WEEK_DAYS):
            row, column = divmod(number, WEEK_DAYS)
            rect = QRectF(column * width, MONTH_HEADER_H + row * height, width, height)
            rect = rect.adjusted(TILE_GAP, TILE_GAP, -TILE_GAP, -TILE_GAP)
            found.append(_Cell(first + timedelta(days=number), rect))
        return found

    def day_at(self, x: float, y: float | None = None) -> date | None:
        point = QPointF(x, 0.0 if y is None else y)
        width = self.width() / WEEK_DAYS
        height = (self.height() - MONTH_HEADER_H) / 6
        first = self.first_day()
        if first is None or point.y() < MONTH_HEADER_H or width <= 0 or height <= 0:
            return None
        row = min(5, int((point.y() - MONTH_HEADER_H) // height))
        column = min(WEEK_DAYS - 1, max(0, int(point.x() // width)))
        return first + timedelta(days=row * WEEK_DAYS + column)

    def capacity(self) -> int:
        """How many lines a day's tile holds below its number."""
        cells = self.cells()
        if not cells:
            return 0
        room = cells[0].rect.height() - NUMBER_H - 2
        return max(0, int((room + LINE_GAP) // (LINE_H + LINE_GAP)))

    def _week_items(self, row: int) -> list[Bar]:
        """One week's items in lanes: bars, then a line for each timed event on each day."""
        first = self.first_day()
        if first is None or self._shown is None:
            return []
        start = first + timedelta(days=row * WEEK_DAYS)
        end = start + timedelta(days=WEEK_DAYS - 1)
        found = bars(self._shown, start, end, self._zone)
        events: list[CalendarEvent | GoogleEvent] = [
            *self._shown.events,
            *self._shown.google_events,
        ]
        timed = sorted(
            (event for event in events if not is_bar(event)),
            key=lambda event: (event.starts_at_utc, isinstance(event, GoogleEvent)),
        )
        for event in timed:
            touched_first, touched_last = event.days(self._zone)
            for offset in range(WEEK_DAYS):
                day = start + timedelta(days=offset)
                if touched_first <= day <= touched_last:
                    found.append(Bar(event, offset, offset))
        return pack_lanes(found)

    def hits(self) -> list[Hit]:
        """Every painted item and "+N more", in the order painted."""
        cells = self.cells()
        capacity = self.capacity()
        found: list[Hit] = []
        for row in range(6):
            items = self._week_items(row)
            week = cells[row * WEEK_DAYS : (row + 1) * WEEK_DAYS]
            for offset, cell in enumerate(week):
                here = [item for item in items if item.first <= offset <= item.last]
                count = len(here)
                shown_lanes = capacity if count <= capacity else max(0, capacity - 1)
                for item in here:
                    if item.lane < shown_lanes:
                        rect = self._line_rect(cell, item.lane)
                        found.append(Hit(rect, cell.day, item.entry, lane=item.lane))
                if count > shown_lanes and capacity:
                    rect = self._line_rect(cell, shown_lanes)
                    found.append(Hit(rect, cell.day, more=True))
        return found

    def _line_rect(self, cell: _Cell, lane: int) -> QRectF:
        top = cell.rect.top() + NUMBER_H + 1 + lane * (LINE_H + LINE_GAP)
        return QRectF(cell.rect.left() + 3, top, cell.rect.width() - 6, LINE_H)

    def hit(self, point: QPointF) -> Hit | None:
        return next((hit for hit in reversed(self.hits()) if hit.rect.contains(point)), None)

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width() / WEEK_DAYS
        painter.setFont(ui_font(CAPTION_PX, semibold=True))
        painter.setPen(QColor(tokens.text_secondary))
        for column, name in enumerate(WEEKDAYS):
            painter.drawText(QRectF(column * width, 0, width, MONTH_HEADER_H), _ALIGN_CENTER, name)
        month = self.month()
        for cell in self.cells():
            self._paint_tile(painter, cell, month, tokens)
        for hit in self._merged(self.hits()):
            self._paint_hit(painter, hit, tokens)
        painter.end()

    def _merged(self, hits: list[Hit]) -> list[Hit]:
        """The hits to paint: a bar's pieces on neighbouring days of one week, in one lane,
        become one bar across them, as calendar apps draw an event of several days."""
        first = self.first_day()
        merged: list[Hit] = []
        open_runs: dict[tuple[int, int, Key], int] = {}  # Where each run is in ``merged``.
        for hit in hits:
            entry = hit.entry
            if (
                first is None
                or entry is None
                or hit.more
                or not (isinstance(entry, ActionMarker) or is_bar(entry))
            ):
                merged.append(hit)
                continue
            row = (hit.day - first).days // WEEK_DAYS
            run = (row, hit.lane, entry_key(entry))
            index = open_runs.get(run)
            if index is not None and self._touches(merged[index], hit):
                before = merged[index]
                rect = QRectF(before.rect.topLeft(), hit.rect.bottomRight())
                merged[index] = Hit(rect, before.day, entry, lane=hit.lane, last_day=hit.day)
            else:
                open_runs[run] = len(merged)
                merged.append(hit)
        return merged

    @staticmethod
    def _touches(run: Hit, hit: Hit) -> bool:
        """Whether ``hit`` is on the day right after the run's last: the next tile along."""
        gap = hit.rect.left() - run.rect.right()
        return 0 <= gap <= 2 * TILE_GAP + 6 + 1

    def _paint_tile(
        self, painter: QPainter, cell: _Cell, month: date | None, tokens: Tokens
    ) -> None:
        inside = month is not None and (cell.day.year, cell.day.month) == (
            month.year,
            month.month,
        )
        if inside:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.card))
            painter.drawRoundedRect(cell.rect, TILE_RADIUS, TILE_RADIUS)
        number = QRectF(cell.rect.left() + 4, cell.rect.top() + 2, NUMBER_H, NUMBER_H - 2)
        color = tokens.text if inside else tokens.text_muted
        if cell.day == self._today:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.primary_fill))
            painter.drawEllipse(number)
            color = tokens.on_primary
        painter.setFont(ui_font(SMALL_PX, semibold=True, tabular=True))
        painter.setPen(QColor(color))
        painter.drawText(number, _ALIGN_CENTER, str(cell.day.day))
        if cell.day == self._day and self._key is None:
            paint_ring(painter, cell.rect, TILE_RADIUS, tokens.accent_fg)

    def _paint_hit(self, painter: QPainter, hit: Hit, tokens: Tokens) -> None:
        if hit.more:
            painter.setFont(ui_font(CAPTION_PX, semibold=True))
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(
                hit.rect.adjusted(4, 0, 0, 0), _ALIGN_LEFT, more_text(self._more_on(hit))
            )
            return
        entry = hit.entry
        assert entry is not None
        if isinstance(entry, ActionMarker) or is_bar(entry):
            paint_bar(painter, hit.rect, entry, self._zone, tokens, px=CAPTION_PX)
        else:
            self._paint_line(painter, hit, entry, tokens)
        last = hit.last_day or hit.day
        if entry_key(entry) == self._key and self._day is not None and hit.day <= self._day <= last:
            paint_ring(painter, hit.rect, BLOCK_RADIUS, tokens.accent_fg)

    def _more_on(self, hit: Hit) -> int:
        """How many of the day's items "+N more" stands for."""
        cells = self.cells()
        index = next(number for number, cell in enumerate(cells) if cell.day == hit.day)
        row, offset = divmod(index, WEEK_DAYS)
        here = [item for item in self._week_items(row) if item.first <= offset <= item.last]
        capacity = self.capacity()
        return len(here) - max(0, capacity - 1)

    def _paint_line(
        self, painter: QPainter, hit: Hit, event: CalendarEvent | GoogleEvent, tokens: Tokens
    ) -> None:
        """A timed event as one line: a dot in its colour, its start (on its first day) and
        its title."""
        _fill, _text, edge = entry_style(event, tokens)
        rect = hit.rect
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(edge))
        painter.drawEllipse(QPointF(rect.left() + 4, rect.center().y()), DOT_PX / 2, DOT_PX / 2)
        assert event.starts_at_utc is not None
        start = event.starts_at_utc.astimezone(self._zone)
        left = rect.left() + 4 + DOT_PX
        if start.date() == hit.day:
            when = clock(event.starts_at_utc, self._zone)
            metrics = ui_metrics(CAPTION_PX, tabular=True)
            painter.setFont(ui_font(CAPTION_PX, tabular=True))
            painter.setPen(QColor(tokens.text_secondary))
            area = QRectF(left, rect.top(), rect.right() - left, rect.height())
            painter.drawText(area, _ALIGN_LEFT, when)
            left += metrics.horizontalAdvance(when) + 4
        title = ui_metrics(CAPTION_PX, semibold=True)
        painter.setFont(ui_font(CAPTION_PX, semibold=True))
        painter.setPen(QColor(tokens.text))
        area = QRectF(left, rect.top(), max(0.0, rect.right() - left), rect.height())
        painter.drawText(area, _ALIGN_LEFT, _elided(event.title, title, area.width()))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        self._choose(self.hit(point), self.day_at(point.x(), point.y()))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        self._activate(self.hit(point), self.day_at(point.x(), point.y()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        first = self.first_day()
        key = event.key()
        steps = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -WEEK_DAYS,
            Qt.Key.Key_Down: WEEK_DAYS,
        }
        if first is None:
            super().keyPressEvent(event)
            return
        last = first + timedelta(days=6 * WEEK_DAYS - 1)
        day = self._day if self._day is not None and first <= self._day <= last else None
        if day is None:
            day = self._today if self._today is not None and first <= self._today <= last else first
        if key in steps:
            moved = day + timedelta(days=steps[Qt.Key(key)])
            self.chosen.emit(min(last, max(first, moved)), None)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            entry = next(
                (
                    hit.entry
                    for hit in self.hits()
                    if hit.day == day
                    and hit.entry is not None
                    and entry_key(hit.entry) == self._key
                ),
                None,
            )
            self.activated.emit(day, entry)
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.period_requested.emit(-1 if key == Qt.Key.Key_PageUp else 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(640, 480)
