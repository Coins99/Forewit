"""Glass painters: text contrast on the pixels they actually paint, crisp rims at every
scale, and the opaque fallbacks."""

from collections.abc import Callable

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mailbrief.ui.glass.backdrop import SCALE, Backdrop, BackdropKey, render_backdrop
from mailbrief.ui.glass.button import PAD_Y
from mailbrief.ui.glass.painter import (
    GLOW_WIDTH,
    GlassMode,
    paint_glow,
    paint_panel,
    paint_pill,
    paint_raised,
    paint_rim,
)
from mailbrief.ui.theme import ColorScheme, GlassTokens, ThemeMode, glass_for, tokens_for
from tests.ui.theme.test_glass import SETS, contrast, luminance

WIDTH, HEIGHT = 440, 288
PATCH = 24  # A small panel, so its whole height lies under the sheen's strongest part.


def image(width: int, height: int, dpr: float = 1.0) -> QImage:
    result = QImage(
        round(width * dpr), round(height * dpr), QImage.Format.Format_ARGB32_Premultiplied
    )
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.GlobalColor.transparent)
    return result


def extreme_spots(backdrop: Backdrop) -> list[QPointF]:
    """The window positions of the backdrop's darkest and brightest pixels."""
    picture = backdrop.blurred
    spots = [
        (luminance(rgb_of(picture.pixelColor(x, y))), x, y)
        for y in range(picture.height())
        for x in range(picture.width())
    ]
    chosen = (min(spots), max(spots))
    return [QPointF(x / SCALE, y / SCALE) for _, x, y in chosen]


def rgb_of(color: QColor) -> tuple[float, float, float]:
    return (float(color.red()), float(color.green()), float(color.blue()))


def painted(
    glass: GlassTokens,
    backdrop: Backdrop | None,
    spot: QPointF,
    surface: Callable[[QPainter, QRectF], None] | None = None,
) -> list[tuple[float, float, float]]:
    """A panel over ``spot``, with ``surface`` painted on it, sampled away from its edges,
    where a glyph would sit."""
    picture = image(PATCH, PATCH)
    painter = QPainter(picture)
    rect = QRectF(0, 0, PATCH, PATCH)
    paint_panel(painter, rect, glass, backdrop, QRectF(spot.x(), spot.y(), PATCH, PATCH))
    if surface is not None:
        surface(painter, rect)
    painter.end()
    return [
        rgb_of(picture.pixelColor(x, y))
        for y in range(4, PATCH - 4)  # Clear of the lit top edge and any rim.
        for x in range(8, PATCH - 8)
    ]


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_text_on_painted_glass_meets_wcag_aa(
    qapp: QApplication, scheme: ColorScheme, mode: ThemeMode
) -> None:
    """On real pixels, including 8-bit rounding, over the backdrop's darkest and brightest
    spots. The blur keeps those inside the bounds test_glass.py proves contrast over, so
    that proof is the stricter check; this one checks the painters follow it."""
    tokens, glass = tokens_for(mode, scheme), glass_for(mode, scheme)
    backdrop = render_backdrop(BackdropKey(WIDTH, HEIGHT, scheme, mode))
    surfaces: list[tuple[str, Callable[[QPainter, QRectF], None] | None, tuple[str, ...]]] = [
        ("panel", None, (tokens.text, tokens.text_secondary)),
        ("raised", lambda p, r: paint_raised(p, r, glass), (tokens.text,)),
        ("pill", lambda p, r: paint_pill(p, r, glass), (tokens.text,)),
        ("primary", lambda p, r: paint_pill(p, r, glass, primary=True), (tokens.text,)),
    ]
    for spot in extreme_spots(backdrop):
        for source in (backdrop, None):  # Also before the backdrop is ready.
            for name, surface, texts in surfaces:
                colours = painted(glass, source, spot, surface)
                for text in texts:
                    worst = min(contrast(text, colour) for colour in colours)
                    assert worst >= 4.5, (scheme, mode, name, text, round(worst, 2))


@pytest.mark.parametrize("offset", [0.0, 3.0])
@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_a_rim_is_one_crisp_device_pixel(qapp: QApplication, dpr: float, offset: float) -> None:
    """At 125% and 150% a logical edge at an odd position falls between device pixels; the
    rim is snapped onto the device grid, so it is still one full pixel."""
    picture = image(110, 50, dpr)
    painter = QPainter(picture)
    painter.translate(offset, offset)
    white = QColor(Qt.GlobalColor.white)
    paint_rim(painter, QRectF(0, 0, 100, 40), 20, (white, white, white))
    painter.end()
    column = round((offset + 50) * dpr)  # Where the top edge is straight.
    alphas = [picture.pixelColor(column, y).alpha() for y in range(round((offset + 10) * dpr))]
    lit = [alpha for alpha in alphas if alpha]
    assert lit == [255], (dpr, offset, alphas)


