"""The brief as a painted list: section headers, then one row per email with its chips.

Mail and AI text is drawn with ``drawText`` only, so it can never become rich text or a
link, and it never reaches a tooltip.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Final, Literal, Protocol
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QFrame,
    QListView,
    QScrollBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from mailbrief.domain.actions import ActionProposal, ProposalState
from mailbrief.domain.analysis import DeadlinePrecision, FollowUpKind
from mailbrief.domain.digests import DailyDigest, DigestItem, DigestSection
from mailbrief.domain.messages import EmailContact, display_name
from mailbrief.ui.deadline_text import month_day, weekday
from mailbrief.ui.hairline import (
    SELECTION_INSET_X,
    SELECTION_INSET_Y,
    hairline_pen,
    paint_focus_ring,
)
from mailbrief.ui.labels import cut_text
from mailbrief.ui.theme import (
    BODY_PX,
    CAPTION_PX,
    META_PX,
    RADIUS,
    SMALL_PX,
    Tokens,
    current_tokens,
    ui_font,
    ui_metrics,
)

NO_SUBJECT: Final = "(no subject)"
BRIEF_SECTION_TITLES: Final = {
    DigestSection.ACTIONS: "Needs attention",
    DigestSection.DEADLINES: "Upcoming deadlines",
    DigestSection.DECISIONS: "Decisions",
    DigestSection.HIGHLIGHTS: "Highlights",
    DigestSection.FOLLOW_UPS: "New replies",
}
_UNRESOLVED_CHARS: Final = 24
# A timed deadline up to this many days after the brief's day is named by its weekday.
_WEEKDAY_DAYS: Final = 6

# Two-line rows, in the brief list and the run page's shortlist: padding, and the gap above
# the chip row (logical px).
ROW_PAD_V: Final = 10
ROW_PAD_H: Final = 18
CHIP_GAP: Final = 4
SINGLE_LINE: Final = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) | int(
    Qt.TextFlag.TextSingleLine
)
# Chips are bare coloured labels, as Apple marks what's new: this far apart (logical px).
_CHIP_SPACING: Final = 12
# Header rows: left inset, space above and below the title.
_HEADER_ABOVE: Final = 16
_HEADER_BELOW: Final = 4
# The room a row's text leaves: its padding at both sides (logical px).
ELIDE_MARGIN: Final = 2 * ROW_PAD_H


class ChipTone(StrEnum):
    WARNING = "warning"
    ACCENT = "accent"


@dataclass(frozen=True)
class Chip:
    text: str
    tone: ChipTone


@dataclass(frozen=True)
class BriefRow:
    kind: Literal["header", "item"]
    title: str
    item: DigestItem | None
    sender: str
    chips: tuple[Chip, ...]
    count: int = 0


def sender_text(contact: EmailContact) -> str:
    """The sender's name, else the address."""
    return contact.name or contact.address


def initials(name: str) -> str:
    """Up to two initials for an avatar: "Alex Kim" is "AK", "Alex" is "A"."""
    return "".join(part[0] for part in name.split()[:2]).upper()


def sender_with_address(contact: EmailContact) -> str:
    """The sender as "Name <address>", so a display name can't pass for someone else (see
    ``with_address``)."""
    return with_address(contact.name, contact.address)


def with_address(name: str | None, address: str) -> str:
    """A sender as "Name <address>"; the address alone when the name is empty or the
    address itself, or contains an at sign: a name that looks like an address is how a
    sender poses as another (``display_name``)."""
    shown = display_name(name, address)
    return address if shown is None else f"{shown} <{address}>"


