"""The three-pane workspace: header, sidebar, pages, brief list and detail pane."""

import math
import weakref
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractAnimation,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QRegion,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import ActionProposal, ThreadLink
from mailbrief.domain.digests import DailyDigest, DigestItem, DigestStatus, SavedBriefSummary
from mailbrief.services.history import coverage_line, outside_count
from mailbrief.ui.brief_detail import BriefDetailPane
from mailbrief.ui.brief_list import ROW_PAD_H, BriefListView, build_rows
from mailbrief.ui.deadline_text import day_text, moment_text, month_day
from mailbrief.ui.glass.backdrop import Backdrop, BackdropKey, BackdropRenderer
from mailbrief.ui.glass.backdrop import color as tint_color
from mailbrief.ui.glass.motion import GLIDE_MS, fluid_curve
from mailbrief.ui.glass.painter import paint_panel, paint_raised
from mailbrief.ui.glass.preferences import glass_preferences
from mailbrief.ui.glass.profile import profiled
from mailbrief.ui.hairline import (
    HairlineSplitter,
    crisp_fill_rect,
    crisp_rect,
    hairline_pen,
    paint_focus_ring,
)
from mailbrief.ui.labels import ElidedLabel, button_label, plain_label
from mailbrief.ui.theme import (
    CAPTION_PX,
    HEADING_PX,
    SHEET_RADIUS,
    SIDEBAR_WIDTH,
    SMALL_PX,
    TEXT_PX,
    current_glass,
    current_mode,
    current_scheme,
    current_tokens,
    follow_theme,
    icon_pixmap,
    ui_font,
)

# How strongly a hovered or focused sidebar button shows the raised fill.
ACTIVE_RAISE: Final = 0.6
# The app's name in the header, Semibold.
TITLE_BAR_PX: Final = 15
# The header alert's icon (logical px).
_ALERT_ICON_PX: Final = 14
# The glass between the sheet and the window's right edge (logical px).
SHEET_GUTTER: Final = 8

# Ordered as Gmail and Outlook order theirs: the daily home first (their Inbox), the
# mailbox, flagged and deferred work (Starred, Snoozed), Drafts, then history (All Mail,
# Archive). Calendar comes last, set apart, as Outlook keeps it apart from mail. Ctrl+1 to
# Ctrl+7 follow this order.
PAGES: Final = (
    ("today", "Today", "sun"),
    ("inbox", "Inbox", "mail"),
    ("actions", "Actions", "checkbox"),
    ("waiting", "Waiting", "hourglass"),
    ("drafts", "Drafts", "pencil"),
    ("briefs", "Briefs", "calendar"),
    ("calendar", "Calendar", "calendar-week"),
)
# Pages that start a group: a gap with a hairline divider above them.
GROUP_STARTS: Final = frozenset({"calendar"})
EMPTY_BRIEF: Final = "No analyzed messages in this brief."

KEY_ROLE: Final = Qt.ItemDataRole.UserRole + 1
ICON_ROLE: Final = Qt.ItemDataRole.UserRole + 2
COUNT_ROLE: Final = Qt.ItemDataRole.UserRole + 3

_NAV_ROW: Final = 32
_GROUP_GAP: Final = 12  # Above a page that starts a group.
_ICON_PX: Final = 16
_ICON_X: Final = 10
_LABEL_X: Final = _ICON_X + _ICON_PX + 10

_Index = QModelIndex | QPersistentModelIndex


def _styled(widget: QWidget, name: str) -> None:
    """Name a plain QWidget so the stylesheet's background applies to it."""
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def brief_title(digest: DailyDigest, today: date) -> str:
    """The brief's day, such as "Tue Oct 6" (with the year when it isn't ``today``'s), and
    whether it is partial or empty."""
    title = day_text(digest.local_date, today=today)
    if digest.status is DigestStatus.PARTIAL:
        return f"{title} · Partial"
    if digest.status is DigestStatus.EMPTY:
        return f"{title} · Empty"
    return title


