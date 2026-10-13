"""Hairlines painted with cosmetic pens: one device pixel at every scale, unlike QSS borders,
which round to whole logical pixels and blur at fractional scales."""

import math
from typing import Final

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QSplitter, QSplitterHandle, QStyle, QWidget

from mailbrief.ui.theme import CARD_RADIUS, RADIUS, current_tokens

# A selected row's rounded fill, inset from the row's sides and from its top and bottom;
# the focus ring follows it.
SELECTION_INSET_X: Final = 6
SELECTION_INSET_Y: Final = 2
# How far a splitter's line stops short of its ends (logical px).
SPLITTER_INSET: Final = 12


def crisp_rect(painter: QPainter, rect: QRectF) -> QRectF:
    """``rect`` moved onto the device pixel grid, half a device pixel inside its edges, so a
    cosmetic 1 px line along it covers whole device pixels at any scale and position (at
    125% or 150% a logical edge falls between device pixels).

    Edges round inward, to the first and last whole device pixels inside ``rect``: a
    widget's painting is clipped there, and a line rounded outward would be cut off."""
    to_device = painter.deviceTransform()
    to_logical, invertible = to_device.inverted()
    if not invertible:
        return rect
    edges = to_device.mapRect(rect)
    snapped = QRectF(
        QPointF(math.ceil(edges.left()) + 0.5, math.ceil(edges.top()) + 0.5),
        QPointF(math.floor(edges.right()) - 0.5, math.floor(edges.bottom()) - 0.5),
    )
    return to_logical.mapRect(snapped)


def crisp_fill_rect(painter: QPainter, rect: QRectF) -> QRectF:
    """``rect`` snapped inward to whole device pixels, through the painter's device
    transform, so a fill's straight edges are crisp at any scale: the outer edges of the
    pixels `crisp_rect` puts a hairline on."""
    to_device = painter.deviceTransform()
    to_logical, invertible = to_device.inverted()
    if not invertible:
        return rect
    edges = to_device.mapRect(rect)
    snapped = QRectF(
        QPointF(math.ceil(edges.left()), math.ceil(edges.top())),
        QPointF(math.floor(edges.right()), math.floor(edges.bottom())),
    )
    return to_logical.mapRect(snapped)


def hairline_pen(color: str) -> QPen:
    """A cosmetic pen: width 0 is always one device pixel."""
    pen = QPen(QColor(color), 0)
    pen.setCosmetic(True)
    return pen


def keyboard_focus(state: QStyle.StateFlag) -> bool:
    """The focus ring shows once the keyboard has moved focus, not on first show."""
    return bool(
        state & QStyle.StateFlag.State_HasFocus
        and state & QStyle.StateFlag.State_KeyboardFocusChange
    )


def paint_focus_ring(
    painter: QPainter,
    rect: QRect,
    state: QStyle.StateFlag,
    color: str,
    *,
    inset: tuple[float, float] = (SELECTION_INSET_X, SELECTION_INSET_Y),
    radius: float | None = RADIUS,
) -> None:
    """A rounded one-device-pixel ring round a row's selection (``inset`` from ``rect``),
    once the keyboard has moved focus. ``radius`` None rounds it into a pill."""
    if not keyboard_focus(state):
        return
    ring = QRectF(rect).adjusted(inset[0], inset[1], -inset[0], -inset[1])
    corner = ring.height() / 2 if radius is None else radius
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(hairline_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    edge = crisp_rect(painter, ring)
    painter.drawRoundedRect(edge, corner, corner)
    painter.restore()


class HairlineFrame(QFrame):
    """A card: its contents on a rounded ``card`` fill, set apart by tone rather than a
    border or shadow. A ``strong`` card also has a hairline border in ``border_strong``."""

    def __init__(
        self, parent: QWidget | None = None, *, radius: int = CARD_RADIUS, strong: bool = False
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._strong = strong
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContentsMargins(16, 14, 16, 14)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # On device pixel centres, so the border stays crisp at 125% and 150% too; the
        # fill reaches the outer edges of those pixels, so its straight edges are crisp.
        rect = crisp_rect(painter, QRectF(self.rect()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.card))
        fill = crisp_fill_rect(painter, QRectF(self.rect()))
        painter.drawRoundedRect(fill, self._radius, self._radius)
        if self._strong:
            painter.setPen(hairline_pen(tokens.border_strong))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.end()


class HairlineDivider(QWidget):
    """A one-pixel line between panes."""

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        if orientation is Qt.Orientation.Horizontal:
            self.setFixedHeight(1)
        else:
            self.setFixedWidth(1)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(hairline_pen(current_tokens().hairline))
        if self._orientation is Qt.Orientation.Horizontal:
            painter.drawLine(QPointF(0, 0), QPointF(self.width(), 0))
        else:
            painter.drawLine(QPointF(0, 0), QPointF(0, self.height()))
        painter.end()


class _HairlineHandle(QSplitterHandle):
    """A handle wide enough to grab, drawn as one hairline at its centre. The line stops
    SPLITTER_INSET short of both ends, so it never meets another hairline (the sheet's
    edge) at a sharp corner."""

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.panel))
        painter.setPen(hairline_pen(tokens.hairline))
        if self.orientation() is Qt.Orientation.Horizontal:
            x = self.width() // 2
            end = max(SPLITTER_INSET, self.height() - SPLITTER_INSET)
            painter.drawLine(QPointF(x, SPLITTER_INSET), QPointF(x, end))
        else:
            y = self.height() // 2
            end = max(SPLITTER_INSET, self.width() - SPLITTER_INSET)
            painter.drawLine(QPointF(SPLITTER_INSET, y), QPointF(end, y))
        painter.end()


class HairlineSplitter(QSplitter):
    """A splitter whose handle looks like a single hairline."""

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        # Five pixels to grab; only the centre one is drawn.
        self.setHandleWidth(5)

    def createHandle(self) -> QSplitterHandle:
        return _HairlineHandle(self.orientation(), self)