def deadline_chip(
    item: DigestItem, zone: ZoneInfo, today: date, owner_zone: ZoneInfo
) -> Chip | None:
    """A short deadline in the digest's ``zone`` (as the detail pane shows it), or None when
    the email states none. A timed deadline within the week from ``today``, judged by its
    date in ``owner_zone`` (where ``today`` is), gives its weekday; any other, past ones
    included, its date."""
    text: str | None = None
    if item.deadline_precision is DeadlinePrecision.DATETIME and item.deadline_at_utc:
        local = item.deadline_at_utc.astimezone(zone)
        owner_day = item.deadline_at_utc.astimezone(owner_zone).date()
        if today <= owner_day <= today + timedelta(days=_WEEKDAY_DAYS):
            text = f"Due {weekday(local.date())} {local:%H:%M}"
        else:
            text = f"Due {month_day(local.date(), today=today)} {local:%H:%M}"
    elif item.deadline_precision is DeadlinePrecision.DATE and item.deadline_date:
        text = f"Due {month_day(item.deadline_date, today=today)}"
    elif item.deadline_precision is DeadlinePrecision.UNRESOLVED and item.deadline_text:
        text = f"Due {cut_text(item.deadline_text, _UNRESOLVED_CHARS)}"
    return None if text is None else Chip(text, ChipTone.WARNING)


def proposal_chip(proposals: Sequence[ActionProposal]) -> Chip | None:
    """What the first pending proposal would do, or None."""
    for proposal in proposals:
        if proposal.state is ProposalState.PENDING:
            text = (
                "Proposes a new deadline"
                if proposal.kind is FollowUpKind.NEW_DEADLINE
                else "Proposes completing an action"
            )
            return Chip(text, ChipTone.ACCENT)
    return None


def build_rows(
    digest: DailyDigest,
    proposals: Mapping[str, Sequence[ActionProposal]],
    today: date,
    owner_zone: ZoneInfo,
) -> list[BriefRow]:
    """A header row whenever the section changes, then each item in digest order. Deadline
    chips count from ``today``, the owner's current day in ``owner_zone``, whatever day the
    brief covers."""
    zone = ZoneInfo(digest.timezone_name)
    rows: list[BriefRow] = []
    section = None
    for item in digest.items:
        if item.section is not section:
            section = item.section
            count = sum(found.section is section for found in digest.items)
            rows.append(BriefRow("header", BRIEF_SECTION_TITLES[section], None, "", (), count))
        chips = tuple(
            chip
            for chip in (
                deadline_chip(item, zone, today, owner_zone),
                proposal_chip(proposals.get(item.message_key, ())),
            )
            if chip is not None
        )
        rows.append(
            BriefRow("item", item.subject or NO_SUBJECT, item, sender_text(item.sender), chips)
        )
    return rows


KIND_ROLE: Final = Qt.ItemDataRole.UserRole + 1
ROW_ROLE: Final = Qt.ItemDataRole.UserRole + 2

_Index = QModelIndex | QPersistentModelIndex
_ROOT: Final = QModelIndex()


class ListRow(Protocol):
    """A row of a painted list: a section header or an entry under one."""

    @property
    def kind(self) -> str: ...

    @property
    def title(self) -> str: ...


