"""Hairlines painted with cosmetic pens: one device pixel at every scale, unlike QSS borders,
which round to whole logical pixels and blur at fractional scales."""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QSplitter, QSplitterHandle, QStyle, QWidget

from mailbrief.ui.theme import RADIUS, current_tokens


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


def paint_focus_ring(painter: QPainter, rect: QRect, state: QStyle.StateFlag, color: str) -> None:
    """A one-device-pixel ring just inside ``rect``, once the keyboard has moved focus."""
    if not keyboard_focus(state):
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(hairline_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(rect).adjusted(1, 1, -1, -1))


class HairlineFrame(QFrame):
    """A card: a rounded hairline border around its contents."""

    def __init__(
        self, parent: QWidget | None = None, *, radius: int = RADIUS, strong: bool = False
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._strong = strong
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContentsMargins(12, 10, 12, 10)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(hairline_pen(tokens.border_strong if self._strong else tokens.hairline))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # On device pixel centres, so the line stays crisp at 125% and 150% too.
        rect = crisp_rect(painter, QRectF(self.rect()))
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
    """A handle wide enough to grab, drawn as one hairline at its centre."""

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.panel))
        painter.setPen(hairline_pen(tokens.hairline))
        if self.orientation() is Qt.Orientation.Horizontal:
            x = self.width() // 2
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        else:
            y = self.height() // 2
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
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