def test_a_panel_wider_than_its_backdrop_stays_opaque(qapp: QApplication) -> None:
    """While a window grows, the last backdrop may not reach the panel's new edge."""
    glass = glass_for(ThemeMode.DARK)
    small = render_backdrop(BackdropKey(200, 100, ColorScheme.SLATE, ThemeMode.DARK))
    picture = image(400, 60)
    painter = QPainter(picture)
    paint_panel(painter, QRectF(0, 0, 400, 60), glass, small, QRectF(0, 0, 400, 60))
    painter.end()
    alphas = {picture.pixelColor(x, y).alpha() for y in range(0, 60, 5) for x in range(0, 400, 5)}
    assert alphas == {255}


@pytest.mark.parametrize("mode", [GlassMode.REDUCED_TRANSPARENCY, GlassMode.INCREASED_CONTRAST])
def test_fallback_panels_are_opaque_with_no_backdrop(qapp: QApplication, mode: GlassMode) -> None:
    glass = glass_for(ThemeMode.DARK)
    backdrop = render_backdrop(BackdropKey(WIDTH, HEIGHT, ColorScheme.SLATE, ThemeMode.DARK))
    picture = image(PATCH, PATCH)
    painter = QPainter(picture)
    paint_panel(
        painter, QRectF(0, 0, PATCH, PATCH), glass, backdrop, QRectF(0, 0, PATCH, PATCH), mode
    )
    painter.end()
    colours = {picture.pixelColor(x, y).name() for y in range(PATCH) for x in range(PATCH)}
    assert colours == {glass.opaque}


def test_increased_contrast_draws_solid_rims(qapp: QApplication) -> None:
    glass = glass_for(ThemeMode.DARK)
    picture = image(100, 40)
    painter = QPainter(picture)
    paint_pill(painter, QRectF(0, 0, 100, 40), glass, mode=GlassMode.INCREASED_CONTRAST)
    paint_raised(painter, QRectF(0, 0, 100, 40), glass, GlassMode.INCREASED_CONTRAST)
    painter.end()
    for x in (50, 30, 70):
        assert picture.pixelColor(x, 0).name() == glass.rim_contrast
        assert picture.pixelColor(x, 39).name() == glass.rim_contrast


def test_a_normal_pill_is_lighter_on_top_and_shaded_below(qapp: QApplication) -> None:
    glass = glass_for(ThemeMode.DARK)
    picture = image(100, 40)
    picture.fill(QColor(tokens_for(ThemeMode.DARK).panel))
    painter = QPainter(picture)
    paint_pill(painter, QRectF(0, 0, 100, 40), glass)
    painter.end()
    top, middle, bottom = (picture.pixelColor(50, y).lightness() for y in (3, 20, 36))
    assert top > middle > bottom


def test_the_hover_glow_stays_in_a_band_inside_the_edge(qapp: QApplication) -> None:
    """Labels sit at least PAD_Y inside a pill, so a glow within GLOW_WIDTH of the edge
    never lies under text and its contrast stays proven."""
    assert GLOW_WIDTH < PAD_Y
    glass = glass_for(ThemeMode.DARK)
    width, height = 120, 40
    radius = height / 2
    picture = image(width, height)
    painter = QPainter(picture)
    paint_glow(painter, QRectF(0, 0, width, height), glass, QPointF(30, 20), 0.0)
    assert all(
        picture.pixelColor(x, y).alpha() == 0 for y in range(height) for x in range(width)
    )  # No spread yet: no light.
    paint_glow(painter, QRectF(0, 0, width, height), glass, QPointF(30, 20), 1.0)
    painter.end()
    lit = 0
    for y in range(height):
        for x in range(width):
            if not picture.pixelColor(x, y).alpha():
                continue
            lit += 1
            # Distance in from the stadium's edge, measured at the pixel's centre.
            px, py = x + 0.5, y + 0.5
            nearest_x = min(max(px, radius), width - radius)
            inside = radius - ((px - nearest_x) ** 2 + (py - radius) ** 2) ** 0.5
            assert inside <= GLOW_WIDTH + 1, (x, y, inside)
    assert lit > 0