class HeaderRowsModel[R: ListRow](QAbstractListModel):
    """The rows of a painted list with section headers. DisplayRole is a row's title,
    KIND_ROLE its kind and ROW_ROLE the row itself; a header is neither enabled nor
    selectable, so the keyboard passes over it."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[R] = []

    def set_rows(self, rows: Sequence[R]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> tuple[R, ...]:
        return tuple(self._rows)

    def accessible_text(self, row: R) -> str:
        """What a screen reader says for ``row``."""
        return row.title

    def rowCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return self.accessible_text(row)
        if role == KIND_ROLE:
            return row.kind
        if role == ROW_ROLE:
            return row
        return None

    def flags(self, index: _Index) -> Qt.ItemFlag:
        if not index.isValid() or self._rows[index.row()].kind == "header":
            # Not even enabled: an enabled, unselectable row would still take focus.
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class BriefListModel(HeaderRowsModel[BriefRow]):
    def update_rows(self, rows: Sequence[BriefRow]) -> None:
        """Replace rows that match the current ones one for one (the same brief, its chips
        recounted) in place: no reset, so the selection, the detail and focus stay."""
        if len(rows) != len(self._rows):
            raise ValueError("update_rows needs the same rows")
        self._rows = list(rows)
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1))

    def accessible_text(self, row: BriefRow) -> str:
        if row.kind == "header":
            return row.title
        return f"{row.title}, from {row.sender}" + "".join(f", {chip.text}" for chip in row.chips)


def chip_height() -> int:
    return ui_metrics(CAPTION_PX, medium=True).height()


def paint_chips(painter: QPainter, chips: Sequence[Chip], left: int, top: int) -> None:
    """Chips in a row from ``left``: bare caption text in the warning or accent colour, with
    no fill, as Apple labels what's new."""
    tokens = current_tokens()
    metrics = ui_metrics(CAPTION_PX, medium=True)
    height = chip_height()
    painter.setFont(ui_font(CAPTION_PX, medium=True))
    x = left
    for chip in chips:
        width = metrics.horizontalAdvance(chip.text)
        warning = chip.tone is ChipTone.WARNING
        painter.setPen(QColor(tokens.warning_fg if warning else tokens.accent_fg))
        painter.drawText(QRect(x, top, width, height), SINGLE_LINE, chip.text)
        x += width + _CHIP_SPACING


def flat(text: str) -> str:
    """One line: elision measures the string as drawn, so line breaks become spaces."""
    return " ".join(text.split())


def elided(text: str, metrics: QFontMetrics, width: int) -> str:
    """``text`` on one line, cut at the right with "…" to fit ``width``."""
    return metrics.elidedText(flat(text), Qt.TextElideMode.ElideRight, width)


def row_height(has_chips: bool) -> int:
    """A two-line row's height: padding, the title and sender lines, the hairline, and the
    chip row when there is one."""
    height = (
        ROW_PAD_V
        + ui_metrics(BODY_PX, medium=True).height()
        + ui_metrics(META_PX).height()
        + ROW_PAD_V
        + 1  # The hairline.
    )
    if has_chips:
        height += CHIP_GAP + chip_height()
    return height


def paint_selection(
    painter: QPainter, rect: QRect, state: QStyle.StateFlag, tokens: Tokens
) -> None:
    """A selected row's fill: a rounded rectangle inset from the row's edges."""
    if state & QStyle.StateFlag.State_Selected:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.selection))
        fill = QRectF(rect).adjusted(
            SELECTION_INSET_X, SELECTION_INSET_Y, -SELECTION_INSET_X, -SELECTION_INSET_Y
        )
        painter.drawRoundedRect(fill, RADIUS, RADIUS)
        painter.restore()


# A hovered row's fill: the selection's colour at this opacity, so hovering never reads as
# choosing.
HOVER_OPACITY: Final = 0.5


def paint_hover(painter: QPainter, rect: QRect, state: QStyle.StateFlag, tokens: Tokens) -> None:
    """A hovered row's fill, shaped like the selection's, fainter; none on a selected row."""
    if state & QStyle.StateFlag.State_MouseOver and not state & QStyle.StateFlag.State_Selected:
        painter.save()
        painter.setOpacity(HOVER_OPACITY)
        paint_selection(painter, rect, state | QStyle.StateFlag.State_Selected, tokens)
        painter.restore()


def paint_lines(
    painter: QPainter,
    left: int,
    top: int,
    width: int,
    title: str,
    subtitle: str,
    *,
    title_color: str,
    subtitle_color: str,
) -> int:
    """The title, 13 px Medium and elided to ``width``, then ``subtitle`` (a sender or a meta
    line) as given, 13 px (callers fit it to ``width``). Returns the y below the subtitle."""
    title_metrics = ui_metrics(BODY_PX, medium=True)
    painter.setFont(ui_font(BODY_PX, medium=True))
    painter.setPen(QColor(title_color))
    painter.drawText(
        QRect(left, top, width, title_metrics.height()),
        SINGLE_LINE,
        elided(title, title_metrics, width),
    )
    top += title_metrics.height()
    subtitle_metrics = ui_metrics(META_PX)
    painter.setFont(ui_font(META_PX))
    painter.setPen(QColor(subtitle_color))
    painter.drawText(QRect(left, top, width, subtitle_metrics.height()), SINGLE_LINE, subtitle)
    return top + subtitle_metrics.height()


