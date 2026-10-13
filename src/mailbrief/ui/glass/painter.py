"""Painting Liquid Glass with QPainter, from the glass tokens at paint time.

Three surfaces: a panel (header, sidebar) showing the blurred backdrop through its tint, a
raised pill on a panel (the selected page) and a pill button. Each has opaque fallbacks:
reduced transparency fills a panel with `opaque` and drops the backdrop and sheen;
increased contrast does that too and draws every rim solid in `rim_contrast`.
"""

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

from mailbrief.ui.glass.backdrop import Backdrop, color
from mailbrief.ui.hairline import crisp_rect
from mailbrief.ui.theme import GlassTokens, Tint

# How far a panel's sheen reaches down from its top, in logical pixels.
SHEEN_DEPTH = 120.0
# A hovered pill's glow: how far it reaches in from the edge, in logical pixels (labels
# sit at least 6 px in), and its strength as a share of the specular token's alpha.
GLOW_WIDTH = 3.0
GLOW_STRENGTH = 0.45


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


def paint_specular(
    painter: QPainter, rect: QRectF, glass: GlassTokens, point: QPointF, strength: float = 1.0
) -> None:
    """The light a pointer at ``point`` casts on a pill's rim, brightest nearest it, at
    ``strength`` (0 to 1) of the specular token. Only the rim lights up, so text on the
    pill keeps its proven contrast."""
    light = QRadialGradient(point, rect.height() * 1.6)
    light.setColorAt(0.0, color(glass.specular, _unit(strength)))
    light.setColorAt(1.0, color(glass.specular, 0.0))
    _stroke_rim(painter, rect, rect.height() / 2, QBrush(light))


def paint_glow(
    painter: QPainter, rect: QRectF, glass: GlassTokens, point: QPointF, spread: float
) -> None:
    """Light from inside a pill's edge, as Apple's glass lights from within: it starts
    under the pointer at ``point`` and spreads round the pill as ``spread`` goes from 0 to
    1. It stays within GLOW_WIDTH of the edge, clear of the label (which sits at least
    PAD_Y inside), so text keeps its proven contrast."""
    spread = _unit(spread)
    if spread <= 0:
        return
    path, _radius = _pill_path(rect)
    reach = rect.height() * 0.5 + rect.width() * 1.1 * spread
    light = QRadialGradient(point, reach)
    light.setColorAt(0.0, color(glass.specular, GLOW_STRENGTH * spread))
    light.setColorAt(1.0, color(glass.specular, 0.0))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(path)  # The stroke's outer half falls outside: a band inside.
    painter.setPen(QPen(QBrush(light), 2 * GLOW_WIDTH))
    painter.setBrush(QBrush())
    painter.drawPath(path)
    painter.restore()


def _unit(value: float) -> float:
    return max(0.0, min(value, 1.0))


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


def paint_pill(
    painter: QPainter,
    rect: QRectF,
    glass: GlassTokens,
    *,
    primary: bool = False,
    mode: GlassMode = GlassMode.NORMAL,
    hover: float = 0.0,
) -> None:
    """A pill button's body and rim; lighter in its top half and shaded in its bottom half.
    A pill that isn't primary fills with `pill_hover` at ``hover`` (0 to 1) of its
    strength as the pointer lifts it. The caller draws its icon and text."""
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
    if not primary and hover > 0 and glass.pill_hover.alpha > 0:
        painter.fillPath(path, color(glass.pill_hover, _unit(hover)))
    painter.restore()
    top = glass.primary_rim if primary else glass.rim_top
    paint_rim(painter, rect, radius, _rim_stops(glass, top, mode))


# Liquid Glass's layers on a pill (WWDC25), for schemes whose highlight token is lit.
# The lensing band's width inside the edge (labels sit PAD_Y, 6 px, in), and the shadow's
# rings: (spread, drop, share of the shadow token's alpha), within the button's room round
# the pill. The first is an even contact edge all round, as a glass tab's dark outline
# (in light mode, Apple's subtle grey ring); the others fall below it, softly.
LENS_WIDTH = 3.0
SHADOW_RINGS = ((0.75, 0.0, 0.9), (1.5, 1.0, 0.35), (2.5, 2.0, 0.15))
# The specular highlight round the rim: (angle as a share of a turn from three o'clock,
# anticlockwise; strength). Bright along the top and brightest at the top left, where the
# light falls; softer along the bottom, reflected most at the bottom right.
HIGHLIGHT_STOPS = (
    (0.0, 0.3),
    (0.1, 0.5),
    (0.25, 0.85),
    (0.375, 1.0),
    (0.5, 0.45),
    (0.6, 0.2),
    (0.75, 0.35),
    (0.875, 0.6),
    (1.0, 0.3),
)