def brief_meta(digest: DailyDigest, zone: ZoneInfo, today: date) -> tuple[str, str]:
    """The brief's account, save time and coverage: a short line to show, and the full
    sentence for its accessible name.

    The save time is in the brief's own zone, like its coverage footer. When the owner's
    zone, ``zone``, would show another time, the brief's zone is named. The full sentence
    gives the same time, in the brief's zone, written as ``moment_text`` does and named the
    same way, so what is spoken matches what is shown.
    """
    saved = digest.generated_at_utc.astimezone(ZoneInfo(digest.timezone_name))
    if saved.date() > digest.local_date:
        when = f"{month_day(saved.date(), today=today)} {saved:%H:%M}"
    else:
        when = f"{saved:%H:%M}"
    # Compare offsets, not names: two names for one zone (Asia/Calcutta, Asia/Kolkata),
    # or zones that agree at that moment, show the same time and need no name.
    owner_offset = digest.generated_at_utc.astimezone(zone).utcoffset()
    named = "" if saved.utcoffset() == owner_offset else f" ({digest.timezone_name})"
    short = [f"{digest.account_id} · saved {when}{named}"]
    when_full = moment_text(digest.generated_at_utc, ZoneInfo(digest.timezone_name), today=today)
    full = f"{digest.account_id}. Saved {when_full}{named}."
    coverage = digest.coverage
    if coverage is not None:
        if coverage.failed:
            short.append(f"{coverage.failed} failed")
        if coverage.deferred:
            short.append(f"{coverage.deferred} deferred")
        if not coverage.sync_complete:
            short.append("Inbox sync incomplete")
        counts = (
            f"{coverage.analyzed} analyzed, {coverage.reused} reused, "
            f"{coverage.failed} failed, {coverage.skipped} skipped"
        )
        if coverage.deferred:
            counts += f", {coverage.deferred} deferred"
        sync = "complete" if coverage.sync_complete else "incomplete"
        full = f"{full} {counts}. Inbox sync {sync}."
    return " · ".join(short), full


def coverage_short(brief: DailyDigest | SavedBriefSummary, today: date) -> str:
    """What a brief covers, in a few words for the Today footer and in the brief's own zone:
    "Inbox on Oct 6, up to 09:14" on the brief's own day, "Inbox on Oct 6, checked Oct 7 at
    08:00" later, plus the replies from tracked threads that weren't in that Inbox. A day in
    a year other than ``today``'s (the owner's day) names it. The footer's accessible name,
    and the CLI, use the full ``coverage_line``."""
    made = brief.generated_at_utc.astimezone(ZoneInfo(brief.timezone_name))
    day = month_day(brief.local_date, today=today)
    if made.date() <= brief.local_date:
        line = f"Inbox on {day}, up to {made:%H:%M}"
    else:
        checked = month_day(made.date(), today=today)
        line = f"Inbox on {day}, checked {checked} at {made:%H:%M}"
    outside = outside_count(brief)
    if outside:
        line += f", plus {outside} tracked repl{'y' if outside == 1 else 'ies'}"
    return line


class _StatusLabel(ElidedLabel):
    """Asks for its whole text's width, but can shrink to nothing: a long status elides
    instead of widening the window."""

    def sizeHint(self) -> QSize:
        margins = self.contentsMargins()
        # Elision measures fractional widths, so round up: a whole-pixel width can fall a
        # fraction short and elide a status that fits.
        text = " ".join(self.text().split())
        width = math.ceil(QFontMetricsF(self.font()).horizontalAdvance(text))
        return QSize(width + margins.left() + margins.right(), super().sizeHint().height())


