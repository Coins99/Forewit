"""Painting Liquid Glass with QPainter, from the glass tokens at paint time.

Three surfaces: a panel (header, sidebar) showing the blurred backdrop through its tint, a
raised pill on a panel (the selected page) and a pill button. Each has opaque fallbacks:
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
    QRadialGradient,
)

from mailbrief.ui.glass.backdrop import Backdrop, color
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


def _crisp(painter: QPainter, rect: QRectF) -> QRectF:
    """``rect`` moved onto the device pixel grid, half a device pixel inside its edges, so a
    cosmetic 1 px line along it covers whole device pixels at any scale and position (at
    125% or 150% a logical edge falls between device pixels)."""
    to_device = painter.deviceTransform()
    to_logical, invertible = to_device.inverted()
    if not invertible:
        return rect
    edges = to_device.mapRect(rect)
    snapped = QRectF(
        QPointF(round(edges.left()) + 0.5, round(edges.top()) + 0.5),
        QPointF(round(edges.right()) - 0.5, round(edges.bottom()) - 0.5),
    )
    return to_logical.mapRect(snapped)


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


def paint_specular(painter: QPainter, rect: QRectF, glass: GlassTokens, point: QPointF) -> None:
    """The light a pointer at ``point`` casts on a pill's rim, brightest nearest it. Only
    the rim lights up, so text on the pill keeps its proven contrast."""
    light = QRadialGradient(point, rect.height() * 1.6)
    light.setColorAt(0.0, color(glass.specular))
    light.setColorAt(1.0, color(glass.specular, 0.0))
    _stroke_rim(painter, rect, rect.height() / 2, QBrush(light))


def _stroke_rim(painter: QPainter, rect: QRectF, radius: float, brush: QBrush) -> None:
    inner = _crisp(painter, rect)
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
    backdrop is ready, the tint lies over `opaque`."""
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
    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + SHEEN_DEPTH))
    sheen.setColorAt(0.0, color(glass.sheen))
    sheen.setColorAt(1.0, color(glass.sheen, 0.0))
    painter.fillRect(rect, sheen)
    pen = QPen(color(glass.edge_light), 1.0)
    pen.setCosmetic(True)
    painter.setPen(pen)
    top = _crisp(painter, rect).top()
    painter.drawLine(QPointF(rect.left(), top), QPointF(rect.right(), top))  # Lit top edge.
    painter.restore()


def _pill_path(rect: QRectF) -> tuple[QPainterPath, float]:
    radius = rect.height() / 2
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path, radius


def paint_raised(
    painter: QPainter, rect: QRectF, glass: GlassTokens, mode: GlassMode = GlassMode.NORMAL
) -> None:
    """The selected page's pill: a fill on the glass, never a second layer of glass."""
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
    paint_rim(painter, rect, radius, _rim_stops(glass, glass.rim_top, mode))


def paint_pill(
    painter: QPainter,
    rect: QRectF,
    glass: GlassTokens,
    *,
    primary: bool = False,
    mode: GlassMode = GlassMode.NORMAL,
) -> None:
    """A pill button's body and rim; lighter in its top half and shaded in its bottom half.
    The caller draws its icon and text."""
    path, radius = _pill_path(rect)
    middle = QPointF(rect.left(), rect.center().y())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if mode is not GlassMode.NORMAL:
        painter.fillPath(path, QColor(glass.opaque))
    painter.fillPath(path, color(glass.primary if primary else glass.pill))
    if mode is GlassMode.NORMAL:
        _fade(painter, path, rect.topLeft(), middle, glass.pill_sheen)
        _fade(painter, path, rect.bottomLeft(), middle, glass.pill_shade)
    painter.restore()
    top = glass.primary_rim if primary else glass.rim_top
    paint_rim(painter, rect, radius, _rim_stops(glass, top, mode))
