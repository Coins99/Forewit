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
    paint_highlight,
    paint_liquid_pill,
    paint_panel,
    paint_pill,
    paint_raised,
    paint_rim,
)
from mailbrief.ui.theme import ColorScheme, GlassTokens, ThemeMode, Tint, glass_for, tokens_for
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
        ("primary", lambda p, r: paint_pill(p, r, glass, primary=True), (glass.primary_text,)),
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
    glass = glass_for(ThemeMode.DARK, ColorScheme.SLATE)
    picture = image(100, 40)
    picture.fill(QColor(tokens_for(ThemeMode.DARK, ColorScheme.SLATE).panel))
    painter = QPainter(picture)
    paint_pill(painter, QRectF(0, 0, 100, 40), glass)
    painter.end()
    top, middle, bottom = (picture.pixelColor(50, y).lightness() for y in (3, 20, 36))
    assert top > middle > bottom


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_a_calm_pill_is_flat(qapp: QApplication, mode: ThemeMode) -> None:
    """Studio has no sheen or shade: a pill is one colour from top to bottom."""
    picture = image(100, 40)
    picture.fill(QColor(tokens_for(mode, ColorScheme.STUDIO).panel))
    painter = QPainter(picture)
    paint_pill(painter, QRectF(0, 0, 100, 40), glass_for(mode, ColorScheme.STUDIO))
    painter.end()
    assert len({picture.pixelColor(50, y).name() for y in (3, 20, 36)}) == 1


def test_the_hover_glow_stays_in_a_band_inside_the_edge(qapp: QApplication) -> None:
    """Labels sit at least PAD_Y inside a pill, so a glow within GLOW_WIDTH of the edge
    never lies under text and its contrast stays proven."""
    assert GLOW_WIDTH < PAD_Y
    glass = glass_for(ThemeMode.DARK, ColorScheme.SLATE)  # A scheme that glows.
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


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_a_content_pill_lifts_into_glass_under_the_pointer(
    qapp: QApplication, mode: ThemeMode
) -> None:
    """In the content layer a pill rests as an outline and lifts into glass as the pointer
    arrives (WWDC25): its body fills, and a shadow appears under it. The primary pill is
    glass at rest and keeps its colour, so its white label keeps its contrast."""
    glass = glass_for(mode, ColorScheme.STUDIO)
    card = tokens_for(mode, ColorScheme.STUDIO).card

    def paint(primary: bool, hover: float) -> QImage:
        picture = image(110, 50)
        picture.fill(QColor(card))
        painter = QPainter(picture)
        rect = QRectF(5, 5, 100, 36)
        paint_liquid_pill(painter, rect, glass, primary=primary, hover=hover, on_glass=False)
        painter.end()
        return picture

    resting, lifted = paint(False, 0.0), paint(False, 1.0)
    assert resting.pixelColor(55, 23).name() == card  # An outline only…
    assert lifted.pixelColor(55, 23).name() != card  # …that fills as it lifts,
    assert resting.pixelColor(55, 43).name() == card  # and casts its shadow only then.
    if glass.shadow.alpha > 0:  # Dark glass, as a tab, casts none.
        assert lifted.pixelColor(55, 43).name() != card
    assert paint(True, 0.0).pixelColor(55, 23) == paint(True, 1.0).pixelColor(55, 23)


def test_light_enters_a_pill_along_its_long_edges(qapp: QApplication) -> None:
    """As on a Liquid Glass tab: a crisp line of light along the top and an almost equal
    one along the bottom, fading out round the ends."""
    picture = image(120, 40)
    picture.fill(QColor("#000000"))
    painter = QPainter(picture)
    paint_highlight(painter, QRectF(0, 0, 120, 40), Tint("#ffffff", 1.0))
    painter.end()

    def lit(x: int, y: int) -> int:
        return max(
            picture.pixelColor(x + dx, y + dy).lightness() for dx in (-1, 0, 1) for dy in (0, 1)
        )

    top, bottom, end = lit(60, 0), lit(60, 38), lit(1, 19)
    assert top > 200 and bottom > 180  # Both long edges are lit…
    assert top >= bottom
    assert end < 40  # …and the light never reaches the ends' sides.


def test_a_dark_glass_pill_matches_the_tab_it_is_measured_on(qapp: QApplication) -> None:
    """On the tab's toolbar grey: a body a shade lighter, the light on both long edges
    falling steeply into a faint lensed glow, no shadow above or below, and the ends
    shaded darker than the toolbar."""
    glass = glass_for(ThemeMode.DARK, ColorScheme.STUDIO)
    picture = image(220, 60, 2.0)
    picture.fill(QColor("#424448"))
    painter = QPainter(picture)
    paint_liquid_pill(painter, QRectF(10, 15, 195, 29), glass, on_glass=True)
    painter.end()
    background = QColor("#424448").lightness()

    def at(x: int, y: int) -> int:
        return picture.pixelColor(x, y).lightness()

    column = [at(215, y) for y in range(20, 100)]  # The middle, in device pixels.
    body = at(215, 59)
    assert 0 < body - background <= 10  # A body a shade lighter than the toolbar.
    assert max(column) - body > 40  # A crisp line of light at an edge…
    assert at(215, 36) - body < 6  # …that has faded into the body a few points in.
    assert min(column) >= background - 2  # No shadow above or below.
    end = min(at(x, 59) for x in range(16, 22))  # Round the left end's outer edge.
    assert end < background - 8  # The ends are shaded darker than the toolbar.


def test_a_calm_selected_pill_has_no_rim(qapp: QApplication) -> None:
    """Studio's selected page is a soft grey capsule, as Apple's selected controls: its
    edge is its fill, with no rim, except when contrast is increased."""
    glass = glass_for(ThemeMode.LIGHT, ColorScheme.STUDIO)

    def top_edge(mode: GlassMode) -> str:
        picture = image(100, 40)
        picture.fill(QColor("#ffffff"))
        painter = QPainter(picture)
        paint_raised(painter, QRectF(0, 0, 100, 40), glass, mode)
        painter.end()
        return picture.pixelColor(50, 0).name()

    middle = image(100, 40)
    middle.fill(QColor("#ffffff"))
    painter = QPainter(middle)
    paint_raised(painter, QRectF(0, 0, 100, 40), glass)
    painter.end()
    assert top_edge(GlassMode.NORMAL) == middle.pixelColor(50, 20).name()
    assert top_edge(GlassMode.INCREASED_CONTRAST) == glass.rim_contrast