class HeaderAlert(QWidget):
    """A standing problem, shown once, in the header: an alert icon and a few words in the
    warning colour, as Studio shows status (bare coloured text, never a filled chip). The
    whole sentence is its tooltip and accessible description. Hidden without one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerAlert")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.icon = QLabel()
        self.icon.setObjectName("headerAlertIcon")
        follow_theme(
            self.icon,
            lambda label: label.setPixmap(
                icon_pixmap("alert-circle", current_tokens().warning_fg, _ALERT_ICON_PX, 2.0)
            ),
        )
        layout.addWidget(self.icon)
        self.text = _StatusLabel(tone="warning", px=SMALL_PX)
        self.text.setObjectName("headerAlertText")
        layout.addWidget(self.text)
        self.set_alert("")

    def set_alert(self, text: str, detail: str = "") -> None:
        """Show ``text``, with ``detail`` (else the text) as the whole sentence; an empty
        text hides the alert."""
        whole = detail or text
        self.text.setText(text, whole)
        self.setToolTip(whole)
        self.setAccessibleName(text)
        self.setAccessibleDescription(whole)
        self.setVisible(bool(text))


class GlassChrome(QWidget):
    """The header and sidebar: Liquid Glass over the part of ``workspace``'s backdrop that
    lies behind them. Their stylesheet background is transparent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workspace: weakref.ref[ThreePaneWorkspace] | None = None

    @property
    def workspace(self) -> "ThreePaneWorkspace | None":
        return None if self._workspace is None else self._workspace()

    @workspace.setter
    def workspace(self, workspace: "ThreePaneWorkspace | None") -> None:
        # Held weakly: the chrome is the workspace's child, and a reference back up would
        # leave a dropped workspace to the cycle collector (see `follow_theme`).
        self._workspace = None if workspace is None else weakref.ref(workspace)

    @profiled()  # HeaderBar and SidebarNav report apart.
    def paintEvent(self, event: QPaintEvent) -> None:
        rect = QRectF(self.rect())
        workspace = self.workspace
        backdrop = None if workspace is None else workspace.backdrop
        offset = QPointF(0, 0) if workspace is None else QPointF(self.mapTo(workspace, QPoint()))
        painter = QPainter(self)
        mode = glass_preferences().mode
        paint_panel(painter, rect, current_glass(), backdrop, rect.translated(offset), mode)
        painter.end()


class ContentSheet(GlassChrome):
    """The pages on a rounded sheet of the panel colour, floating on the glass with a
    gutter at its right, so no pane meets the chrome at a sharp corner. The pages fill the
    sheet just inside its hairline, masked to its rounded corners: their own square
    corners never show."""

    def __init__(self, pages: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _styled(self, "contentSheet")
        self.pages = pages
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, SHEET_GUTTER + 1, 1)  # Inside the hairline.
        layout.setSpacing(0)
        layout.addWidget(pages)
        pages.installEventFilter(self)

    def sheet_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0, 0, -SHEET_GUTTER, 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.pages and event.type() == QEvent.Type.Resize:
            self._mask_pages()
        return False

    def _mask_pages(self) -> None:
        """Round the pages' corners. A mask is aliased, so it follows a corner one pixel
        smaller than the sheet's: its steps fall on the sheet's own antialiased fill."""
        path = QPainterPath()
        radius = SHEET_RADIUS - 1
        path.addRoundedRect(QRectF(self.pages.rect()), radius, radius)
        self.pages.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)  # The glass around the sheet.
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Its edge on device pixel centres, so the hairline stays crisp at any scale, and
        # the fill out to those pixels' outer edges.
        edge = crisp_rect(painter, self.sheet_rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.panel))
        fill = crisp_fill_rect(painter, self.sheet_rect())
        painter.drawRoundedRect(fill, SHEET_RADIUS, SHEET_RADIUS)
        painter.setPen(hairline_pen(tokens.hairline))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(edge, SHEET_RADIUS, SHEET_RADIUS)
        painter.end()