def header_height() -> int:
    """A section header row's height: the space above, its title and the space below."""
    return _HEADER_ABOVE + ui_metrics(SMALL_PX, semibold=True).height() + _HEADER_BELOW


def paint_header(painter: QPainter, rect: QRect, title: str, tokens: Tokens) -> None:
    """A section header row: its title, 12 px Semibold in text_secondary, at the rows' text
    inset."""
    painter.setFont(ui_font(SMALL_PX, semibold=True))
    painter.setPen(QColor(tokens.text_secondary))
    height = ui_metrics(SMALL_PX, semibold=True).height()
    target = QRect(rect.left() + ROW_PAD_H, rect.top() + _HEADER_ABOVE, 0, height)
    target.setRight(rect.right() - ROW_PAD_H)
    painter.drawText(target, SINGLE_LINE, title)


def paint_separator(painter: QPainter, rect: QRect, tokens: Tokens) -> None:
    """The cosmetic hairline along a row's bottom edge, inset to line up with its text."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(hairline_pen(tokens.hairline))
    bottom = rect.bottom()
    painter.drawLine(
        QPointF(rect.left() + ROW_PAD_H, bottom), QPointF(rect.right() - ROW_PAD_H, bottom)
    )


class BriefItemDelegate(QStyledItemDelegate):
    """Paints header and item rows with ``drawText`` only."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        row = index.data(ROW_ROLE)
        if not isinstance(row, BriefRow):
            return
        tokens = current_tokens()
        rect: QRect = option.rect
        state: QStyle.StateFlag = option.state
        painter.save()
        painter.setClipRect(rect)
        if row.kind == "header":
            painter.setFont(ui_font(META_PX, medium=True))
            painter.setPen(QColor(tokens.text_muted))
            height = ui_metrics(META_PX).height()
            target = QRect(rect.left() + ROW_PAD_H, rect.top() + _HEADER_ABOVE, 0, height)
            target.setRight(rect.right() - ROW_PAD_H)
            painter.drawText(target, SINGLE_LINE, row.title.upper())
            painter.drawText(
                target,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(row.count),
            )
            painter.restore()
            return
        paint_selection(painter, rect, state, tokens)
        left = rect.left() + 25
        width = max(0, rect.width() - 51)
        top = rect.top() + 12
        warning = next((chip for chip in row.chips if chip.tone is ChipTone.WARNING), None)
        if warning is not None or (row.item and row.item.section is DigestSection.HIGHLIGHTS):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.warning_fg if warning else tokens.text_muted))
            painter.drawRoundedRect(QRectF(rect.left() + 13, top + 2, 3, 28), 1.5, 1.5)
        title_width = width
        if warning is not None:
            metrics = ui_metrics(8, medium=True)
            status_width = min(width // 2, metrics.horizontalAdvance(warning.text))
            painter.setFont(ui_font(8, medium=True))
            painter.setPen(QColor(tokens.warning_fg))
            painter.drawText(
                QRect(left + width - status_width, top, status_width, 15),
                SINGLE_LINE,
                elided(warning.text, metrics, status_width),
            )
            title_width = max(0, width - status_width - 8)
        top = paint_lines(
            painter,
            left,
            top,
            title_width,
            row.title,
            elided(row.sender, ui_metrics(META_PX), width),
            title_color=tokens.text,
            subtitle_color=tokens.text_secondary,
        )
        if row.item is not None:
            metrics = ui_metrics(META_PX)
            painter.setFont(ui_font(META_PX))
            painter.setPen(QColor(tokens.text_muted))
            painter.drawText(
                QRect(left, top + 4, width, metrics.height()),
                SINGLE_LINE,
                elided(row.item.summary, metrics, width),
            )
            top += metrics.height() + 4
        extra = tuple(chip for chip in row.chips if chip.tone is not ChipTone.WARNING)
        if extra:
            paint_chips(painter, extra, left, top + CHIP_GAP)
        if state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.setPen(QColor(tokens.text_muted))
            painter.setFont(ui_font(15))
            painter.drawText(
                QRect(rect.right() - 23, rect.top(), 15, rect.height()), SINGLE_LINE, "›"
            )
        paint_focus_ring(painter, rect, state, tokens.accent_fg)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        row = index.data(ROW_ROLE)
        width: int = option.rect.width()
        if not isinstance(row, BriefRow):
            return QSize(width, 0)
        if row.kind == "header":
            return QSize(width, _HEADER_ABOVE + ui_metrics(META_PX).height() + _HEADER_BELOW)
        extra = any(chip.tone is not ChipTone.WARNING for chip in row.chips)
        return QSize(
            width,
            28
            + ui_metrics(BODY_PX, medium=True).height()
            + 2 * ui_metrics(META_PX).height()
            + (CHIP_GAP + chip_height() if extra else 0),
        )


def page_detail(view: QAbstractItemView, bar: QScrollBar | None, event: QKeyEvent) -> bool:
    """Page Up or Page Down in ``view`` scrolls ``bar`` (the detail beside it) by a page
    while the whole list fits; True when it did, and the key is then accepted. A list that
    scrolls, or no ``bar``, leaves the key to the list."""
    modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    if (
        bar is None
        or event.key() not in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
        or modifiers != Qt.KeyboardModifier.NoModifier
        or view.verticalScrollBar().maximum() != 0
    ):
        return False
    # Only while the whole list fits: then the item being read is what needs paging.
    bar.triggerAction(
        QAbstractSlider.SliderAction.SliderPageStepSub
        if event.key() == Qt.Key.Key_PageUp
        else QAbstractSlider.SliderAction.SliderPageStepAdd
    )
    event.accept()
    return True


class BriefListView(QListView):
    """``item_selected(DigestItem)`` reports the selected email. With ``set_page_scroll``,
    Page Up and Page Down scroll the selected email's detail while the whole list fits."""

    item_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("briefList")
        self.setAccessibleName("Brief items")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(False)
        self.brief_model = BriefListModel(self)
        self.setModel(self.brief_model)
        self.setItemDelegate(BriefItemDelegate(self))
        self.selectionModel().currentChanged.connect(self._current_changed)
        self._page_scroll: QScrollBar | None = None

    def set_page_scroll(self, bar: QScrollBar | None) -> None:
        """While the whole list fits, Page Up and Page Down scroll ``bar`` by a page; when
        the list scrolls, or with None, they page the list."""
        self._page_scroll = bar

    def show_rows(self, rows: Sequence[BriefRow], *, select: str | None = None) -> None:
        """Show ``rows`` and select the email whose ``message_key`` is ``select``, else the
        first email."""
        self.brief_model.set_rows(rows)
        items = [number for number, row in enumerate(rows) if row.item is not None]
        chosen = next(
            (
                number
                for number in items
                if (item := rows[number].item) is not None and item.message_key == select
            ),
            items[0] if items else None,
        )
        if chosen is not None:
            self.setCurrentIndex(self.brief_model.index(chosen))

    def selected_key(self) -> str | None:
        """The selected email's ``message_key``, or None when no email is selected."""
        row = self.currentIndex().data(ROW_ROLE)
        if isinstance(row, BriefRow) and row.item is not None:
            return row.item.message_key
        return None

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        row = current.data(ROW_ROLE)
        if isinstance(row, BriefRow) and row.item is not None:
            self.item_selected.emit(row.item)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        index = self.currentIndex()
        if page_detail(self, self._page_scroll, event):
            return
        if index.isValid() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Activate once and consume the key, as ActivatingList does (ui/lists.py).
            self.activated.emit(index)
            event.accept()
            return
        super().keyPressEvent(event)
