"""Glass tokens: text on every glass surface meets WCAG AA over any backdrop pixel.

Glass is composited as Qt paints it, one layer over another in sRGB:
``alpha * layer + (1 - alpha) * below``. The backdrop's darkest pixel is its darker stop
and its brightest is its lighter stop under the brightest streak, and blurring only
averages pixels between the two. Each surface is checked bare and with its full sheen or
shade, so whatever part of a gradient a glyph lands on is covered.
"""

import dataclasses
import re

import pytest

from mailbrief.ui.theme import ColorScheme, GlassTokens, ThemeMode, Tint, glass_for, tokens_for
from tests.ui.theme.test_theme import contrast as hex_contrast

RGB = tuple[float, float, float]
# Every colour scheme in both modes.
SETS = [(scheme, mode) for scheme in ColorScheme for mode in ThemeMode]


def rgb(color: str) -> RGB:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def over(layer: Tint, below: RGB) -> RGB:
    top = rgb(layer.color)
    a = layer.alpha
    return (
        a * top[0] + (1 - a) * below[0],
        a * top[1] + (1 - a) * below[1],
        a * top[2] + (1 - a) * below[2],
    )


def layered(below: RGB, *layers: Tint) -> RGB:
    for layer in layers:
        below = over(layer, below)
    return below


def luminance(color: RGB) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: RGB) -> float:
    lighter, darker = sorted((luminance(rgb(foreground)), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def backdrop_extremes(g: GlassTokens) -> tuple[RGB, RGB]:
    """The backdrop's darkest and brightest possible pixels: either stop, the lighter
    under the brightest streak, each under every combination of its colour pools at
    full strength, painted in order. Blurring only averages between these."""
    top, bottom = rgb(g.backdrop_top), rgb(g.backdrop_bottom)
    darker, lighter = sorted((top, bottom), key=luminance)
    bases = [darker, lighter, over(g.streak, lighter)]
    pools = [g.pool_a, g.pool_b, g.pool_c]
    combos = [[pool for bit, pool in enumerate(pools) if mask >> bit & 1] for mask in range(8)]
    candidates = [layered(base, *combo) for base in bases for combo in combos]
    return min(candidates, key=luminance), max(candidates, key=luminance)


def panels(g: GlassTokens, tint: Tint) -> list[RGB]:
    """A glass panel over either backdrop extreme, bare and under its full sheen."""
    return [layered(b, tint, *sheen) for b in backdrop_extremes(g) for sheen in ((), (g.sheen,))]


def tints(g: GlassTokens) -> list[Tint]:
    """The panel tint as given and at the least opacity it may ever have."""
    return [g.tint, dataclasses.replace(g.tint, alpha=g.min_tint_alpha)]


def pills(fill: Tint, g: GlassTokens, below: list[RGB], *, hover: bool = True) -> list[RGB]:
    """A pill over each of ``below``: bare, under its sheen, under its shade and, unless
    it's the primary pill (which never takes it), under its full hover fill."""
    extras: list[tuple[Tint, ...]] = [(), (g.pill_sheen,), (g.pill_shade,)]
    if hover:  # The lensed glow lights a pill's body too (never the primary's).
        extras += [(g.pill_hover,), (g.lens,), (g.pill_hover, g.lens)]
    return [layered(b, fill, *extra) for b in below for extra in extras]


def test_compositing_matches_the_theme_contrast() -> None:
    assert contrast("#777777", rgb("#ffffff")) == pytest.approx(hex_contrast("#777777", "#ffffff"))
    half = over(Tint("#ffffff", 0.5), rgb("#000000"))
    assert half == pytest.approx((127.5, 127.5, 127.5))


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_glass_colours_are_hex_and_alphas_are_fractions(
    scheme: ColorScheme, mode: ThemeMode
) -> None:
    g = glass_for(mode, scheme)
    for field in dataclasses.fields(g):
        value = getattr(g, field.name)
        if isinstance(value, Tint):
            assert re.fullmatch(r"#[0-9a-f]{6}", value.color), field.name
            # 0 turns a light off: calm schemes have no streak, sheen or lit edge.
            assert 0 <= value.alpha <= 1, field.name
        elif isinstance(value, str):
            assert re.fullmatch(r"#[0-9a-f]{6}", value), field.name
    assert 0 < g.min_tint_alpha <= 1


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_the_tint_respects_its_minimum(scheme: ColorScheme, mode: ThemeMode) -> None:
    g = glass_for(mode, scheme)
    assert g.tint.alpha >= g.min_tint_alpha


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_streaks_only_lighten_the_backdrop(scheme: ColorScheme, mode: ThemeMode) -> None:
    """So the backdrop's darkest pixel is its darker stop."""
    g = glass_for(mode, scheme)
    streak = luminance(rgb(g.streak.color))
    assert streak >= luminance(rgb(g.backdrop_top))
    assert streak >= luminance(rgb(g.backdrop_bottom))


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_text_on_glass_meets_wcag_aa(scheme: ColorScheme, mode: ThemeMode) -> None:
    t, g = tokens_for(mode, scheme), glass_for(mode, scheme)
    for tint in tints(g):
        glass = panels(g, tint)
        raised = [layered(b, g.raised, g.raised_sheen) for b in glass]
        # Pills also sit on opaque surfaces: panes, cards, the opaque fallback and the header.
        content = [rgb(t.panel), rgb(t.card), rgb(g.opaque), rgb(t.canvas)]
        surfaces = [
            ("panel", glass, (t.text, t.text_secondary)),
            ("raised", raised, (t.text,)),
            ("pill", pills(g.pill, g, glass + content), (t.text,)),
            ("primary", pills(g.primary, g, glass + content, hover=False), (g.primary_text,)),
        ]
        for name, backgrounds, texts in surfaces:
            for text in texts:
                worst = min(contrast(text, background) for background in backgrounds)
                assert worst >= 4.5, (scheme, mode, name, text, tint.alpha, round(worst, 2))


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_focus_ring_stands_out_beside_glass(scheme: ColorScheme, mode: ThemeMode) -> None:
    """The ring is drawn just outside a pill, on the panel around it (WCAG 1.4.11)."""
    t, g = tokens_for(mode, scheme), glass_for(mode, scheme)
    around = [*panels(g, g.tint), rgb(t.panel), rgb(g.opaque)]
    assert min(contrast(t.accent_fg, background) for background in around) >= 3.0


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_opaque_fallbacks_meet_wcag(scheme: ColorScheme, mode: ThemeMode) -> None:
    """Reduced transparency and increased contrast paint glass opaque, with solid rims."""
    t, g = tokens_for(mode, scheme), glass_for(mode, scheme)
    for text in (t.text, t.text_secondary):
        assert hex_contrast(text, g.opaque) >= 4.5
    for background in (g.opaque, t.panel):
        assert hex_contrast(g.rim_contrast, background) >= 3.0