class FooterBar(GlassChrome):
    """The glass under the sheet, for the window's status strip (``set_strip``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _styled(self, "footerBar")
        self.setAccessibleName("Status bar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def set_strip(self, strip: QWidget) -> None:
        self._layout.addWidget(strip)


class HeaderBar(GlassChrome):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _styled(self, "headerBar")
        self.setAccessibleName("MailBrief status")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(6)
        title = plain_label("MailBrief", px=TITLE_BAR_PX, semibold=True)
        title.setWordWrap(False)
        layout.addWidget(title)
        layout.addStretch(1)
        self.refresh_icon = QLabel()
        follow_theme(
            self.refresh_icon,
            lambda label: label.setPixmap(
                icon_pixmap("refresh", current_tokens().text_secondary, _ICON_PX, 2.0)
            ),
        )
        self.refresh_icon.setObjectName("headerRefreshIcon")
        self.refresh_icon.setAccessibleName("Refresh status")
        # Hidden without a status, but keeping its place, so the status never moves the
        # header's minimum width.
        policy = self.refresh_icon.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.refresh_icon.setSizePolicy(policy)
        layout.addWidget(self.refresh_icon)
        self.status = _StatusLabel(tone="secondary", px=SMALL_PX)
        self.status.setObjectName("headerStatus")
        layout.addWidget(self.status)
        # After the check time and before the window's buttons, as the one place a standing
        # problem shows.
        self.alert = HeaderAlert()
        layout.addWidget(self.alert)
        # Widgets the window adds, right of the status: 2 + the layout's 6 makes 8.
        self._slots = QHBoxLayout()
        self._slots.setContentsMargins(2, 0, 0, 0)
        self._slots.setSpacing(8)
        layout.addLayout(self._slots)
        self.set_status("")

    def set_status(self, text: str) -> None:
        """Show ``text``; the refresh icon shows only beside a status."""
        self.status.setText(text)
        self.refresh_icon.setVisible(bool(text))

    def set_alert(self, text: str, detail: str = "") -> None:
        """Show a standing problem in a few words, ``detail`` being the whole sentence; an
        empty ``text`` clears it."""
        self.alert.set_alert(text, detail)

    def add_widget(self, widget: QWidget) -> None:
        """Append ``widget`` right of the status."""
        self._slots.addWidget(widget)


class _NavDelegate(QStyledItemDelegate):
    @profiled("SidebarPage")
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        tokens = current_tokens()
        rect: QRect = option.rect
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if index.data(KEY_ROLE) in GROUP_STARTS:
            # The gap above a group, with a hairline across its middle; the row below it.
            painter.setPen(hairline_pen(tokens.hairline))
            middle = rect.top() + _GROUP_GAP // 2
            painter.drawLine(
                QPointF(rect.left() + _ICON_X, middle), QPointF(rect.right() - 10, middle)
            )
            rect = rect.adjusted(0, _GROUP_GAP, 0, 0)
        # The selected page's raised pill is the list's: it glides between rows.
        name = index.data(ICON_ROLE)
        color = tokens.text if selected else tokens.text_secondary
        if isinstance(name, str):
            pixmap = icon_pixmap(name, color, _ICON_PX, painter.device().devicePixelRatioF())
            top = rect.top() + (rect.height() - _ICON_PX) // 2
            painter.drawPixmap(rect.left() + _ICON_X, top, pixmap)
        font = ui_font(TEXT_PX)
        painter.setFont(font)
        painter.setPen(QColor(tokens.text if selected else tokens.text_secondary))
        flags = int(Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextSingleLine)
        label = QRect(
            rect.left() + _LABEL_X, rect.top(), rect.width() - _LABEL_X - 10, rect.height()
        )
        painter.drawText(label, flags | int(Qt.AlignmentFlag.AlignLeft), str(index.data()))
        count = index.data(COUNT_ROLE)
        if isinstance(count, int):
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(label, flags | int(Qt.AlignmentFlag.AlignRight), str(count))
        paint_focus_ring(painter, rect, state, tokens.accent_fg, inset=(1, 2), radius=None)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        gap = _GROUP_GAP if index.data(KEY_ROLE) in GROUP_STARTS else 0
        return QSize(option.rect.width(), _NAV_ROW + gap)


class _NavList(QListView):
    """Return and Enter request the current page once and are consumed. A click in the gap
    above a group (the divider) chooses nothing.

    It paints the selected page's raised pill itself, under the rows, so the pill can
    glide from the old page to the new one on the fluid curve (`GLIDE_MS`), as Liquid
    Glass morphs between states; with motion reduced it moves at once."""

    page_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.glide = QVariantAnimation(self)
        self.glide.setDuration(GLIDE_MS)
        self.glide.setEasingCurve(fluid_curve())
        self.glide.valueChanged.connect(lambda _value: self.viewport().update())

    def pill_rect(self, index: QModelIndex | QPersistentModelIndex) -> QRectF:
        """Where the raised pill sits on ``index``'s row, below a group's gap."""
        rect = QRectF(self.visualRect(index)).adjusted(1, 2, -1, -2)
        if index.data(KEY_ROLE) in GROUP_STARTS:
            rect.setTop(rect.top() + _GROUP_GAP)
        return rect

    def shown_pill(self) -> QRectF | None:
        """The raised pill as painted now: on its way while gliding, else on the current
        row."""
        if self.glide.state() is QAbstractAnimation.State.Running:
            value = self.glide.currentValue()
            if isinstance(value, QRectF):
                return value
        index = self.currentIndex()
        return self.pill_rect(index) if index.isValid() else None

    def currentChanged(
        self,
        current: QModelIndex | QPersistentModelIndex,
        previous: QModelIndex | QPersistentModelIndex,
    ) -> None:
        start = self.shown_pill() if previous.isValid() else None
        super().currentChanged(current, previous)
        self.glide.stop()
        if start is None or not current.isValid() or glass_preferences().reduce_motion:
            self.viewport().update()
            return
        self.glide.setStartValue(start)
        self.glide.setEndValue(self.pill_rect(current))
        self.glide.start()

    def paintEvent(self, event: QPaintEvent) -> None:
        pill = self.shown_pill()
        if pill is not None:
            painter = QPainter(self.viewport())
            paint_raised(painter, pill, current_glass(), glass_preferences().mode)
            painter.end()
        super().paintEvent(event)

    def _in_gap(self, event: QMouseEvent) -> bool:
        point = event.position().toPoint()
        index = self.indexAt(point)
        if not index.isValid() or index.data(KEY_ROLE) not in GROUP_STARTS:
            return False
        return point.y() < self.visualRect(index).top() + _GROUP_GAP

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._in_gap(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._in_gap(event):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._in_gap(event):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        index = self.currentIndex()
        if index.isValid() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.page_activated.emit(str(index.data(KEY_ROLE)))
            event.accept()
            return
        super().keyPressEvent(event)


class NavButton(QPushButton):
    """A button painted like a page row, so its icon and label line up with the pages.

    ``mnemonic`` is app-authored button text with an ``&`` (such as "Se&ttings") so Alt and
    that key press it; the row still paints ``text``.
    """

    def __init__(
        self,
        text: str,
        icon_name: str,
        *,
        mnemonic: str | None = None,
        accessible_name: str | None = None,
    ) -> None:
        super().__init__(button_label(text) if mnemonic is None else mnemonic)
        self._text = text
        self._icon_name = icon_name
        self.setProperty("variant", "nav")
        self.setAccessibleName(text if accessible_name is None else accessible_name)
        self.setAutoDefault(False)
        self.setFixedHeight(_NAV_ROW)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _active(self) -> bool:
        return self.isEnabled() and (self.underMouse() or self.hasFocus() or self.isDown())

    def _color(self) -> str:
        """The icon and label colour: muted while disabled, full while hovered, focused or
        pressed, else secondary."""
        tokens = current_tokens()
        if not self.isEnabled():
            return tokens.text_muted
        return tokens.text if self._active() else tokens.text_secondary

    @profiled("SidebarButton")
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if self._active():  # Never while disabled: a fainter raised fill, with no rim.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint_color(current_glass().raised, ACTIVE_RAISE))
            pill = QRectF(rect).adjusted(1, 2, -1, -2)
            painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        color = self._color()
        pixmap = icon_pixmap(self._icon_name, color, _ICON_PX, self.devicePixelRatioF())
        painter.drawPixmap(_ICON_X, (rect.height() - _ICON_PX) // 2, pixmap)
        painter.setFont(ui_font(TEXT_PX))
        painter.setPen(QColor(color))
        label = QRect(_LABEL_X, 0, rect.width() - _LABEL_X, rect.height())
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(label, flags | int(Qt.TextFlag.TextSingleLine), self._text)
        painter.end()


class SidebarNav(GlassChrome):
    """``page_requested(key)`` on selection and on Return or Enter; ``settings_requested``.

    Below the pages: Saved mail, Data, Settings, the note, then ``footer`` for the window's
    own widgets."""

    page_requested = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _styled(self, "sidebar")
        self.setAccessibleName("Pages")
        self.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(2)
        self.pages = QStandardItemModel(self)
        for key, label, icon_name in PAGES:
            item = QStandardItem(label)
            item.setData(key, KEY_ROLE)
            item.setData(icon_name, ICON_ROLE)
            item.setData(label, Qt.ItemDataRole.AccessibleTextRole)
            item.setEditable(False)
            self.pages.appendRow(item)
        self.nav = _NavList()
        self.nav.setObjectName("sidebarNav")
        self.nav.setAccessibleName("Pages")
        self.nav.setModel(self.pages)
        self.nav.setItemDelegate(_NavDelegate(self.nav))
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFixedHeight(_NAV_ROW * len(PAGES) + _GROUP_GAP * len(GROUP_STARTS))
        self.nav.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._quiet = False
        self.nav.selectionModel().currentChanged.connect(self._current_changed)
        self.nav.page_activated.connect(self.page_requested)
        layout.addWidget(self.nav)
        layout.addStretch(1)
        self.saved_mail = NavButton("Saved mail", "mail", mnemonic="Saved &mail")
        self.saved_mail.setObjectName("savedMailButton")
        layout.addWidget(self.saved_mail)
        self.data = NavButton(
            "Data", "database", mnemonic="&Data", accessible_name="Data and recovery"
        )
        self.data.setObjectName("dataButton")
        layout.addWidget(self.data)
        self.settings = NavButton("Settings", "settings", mnemonic="Se&ttings")
        self.settings.setObjectName("settingsButton")
        self.settings.clicked.connect(self.settings_requested)
        layout.addWidget(self.settings)
        self.note = plain_label("", tone="muted", px=CAPTION_PX)
        self.note.setObjectName("sidebarNote")
        self.note.setAccessibleName("Connection")
        self.note.setContentsMargins(10, 4, 4, 0)
        self.note.hide()
        layout.addWidget(self.note)
        self.footer = QVBoxLayout()
        self.footer.setContentsMargins(0, 0, 0, 0)
        self.footer.setSpacing(4)
        layout.addLayout(self.footer)
        self.set_current("today")

    def _row(self, key: str) -> int:
        return next(number for number, page in enumerate(PAGES) if page[0] == key)

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if not self._quiet and current.isValid():
            self.page_requested.emit(str(current.data(KEY_ROLE)))

    def set_current(self, key: str) -> None:
        """Show ``key`` as the current page without requesting it."""
        self._quiet = True
        try:
            self.nav.setCurrentIndex(self.pages.index(self._row(key), 0))
        finally:
            self._quiet = False

    def current_key(self) -> str:
        return str(self.nav.currentIndex().data(KEY_ROLE))

    def set_counts(self, actions: int | None, waiting: int | None, drafts: int | None) -> None:
        """Show each count (None hides it); a screen reader reads "Actions, 5"."""
        for key, count in (("actions", actions), ("waiting", waiting), ("drafts", drafts)):
            item = self.pages.item(self._row(key))
            item.setData(count, COUNT_ROLE)
            label = item.text()
            item.setData(
                label if count is None else f"{label}, {count}", Qt.ItemDataRole.AccessibleTextRole
            )

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def count_text(self, key: str) -> str | None:
        count = self.pages.item(self._row(key)).data(COUNT_ROLE)
        return None if count is None else str(count)


class BriefHeading(QWidget):
    """The shown brief's day above the list, and its account, save time and coverage."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("briefHeading")
        self.setAccessibleName("Brief")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ROW_PAD_H, 16, ROW_PAD_H, 4)
        layout.setSpacing(2)
        self.title = plain_label("", px=HEADING_PX, semibold=True)
        self.title.setObjectName("briefHeadingTitle")
        layout.addWidget(self.title)
        self.meta = ElidedLabel(tone="muted", px=CAPTION_PX)
        self.meta.setObjectName("briefHeadingMeta")
        layout.addWidget(self.meta)

    def show_brief(self, digest: DailyDigest, zone: ZoneInfo, today: date) -> None:
        self.title.setText(brief_title(digest, today))
        self.meta.setText(*brief_meta(digest, zone, today))
        self.show()


