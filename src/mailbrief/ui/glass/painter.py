"""Painting Liquid Glass with QPainter, from the glass tokens at paint time.

Two surfaces: a panel (header, sidebar, a dialog's frame) showing the blurred backdrop
through its tint, and a raised pill on a panel (the selected page). Buttons are flat and
drawn by ThemeStyle (ui/theme/style.py), never glass. Each surface has opaque fallbacks:
reduced transparency fills a panel with `opaque` and drops the backdrop and sheen;
increased contrast does that too and draws every rim solid in `rim_contrast`.
"""

from enum import Enum

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from mailbrief.ui.glass.backdrop import Backdrop, color
from mailbrief.ui.hairline import crisp_rect
from mailbrief.ui.theme import GlassTokens, Tint

# How far a panel's sheen reaches down from its top, in logical pixels.
SHEEN_DEPTH = 120.0


class GlassMode(Enum):
    NORMAL = "normal"
    REDUCED_TRANSPARENCY = "reduced_transparency"
    INCREASED_CONTRAST = "increased_contrast"


def _fade(painter: QPainter, path: QPainterPath, start: QPointF, end: QPointF, tint: Tint) -> None:
    """Fill ``path`` with ``tint`` at ``start``, fading to clear at ``end``."""
    gradient = QLinearGradient(start, end)
    gradient.setColorAt(0.0, color(tint))
    gradient.setColorAt(1.0, color(tint, 0.0))
    painter.fillPath(path, gradient)


def paint_rim(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    stops: tuple[QColor, QColor, QColor],
) -> None:
    """A 1 device pixel rim inside ``rect``, on the device pixel grid: bright at the top
    left, through ``stops[1]``, to ``stops[2]`` at the bottom right."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, stops[0])
    gradient.setColorAt(0.55, stops[1])
    gradient.setColorAt(1.0, stops[2])
    _stroke_rim(painter, rect, radius, QBrush(gradient))


def _stroke_rim(painter: QPainter, rect: QRectF, radius: float, brush: QBrush) -> None:
    inner = crisp_rect(painter, rect)
    inset = (rect.width() - inner.width()) / 2
    pen = QPen(brush, 1.0)
    pen.setCosmetic(True)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(QBrush())
    painter.drawRoundedRect(inner, max(radius - inset, 0.0), max(radius - inset, 0.0))
    painter.restore()


def _rim_stops(glass: GlassTokens, top: Tint, mode: GlassMode) -> tuple[QColor, QColor, QColor]:
    if mode is GlassMode.INCREASED_CONTRAST:
        solid = QColor(glass.rim_contrast)
        return (solid, solid, solid)
    return (color(top), color(glass.rim_mid), color(glass.rim_bottom))


def paint_panel(
    painter: QPainter,
    rect: QRectF,
    glass: GlassTokens,
    backdrop: Backdrop | None,
    area: QRectF,
    mode: GlassMode = GlassMode.NORMAL,
) -> None:
    """A glass panel filling ``rect``. ``area`` is the same rectangle in the window's
    logical coordinates, which says what part of the backdrop shows through. Until the
    backdrop is ready, the tint lies over `opaque`.

    Panels side by side are one sheet of glass: the sheen is measured from the window's
    top, not the panel's, and only a panel at the window's top has the lit edge."""
    painter.save()
    if mode is not GlassMode.NORMAL:
        painter.fillRect(rect, QColor(glass.opaque))
        painter.restore()
        return
    # Under the backdrop too: while a window grows, the last backdrop may not reach the
    # panel's new edge, and the tint alone would let what lies below show through.
    painter.fillRect(rect, QColor(glass.opaque))
    if backdrop is not None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, backdrop.blurred, backdrop.source(area))
    painter.fillRect(rect, color(glass.tint))
    window_top = rect.top() - area.top()  # The window's top, in ``rect``'s coordinates.
    if window_top + SHEEN_DEPTH > rect.top():
        sheen = QLinearGradient(
            QPointF(rect.left(), window_top), QPointF(rect.left(), window_top + SHEEN_DEPTH)
        )
        sheen.setColorAt(0.0, color(glass.sheen))
        sheen.setColorAt(1.0, color(glass.sheen, 0.0))
        painter.fillRect(rect, sheen)
    if area.top() <= 0:
        pen = QPen(color(glass.edge_light), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        top = crisp_rect(painter, rect).top()
        painter.drawLine(QPointF(rect.left(), top), QPointF(rect.right(), top))  # Lit edge.
    painter.restore()


def _pill_path(rect: QRectF) -> tuple[QPainterPath, float]:
    radius = rect.height() / 2
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path, radius


def paint_raised(
    painter: QPainter, rect: QRectF, glass: GlassTokens, mode: GlassMode = GlassMode.NORMAL
) -> None:
    """The selected page's pill: a fill on the glass, never a second layer of glass. Its
    rim is lit from `raised_rim`; a calm scheme turns it off (alpha 0), as Apple's
    selected controls have none, except when contrast is increased."""
    path, radius = _pill_path(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(path, color(glass.raised))
    if mode is GlassMode.NORMAL:
        top = rect.topLeft()
        _fade(
            painter, path, top, QPointF(top.x(), top.y() + rect.height() * 0.6), glass.raised_sheen
        )
    painter.restore()
    if glass.raised_rim.alpha > 0 or mode is GlassMode.INCREASED_CONTRAST:
        paint_rim(painter, rect, radius, _rim_stops(glass, glass.raised_rim, mode))
