"""Shared plain-text rows for local writing and saved history."""

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from mailbrief.ui.brief_list import (
    SINGLE_LINE,
    elided,
    paint_lines,
    paint_selection,
)
from mailbrief.ui.hairline import hairline_pen, paint_focus_ring
from mailbrief.ui.theme import BODY_PX, META_PX, current_tokens, icon_pixmap, ui_font, ui_metrics

DISPLAY_ROW_ROLE = Qt.ItemDataRole.UserRole + 20


@dataclass(frozen=True)
class DisplayRow:
    title: str
    meta: str
    preview: str = ""
    glyph: str = ""
    date_tile: str = ""
    unread: bool = False
    accent: str = ""


class DisplayRowDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(DISPLAY_ROW_ROLE)
        if not isinstance(row, DisplayRow):
            super().paint(painter, option, index)
            return
        tokens = current_tokens()
        rect = option.rect
        painter.save()
        painter.setClipRect(rect)
        paint_selection(painter, rect, option.state, tokens)
        if row.date_tile and not option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(hairline_pen(tokens.hairline))
            painter.setBrush(QColor(tokens.panel))
            painter.drawRoundedRect(QRectF(rect).adjusted(6, 4, -6, -4), 8, 8)
        inset = 18
        if row.glyph or row.date_tile:
            tile = QRect(rect.left() + 18, rect.top() + 14, 32, 34)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.canvas))
            painter.drawRoundedRect(QRectF(tile), 7, 7)
            if row.glyph:
                painter.drawPixmap(
                    tile.left() + 8,
                    tile.top() + 9,
                    icon_pixmap(
                        row.glyph, tokens.text_secondary, 16, painter.device().devicePixelRatioF()
                    ),
                )
            else:
                month, _, day = row.date_tile.partition("\n")
                painter.setPen(QColor(tokens.text_secondary))
                painter.setFont(ui_font(8, medium=True))
                painter.drawText(
                    tile.adjusted(0, 2, 0, -17), int(Qt.AlignmentFlag.AlignCenter), month
                )
                painter.setFont(ui_font(16, medium=True))
                painter.drawText(tile.adjusted(0, 13, 0, 0), int(Qt.AlignmentFlag.AlignCenter), day)
            inset += 44
        elif row.unread:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.primary))
            painter.drawEllipse(QRectF(rect.left() + 11, rect.top() + 19, 6, 6))
            inset += 8
        width = max(0, rect.width() - inset - 26)
        top = paint_lines(
            painter,
            rect.left() + inset,
            rect.top() + 12,
            width,
            row.title,
            elided(row.meta, ui_metrics(META_PX), width),
            title_color=tokens.text,
            subtitle_color=tokens.text_secondary,
        )
        if row.preview:
            metrics = ui_metrics(META_PX)
            painter.setFont(ui_font(META_PX))
            painter.setPen(QColor(tokens.text_muted))
            painter.drawText(
                QRect(rect.left() + inset, top + 4, width, metrics.height()),
                SINGLE_LINE,
                elided(row.preview, metrics, width),
            )
        painter.setFont(ui_font(15))
        painter.setPen(QColor(tokens.text_muted))
        painter.drawText(QRect(rect.right() - 23, rect.top(), 15, rect.height()), SINGLE_LINE, "›")
        paint_focus_ring(painter, rect, option.state, tokens.accent_fg)
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        row = index.data(DISPLAY_ROW_ROLE)
        if not isinstance(row, DisplayRow):
            return super().sizeHint(option, index)
        if row.date_tile:
            return QSize(0, 70)
        return QSize(
            0,
            24
            + ui_metrics(BODY_PX, medium=True).height()
            + ui_metrics(META_PX).height()
            + (
                ui_metrics(META_PX).height() + 4
                if isinstance(row, DisplayRow) and row.preview
                else 0
            ),
        )


class AgendaRowDelegate(DisplayRowDelegate):
    """Time column beside the event title, with compact day headings between groups."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(DISPLAY_ROW_ROLE)
        if not isinstance(row, DisplayRow):
            super().paint(painter, option, index)
            return
        tokens = current_tokens()
        rect = option.rect
        painter.save()
        painter.setClipRect(rect)
        paint_selection(painter, rect, option.state, tokens)
        day_width = min(145, max(90, rect.width() * 16 // 100))
        time_width = 82
        top = rect.top() + (20 if row.date_tile else 10)
        if row.date_tile:
            painter.setPen(hairline_pen(tokens.hairline))
            painter.drawLine(rect.left() + 24, rect.top(), rect.right() - 24, rect.top())
            day, _, month_date = row.date_tile.partition(", ")
            painter.setFont(ui_font(10, medium=True))
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(QRect(rect.left() + 24, top, day_width - 16, 16), SINGLE_LINE, day)
            painter.setFont(ui_font(8))
            painter.setPen(QColor(tokens.text_muted))
            painter.drawText(
                QRect(rect.left() + 24, top + 19, day_width - 16, 15), SINGLE_LINE, month_date
            )
        color = (
            tokens.warning_fg
            if row.accent == "marker"
            else tokens.success_fg
            if row.accent == "google"
            else tokens.primary
        )
        time_left = rect.left() + 24 + day_width
        painter.fillRect(QRect(time_left, top + 8, 2, 12), QColor(color))
        painter.setFont(ui_font(8))
        painter.setPen(QColor(color))
        painter.drawText(
            QRect(time_left + 10, top + 4, time_width - 16, 20),
            SINGLE_LINE,
            elided(row.meta, ui_metrics(8), time_width - 16),
        )
        paint_lines(
            painter,
            time_left + time_width,
            top,
            max(0, rect.width() - day_width - time_width - 60),
            row.title,
            row.preview,
            title_color=tokens.text,
            subtitle_color=tokens.text_secondary,
        )
        painter.setFont(ui_font(15))
        painter.setPen(QColor(tokens.text_muted))
        painter.drawText(QRect(rect.right() - 40, top, 15, 24), SINGLE_LINE, "›")
        paint_focus_ring(painter, rect, option.state, tokens.accent_fg)
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        row = index.data(DISPLAY_ROW_ROLE)
        return QSize(0, (74 if row.date_tile else 56) if isinstance(row, DisplayRow) else 0)