def paint_liquid_pill(
    painter: QPainter,
    rect: QRectF,
    glass: GlassTokens,
    *,
    primary: bool = False,
    mode: GlassMode = GlassMode.NORMAL,
    hover: float = 0.0,
    on_glass: bool = True,
) -> None:
    """A pill as Liquid Glass (WWDC25): a soft contact shadow, a frosted body, a lensing
    band that concentrates light just inside the edge, and a specular highlight round the
    rim, lit at the top left and reflected at the bottom right.

    On the glass (``on_glass``, the header and sidebar) and for the primary pill, all of it
    shows at rest. In the content layer a pill rests as an outline in `rim_top` and lifts
    into glass as ``hover`` goes from 0 to 1, as WWDC's controls "lift up into Liquid
    Glass" on interaction. The body fills with `pill_hover` at the hover's strength. The
    solid modes keep the outline and body but paint no light or shadow."""
    hover = _unit(hover)
    presence = 1.0 if on_glass or primary else hover
    normal = mode is GlassMode.NORMAL
    path, radius = _pill_path(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if normal and presence > 0 and glass.shadow.alpha > 0:
        _paint_shadow(painter, rect, path, glass.shadow, presence)
    if not normal:
        painter.fillPath(path, QColor(glass.opaque))
    if primary:
        painter.fillPath(path, color(glass.primary))
    else:
        if on_glass:
            painter.fillPath(path, color(glass.pill))
        if hover > 0:
            painter.fillPath(path, color(glass.pill_hover, hover))
    if normal and presence > 0 and glass.lens.alpha > 0:
        _paint_lens(painter, rect, path, glass.lens, presence)
    painter.restore()
    if mode is GlassMode.INCREASED_CONTRAST:
        solid = QColor(glass.rim_contrast)
        paint_rim(painter, rect, radius, (solid, solid, solid))
    elif primary:
        paint_rim(painter, rect, radius, _rim_stops(glass, glass.primary_rim, mode))
    elif not on_glass and presence < 1:
        fade = 1.0 - presence
        stops = (
            color(glass.rim_top, fade),
            color(glass.rim_mid, fade),
            color(glass.rim_bottom, fade),
        )
        paint_rim(painter, rect, radius, stops)
    if normal and presence > 0 and glass.highlight.alpha > 0:
        paint_highlight(painter, rect, glass.highlight, presence)


def paint_highlight(painter: QPainter, rect: QRectF, light: Tint, strength: float = 1.0) -> None:
    """The specular highlight round a pill's rim: one device pixel, brightest at the top
    left where the light falls and again, fainter, where it reflects at the bottom
    right."""
    rim = QConicalGradient(rect.center(), 0.0)
    for at, share in HIGHLIGHT_STOPS:
        rim.setColorAt(at, color(light, share * _unit(strength)))
    _stroke_rim(painter, rect, rect.height() / 2, QBrush(rim))


def _paint_shadow(
    painter: QPainter, rect: QRectF, path: QPainterPath, shadow: Tint, strength: float
) -> None:
    """A soft shadow under the pill, outside it only, so it never darkens the body behind
    the label."""
    outside = QPainterPath()
    outside.addRect(rect.adjusted(-8, -8, 8, 8))
    painter.save()
    painter.setClipPath(outside.subtracted(path))
    painter.setPen(QPen(Qt.PenStyle.NoPen))
    for spread, drop, share in SHADOW_RINGS:
        ring = rect.adjusted(-spread, -spread + drop, spread, spread + drop)
        painter.setBrush(color(shadow, share * strength))
        painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
    painter.restore()


def _paint_lens(
    painter: QPainter, rect: QRectF, path: QPainterPath, lens: Tint, strength: float
) -> None:
    """Light concentrated just inside the edge: bright along the top, clear by the middle,
    and a faint refracted shade along the bottom."""
    band = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    band.setColorAt(0.0, color(lens, strength))
    band.setColorAt(0.45, color(lens, 0.0))
    shade = QColor(Qt.GlobalColor.black)
    shade.setAlphaF(0.0)
    band.setColorAt(0.8, shade)
    shade = QColor(Qt.GlobalColor.black)
    shade.setAlphaF(lens.alpha * 0.12 * strength)
    band.setColorAt(1.0, shade)
    painter.save()
    painter.setClipPath(path)  # The stroke's outer half falls outside: a band inside.
    painter.setPen(QPen(QBrush(band), 2 * LENS_WIDTH))
    painter.setBrush(QBrush())
    painter.drawPath(path)
    painter.restore()
