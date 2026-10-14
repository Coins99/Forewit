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


# Liquid Glass's layers on a pill (WWDC25), for schemes whose highlight token is lit. The
# light follows a Liquid Glass tab's: it enters along the two long edges, so the rim has a
# crisp line of light along the top and an almost equal one along the bottom, and each
# glows into the body (lensing: the edges concentrate light, the middle is darkest).
# Round the ends the light fades out, and the outermost edge falls into shade.
# Each profile is (position down the pill as a share of its height, strength).
# Measured on a tab: its rim line is +51 over the body at the top and +45 at the bottom;
# the glow falls steeply within a point of each edge, then fades over about six.
RIM_LIGHT = ((0.0, 1.0), (0.18, 0.45), (0.34, 0.0), (0.66, 0.0), (0.82, 0.4), (1.0, 0.9))
LENS_LIGHT = (
    (0.0, 1.0),
    (0.04, 0.35),
    (0.1, 0.15),
    (0.22, 0.0),
    (0.78, 0.0),
    (0.9, 0.15),
    (0.96, 0.35),
    (1.0, 0.9),
)
# The ends' shade by distance in from each end, as a share of the pill's radius: darkest
# at the very end, faint half a radius in, gone by one (measured on the tab: -29, -13 and
# -3 levels at 0, 0.3 and 0.55 radii).
END_SHADE = ((0.0, 1.0), (0.31, 0.45), (0.55, 0.1), (1.0, 0.0))
# The shaded ends and the contact ring: how far outside the edge each line sits and how
# wide it is (logical px).
EDGE_OFFSET = 0.5
EDGE_WIDTH = 1.25
# A soft shadow below the pill, within the button's room and never above it: (spread,
# drop, share of the shadow token's alpha).
SHADOW_RINGS = ((1.0, 1.5, 0.35), (2.0, 2.5, 0.15))


def paint_liquid_pill(
    painter: QPainter,
    rect: QRectF,
    glass: GlassTokens,
    *,
    primary: bool = False,
    mode: GlassMode = GlassMode.NORMAL,
    hover: float = 0.0,
) -> None:
    """A pill as Liquid Glass, lit as a Liquid Glass tab is: a frosted body; light that
    enters along its long edges, as a crisp rim line along the top and the bottom with a
    glow fading into the body; shaded ends; and a soft shadow below.

    Every pill is glass at rest, wherever it sits, as Disconnect in Settings is; under
    the pointer its body brightens with `pill_hover` at ``hover``'s strength (0 to 1). The
    primary pill's glow stays off its body, so its white label keeps its contrast. The
    solid modes keep the body but paint no light or shadow."""
    hover = _unit(hover)
    presence = 1.0
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
        painter.fillPath(path, color(glass.pill))
        if hover > 0:
            painter.fillPath(path, color(glass.pill_hover, hover))
        if normal and presence > 0 and glass.lens.alpha > 0:
            painter.fillPath(path, _profile(rect, glass.lens, LENS_LIGHT, presence))
    painter.restore()
    if mode is GlassMode.INCREASED_CONTRAST:
        solid = QColor(glass.rim_contrast)
        paint_rim(painter, rect, radius, (solid, solid, solid))
    elif primary:
        paint_rim(painter, rect, radius, _rim_stops(glass, glass.primary_rim, mode))
    if normal and presence > 0:
        _paint_edges(painter, rect, glass, presence)
    if normal and presence > 0 and glass.highlight.alpha > 0:
        paint_highlight(painter, rect, glass.highlight, presence)


def _profile(
    rect: QRectF, light: Tint, profile: tuple[tuple[float, float], ...], strength: float
) -> QBrush:
    """``light`` down the height of ``rect``, at each profile stop's share of its alpha."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    for at, share in profile:
        gradient.setColorAt(at, color(light, share * _unit(strength)))
    return QBrush(gradient)


def paint_highlight(painter: QPainter, rect: QRectF, light: Tint, strength: float = 1.0) -> None:
    """The light on a pill's rim: one device pixel, a crisp line along the top and an
    almost equal one along the bottom, fading out round the ends."""
    _stroke_rim(painter, rect, rect.height() / 2, _profile(rect, light, RIM_LIGHT, strength))


def _paint_edges(painter: QPainter, rect: QRectF, glass: GlassTokens, strength: float) -> None:
    """Just outside the pill: an even contact ring (`edge`), and the shade at its ends
    (`end_shade`), darkest where they face sideways, where the light that runs along the
    long edges never reaches."""
    ring = rect.adjusted(-EDGE_OFFSET, -EDGE_OFFSET, EDGE_OFFSET, EDGE_OFFSET)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush())
    if glass.edge.alpha > 0:
        painter.setPen(QPen(color(glass.edge, _unit(strength)), EDGE_WIDTH))
        painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
    if glass.end_shade.alpha > 0:
        painter.setPen(QPen(_end_shade(ring, glass.end_shade, strength), EDGE_WIDTH))
        painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
    painter.restore()


def _end_shade(rect: QRectF, shade: Tint, strength: float) -> QBrush:
    """``shade`` across ``rect``: darkest at each end, fading in by END_SHADE, clear in the
    middle."""
    gradient = QLinearGradient(rect.topLeft(), rect.topRight())
    reach = min(0.5, rect.height() / 2 / max(rect.width(), 1.0))  # One radius, as a share.
    for at, share in END_SHADE:
        alpha = share * _unit(strength)
        gradient.setColorAt(at * reach, color(shade, alpha))
        gradient.setColorAt(1.0 - at * reach, color(shade, alpha))
    return QBrush(gradient)


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