class ThreePaneWorkspace(QWidget):
    """Header, sidebar and ``pages``: ``today`` holds ``today_top`` above the brief's list
    and detail; the window adds its own pages with ``add_page``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _styled(self, "workspace")
        self.setAccessibleName("MailBrief workspace")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # One sheet of glass (header, sidebar, the gutter round the pages and the footer)
        # with the pages on a rounded sheet: no hairline meets another at a sharp corner.
        self.header = HeaderBar()
        outer.addWidget(self.header)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = SidebarNav()
        body.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.pages.setObjectName("workspacePages")
        self._pages: dict[str, QWidget] = {}
        today = QWidget()
        today.setObjectName("todayPage")
        today.setAccessibleName("Today")
        today_layout = QVBoxLayout(today)
        today_layout.setContentsMargins(0, 0, 0, 0)
        today_layout.setSpacing(0)
        self.today_top = QVBoxLayout()
        self.today_top.setContentsMargins(0, 0, 0, 0)
        self.today_top.setSpacing(0)
        today_layout.addLayout(self.today_top)
        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        list_pane = QWidget()
        list_pane.setObjectName("briefListPane")
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        self.heading = BriefHeading()
        self.heading.hide()
        list_layout.addWidget(self.heading)
        self.brief_list = BriefListView()
        list_layout.addWidget(self.brief_list, 1)
        self.coverage = ElidedLabel(tone="muted", px=CAPTION_PX)
        self.coverage.setContentsMargins(ROW_PAD_H, 10, ROW_PAD_H, 10)
        list_layout.addWidget(self.coverage)
        list_pane.setMinimumWidth(220)
        self.detail = BriefDetailPane()
        self.detail.setMinimumWidth(280)
        # Page Up and Page Down in the list page through the selected email.
        self.brief_list.set_page_scroll(self.detail.verticalScrollBar())
        self.splitter.addWidget(list_pane)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 5)
        self.splitter.setChildrenCollapsible(False)
        today_layout.addWidget(self.splitter, 1)
        self.add_page("today", today)
        self.sheet = ContentSheet(self.pages)
        body.addWidget(self.sheet, 1)
        outer.addLayout(body, 1)
        self.footer_bar = FooterBar()
        outer.addWidget(self.footer_bar)
        self._digest: DailyDigest | None = None
        self._links: Mapping[str, Sequence[ThreadLink]] = {}
        self._proposals: Mapping[str, Sequence[ActionProposal]] = {}
        # The detail's scroll position to restore once its new content has a range.
        self._pending_scroll: int | None = None
        # The owner's zone and day the brief was last shown for, to show it again on a new day.
        self._owner_zone: ZoneInfo | None = None
        self._today: date | None = None
        self.brief_list.item_selected.connect(self._show_item)
        detail_bar = self.detail.verticalScrollBar()
        detail_bar.rangeChanged.connect(self._restore_scroll)
        # Scrolling by hand ends a restore still waiting for the layout.
        detail_bar.actionTriggered.connect(self._drop_pending_scroll)
        # The backdrop the header's and sidebar's glass shows, for this size and theme.
        self.backdrop: Backdrop | None = None
        self.backdrop_renders = BackdropRenderer(self)
        self.backdrop_renders.ready.connect(self._backdrop_ready)
        for chrome in (self.header, self.sidebar, self.sheet, self.footer_bar):
            chrome.workspace = self
        follow_theme(self, lambda workspace: workspace._request_backdrop())

    def _backdrop_key(self) -> BackdropKey:
        return BackdropKey(self.width(), self.height(), current_scheme(), current_mode())

    def _request_backdrop(self) -> None:
        backdrop = self.backdrop_renders.request(self._backdrop_key())
        if backdrop is not None:
            self._show_backdrop(backdrop)

    def _backdrop_ready(self, backdrop: Backdrop) -> None:
        if backdrop.key == self._backdrop_key():
            self._show_backdrop(backdrop)

    def _show_backdrop(self, backdrop: Backdrop) -> None:
        self.backdrop = backdrop
        for chrome in (self.header, self.sidebar, self.sheet, self.footer_bar):
            chrome.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._request_backdrop()

    def add_page(self, key: str, widget: QWidget) -> None:
        self._pages[key] = widget
        self.pages.addWidget(widget)

    def show_page(self, key: str) -> None:
        """Show page ``key``; an unknown key raises KeyError."""
        self.pages.setCurrentWidget(self._pages[key])

    def current_page(self) -> str:
        current = self.pages.currentWidget()
        return next(key for key, widget in self._pages.items() if widget is current)

    def show_digest(
        self,
        digest: DailyDigest,
        *,
        links: Mapping[str, Sequence[ThreadLink]] | None = None,
        proposals: Mapping[str, Sequence[ActionProposal]] | None = None,
        owner_zone: ZoneInfo,
        today: date,
    ) -> None:
        """Show ``digest``, its deadline chips counted from ``today`` (the owner's current
        day). Showing the same saved brief again keeps the selected email and the detail's
        scroll position; another brief starts at its first email."""
        previous = self._digest
        same = previous is not None and _identity(previous) == _identity(digest)
        select = self.brief_list.selected_key() if same else None
        scroll = self.detail.verticalScrollBar().value() if same else 0
        self._digest = digest
        self._links = links or {}
        self._proposals = proposals or {}
        self._owner_zone, self._today = owner_zone, today
        self.heading.show_brief(digest, owner_zone, today)
        self.coverage.setText(coverage_short(digest, today), coverage_line(digest))
        if not digest.items:
            self.brief_list.show_rows([])
            self.detail.show_empty(EMPTY_BRIEF)
            return
        rows = build_rows(digest, self._proposals, today, owner_zone)
        self.brief_list.show_rows(rows, select=select)
        if same and scroll:
            # The new content gets its scroll range once it is laid out: from the next turn
            # of the event loop, or when the range changes, whichever has one first.
            self._pending_scroll = scroll
            QTimer.singleShot(0, self._restore_scroll)

    def set_today(self, today: date, owner_zone: ZoneInfo) -> None:
        """On a new day (or in a new zone), recount the list's deadline chips from
        ``today`` in ``owner_zone``, in place: the selection, the detail pane and keyboard
        focus stay as they are, and nothing is read from storage. The heading's day and save
        time, and the coverage footer's days, are rewritten too: a brief gains its year after
        New Year. Nothing else in the brief depends on the day."""
        digest = self._digest
        if digest is None or (today, owner_zone) == (self._today, self._owner_zone):
            return
        self._today, self._owner_zone = today, owner_zone
        self.heading.show_brief(digest, owner_zone, today)  # Its year, and its save day.
        self.coverage.setText(coverage_short(digest, today), coverage_line(digest))
        if digest.items:
            self.brief_list.brief_model.update_rows(
                build_rows(digest, self._proposals, today, owner_zone)
            )

    def _restore_scroll(self) -> None:
        """Scroll back as far as the range allows. The new content's wrapped labels can
        grow the range over several layout passes, so the position waits until one reaches
        it."""
        bar = self.detail.verticalScrollBar()
        if self._pending_scroll is not None and bar.maximum() > 0:
            bar.setValue(min(self._pending_scroll, bar.maximum()))
            if bar.maximum() >= self._pending_scroll:
                self._pending_scroll = None

    def _drop_pending_scroll(self, action: int) -> None:
        self._pending_scroll = None

    def clear(self, message: str) -> None:
        """Show no brief, only ``message``."""
        self._digest = None
        self._links = {}
        self._proposals = {}
        self._pending_scroll = None
        self._owner_zone = self._today = None
        self.brief_list.show_rows([])
        self.coverage.setText("")
        self.heading.hide()
        self.detail.show_empty(message)

    def _show_item(self, item: DigestItem) -> None:
        self._pending_scroll = None  # Another email starts at the top.
        digest, today = self._digest, self._today
        if digest is None or today is None:
            return
        self.detail.show_item(
            item,
            account_email=digest.account_id,
            timezone_name=digest.timezone_name,
            today=today,
            links=self._links.get(item.message_key, ()),
            proposals=self._proposals.get(item.message_key, ()),
        )


def _identity(digest: DailyDigest) -> tuple[str, date, datetime]:
    return (digest.account_id, digest.local_date, digest.generated_at_utc)
