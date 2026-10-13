"""Design tokens: the only place MailBrief's UI colours are defined.

Each colour scheme has a dark and a light set. Slate (grey-blue, after the Liquid Glass
prototype) is the default, and DARK and LIGHT are its sets. Graphite is the warm grey
measured from the first mockup (docs/ui/mockup-three-pane-dark.png). The other schemes keep
Slate's lightness and change its hue. Light sets are provisional until a light mockup is
approved.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ThemeMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


class ColorScheme(StrEnum):
    """The owner's choice of colours, saved on this device."""

    SLATE = "slate"  # Grey-blue, the default.
    GRAPHITE = "graphite"  # Warm grey, the first mockup's.
    OCEAN = "ocean"
    SAGE = "sage"
    PLUM = "plum"
    ROSE = "rose"


@dataclass(frozen=True, slots=True)
class Tokens:
    canvas: str  # Header bar.
    panel: str  # Window, panes and cards.
    selection: str  # Selected row and navigation item, hover.
    hairline: str  # Dividers and card borders.
    border_strong: str  # Outline buttons.
    accent_border: str  # Selected-row bar.
    text: str  # Titles and body.
    text_secondary: str  # Senders and meta.
    text_muted: str  # Section labels and notes.
    warning_fg: str  # Deadlines.
    warning_bg: str
    accent_fg: str  # Proposal chips and focus.
    accent_bg: str


@dataclass(frozen=True, slots=True)
class Tint:
    """A translucent colour: an opaque #rrggbb and its own alpha, so there is no doubt
    whether a hex string's alpha comes first (Qt) or last (CSS)."""

    color: str
    alpha: float  # 0 is clear, 1 is opaque.


@dataclass(frozen=True, slots=True)
class GlassTokens:
    """Liquid Glass for navigation chrome only (header, sidebar, pill buttons); lists and
    the reading pane stay opaque. Glass lies over a backdrop painted from these tokens, so
    its text contrast can be proven: `text` and `text_secondary` on a panel, `text` on a
    raised pill and on pill buttons. `text_muted` never sits on glass."""

    backdrop_top: str  # The backdrop's gradient, top left to bottom right.
    backdrop_bottom: str
    streak: Tint  # Diagonal light across the backdrop; alpha is the brightest any reaches.
    tint: Tint  # A glass panel's fill over the blurred backdrop.
    min_tint_alpha: float  # The least opaque a panel's tint may ever be.
    sheen: Tint  # A panel's light at its top edge, fading downwards.
    edge_light: Tint  # A panel's lit top line.
    raised: Tint  # The selected page's pill, a fill on the glass.
    raised_sheen: Tint
    rim_top: Tint  # A pill's rim, bright at the top left, dim along the bottom.
    rim_mid: Tint
    rim_bottom: Tint
    pill: Tint  # A pill button's fill.
    pill_sheen: Tint  # Its top half's light.
    pill_shade: Tint  # Its bottom half's shade.
    primary: Tint  # The one primary pill's fill.
    primary_rim: Tint
    opaque: str  # A panel's fill when transparency is reduced or contrast increased.
    rim_contrast: str  # Every rim when contrast is increased (3:1 on `opaque`).


_TOKENS: Final[Mapping[tuple[ColorScheme, ThemeMode], Tokens]] = {
    (ColorScheme.SLATE, ThemeMode.DARK): Tokens(
        canvas="#16171b",
        panel="#17181c",
        selection="#22242a",
        hairline="#2a2c32",
        border_strong="#454852",
        accent_border="#2d5597",
        text="#eff0f2",
        text_secondary="#c3c6cd",
        text_muted="#9095a0",
        warning_fg="#d6a04a",
        warning_bg="#2e1d08",
        accent_fg="#8db4ee",
        accent_bg="#13284a",
    ),
    (ColorScheme.SLATE, ThemeMode.LIGHT): Tokens(
        canvas="#eef0f3",
        panel="#fbfbfc",
        selection="#eceef2",
        hairline="#e1e3e8",
        border_strong="#c5c8cf",
        accent_border="#8fb0e6",
        text="#1c1d20",
        text_secondary="#50535a",
        text_muted="#646872",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#1d4f91",
        accent_bg="#e3edfb",
    ),
    (ColorScheme.GRAPHITE, ThemeMode.DARK): Tokens(
        canvas="#151515",
        panel="#1a1a19",
        selection="#151515",
        hairline="#313130",
        border_strong="#484847",
        accent_border="#193567",
        text="#f0efec",
        text_secondary="#c3c2b8",
        text_muted="#898782",
        warning_fg="#d19633",
        warning_bg="#2e1b04",
        accent_fg="#7aa5e6",
        accent_bg="#0b1f40",
    ),
    (ColorScheme.GRAPHITE, ThemeMode.LIGHT): Tokens(
        canvas="#f5f4f0",
        panel="#ffffff",
        selection="#f5f4f0",
        hairline="#e3e2dd",
        border_strong="#c9c8c2",
        accent_border="#9dbbe8",
        text="#1f1e1b",
        text_secondary="#5f5e5a",
        text_muted="#6f6e69",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#1d4f91",
        accent_bg="#e3edfb",
    ),
    (ColorScheme.OCEAN, ThemeMode.DARK): Tokens(
        canvas="#131819",
        panel="#14191a",
        selection="#1e2627",
        hairline="#262e2f",
        border_strong="#3e4b4d",
        accent_border="#056272",
        text="#eef1f1",
        text_secondary="#bfc8c9",
        text_muted="#89989a",
        warning_fg="#d6a04a",
        warning_bg="#2e1d08",
        accent_fg="#64c2d7",
        accent_bg="#012f38",
    ),
    (ColorScheme.OCEAN, ThemeMode.LIGHT): Tokens(
        canvas="#edf1f1",
        panel="#fafbfc",
        selection="#eaeff0",
        hairline="#dee5e5",
        border_strong="#c1cacb",
        accent_border="#69bdd1",
        text="#1a1e1e",
        text_secondary="#4c5556",
        text_muted="#5e6b6d",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#035a6a",
        accent_bg="#ddf0f5",
    ),
    (ColorScheme.SAGE, ThemeMode.DARK): Tokens(
        canvas="#151815",
        panel="#161916",
        selection="#202621",
        hairline="#282e29",
        border_strong="#424b44",
        accent_border="#126831",
        text="#eff1ef",
        text_secondary="#c2c8c3",
        text_muted="#8e988f",
        warning_fg="#d6a04a",
        warning_bg="#2e1d08",
        accent_fg="#86c393",
        accent_bg="#073215",
    ),
    (ColorScheme.SAGE, ThemeMode.LIGHT): Tokens(
        canvas="#eef1ee",
        panel="#fbfbfb",
        selection="#ebefec",
        hairline="#e0e4e1",
        border_strong="#c4cac5",
        accent_border="#87bf92",
        text="#1b1e1c",
        text_secondary="#4f5550",
        text_muted="#626b63",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#03612a",
        accent_bg="#e2f0e5",
    ),
    (ColorScheme.PLUM, ThemeMode.DARK): Tokens(
        canvas="#18161a",
        panel="#19171b",
        selection="#262328",
        hairline="#2e2b30",
        border_strong="#4b4650",
        accent_border="#66438a",
        text="#f1eff1",
        text_secondary="#c8c4cb",
        text_muted="#98929d",
        warning_fg="#d6a04a",
        warning_bg="#2e1d08",
        accent_fg="#c0a3e3",
        accent_bg="#311f43",
    ),
    (ColorScheme.PLUM, ThemeMode.LIGHT): Tokens(
        canvas="#f1eff2",
        panel="#fbfbfc",
        selection="#efedf1",
        hairline="#e4e2e7",
        border_strong="#cac6cd",
        accent_border="#bba1db",
        text="#1e1c1f",
        text_secondary="#555158",
        text_muted="#6b666f",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#603b85",
        accent_bg="#efe9f8",
    ),
    (ColorScheme.ROSE, ThemeMode.DARK): Tokens(
        canvas="#1b1616",
        panel="#1c1717",
        selection="#2a2223",
        hairline="#322a2b",
        border_strong="#514546",
        accent_border="#8a364e",
        text="#f2efef",
        text_secondary="#cdc3c4",
        text_muted="#9f9192",
        warning_fg="#d6a04a",
        warning_bg="#2e1d08",
        accent_fg="#e699aa",
        accent_bg="#431824",
    ),
    (ColorScheme.ROSE, ThemeMode.LIGHT): Tokens(
        canvas="#f3efef",
        panel="#fcfbfb",
        selection="#f2eced",
        hairline="#e8e1e2",
        border_strong="#cfc5c6",
        accent_border="#de98a7",
        text="#201c1c",
        text_secondary="#595051",
        text_muted="#716466",
        warning_fg="#8a5a00",
        warning_bg="#fbefd9",
        accent_fg="#842d47",
        accent_bg="#fae6ea",
    ),
}

_GLASS: Final[Mapping[tuple[ColorScheme, ThemeMode], GlassTokens]] = {
    (ColorScheme.SLATE, ThemeMode.DARK): GlassTokens(
        backdrop_top="#323640",
        backdrop_bottom="#0e0f12",
        streak=Tint("#aeb4c2", 0.36),
        tint=Tint("#15171c", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#8db4ee", 0.18),
        primary_rim=Tint("#a9c7f5", 0.7),
        opaque="#15171c",
        rim_contrast="#c3c6cd",
    ),
    (ColorScheme.SLATE, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#e4e8ef",
        backdrop_bottom="#c4cad6",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#eceef3", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#1d4f91", 0.12),
        primary_rim=Tint("#1d4f91", 0.45),
        opaque="#eef0f3",
        rim_contrast="#646872",
    ),
    (ColorScheme.GRAPHITE, ThemeMode.DARK): GlassTokens(
        backdrop_top="#373632",
        backdrop_bottom="#0f0f0e",
        streak=Tint("#b5b4af", 0.36),
        tint=Tint("#181715", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#8db4ee", 0.18),
        primary_rim=Tint("#a8c7f5", 0.7),
        opaque="#181715",
        rim_contrast="#c3c2b8",
    ),
    (ColorScheme.GRAPHITE, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#e8e8e5",
        backdrop_bottom="#cbcac5",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#eeeeec", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#204e91", 0.12),
        primary_rim=Tint("#204e91", 0.45),
        opaque="#f0f0ef",
        rim_contrast="#6f6e69",
    ),
    (ColorScheme.OCEAN, ThemeMode.DARK): GlassTokens(
        backdrop_top="#2b393b",
        backdrop_bottom="#0c1010",
        streak=Tint("#a5b8ba", 0.36),
        tint=Tint("#121919", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#64c2d7", 0.18),
        primary_rim=Tint("#8dd2e3", 0.7),
        opaque="#121919",
        rim_contrast="#bfc8c9",
    ),
    (ColorScheme.OCEAN, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#e0eaeb",
        backdrop_bottom="#bdcdcf",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#e9f0f0", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#035a6a", 0.12),
        primary_rim=Tint("#035a6a", 0.45),
        opaque="#edf1f1",
        rim_contrast="#5e6b6d",
    ),
    (ColorScheme.SAGE, ThemeMode.DARK): GlassTokens(
        backdrop_top="#303931",
        backdrop_bottom="#0d100e",
        streak=Tint("#abb8ad", 0.36),
        tint=Tint("#141815", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#86c393", 0.18),
        primary_rim=Tint("#a4d3ad", 0.7),
        opaque="#141815",
        rim_contrast="#c2c8c3",
    ),
    (ColorScheme.SAGE, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#e3eae4",
        backdrop_bottom="#c2cdc4",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#ebefec", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#03612a", 0.12),
        primary_rim=Tint("#03612a", 0.45),
        opaque="#eef1ee",
        rim_contrast="#626b63",
    ),
    (ColorScheme.PLUM, ThemeMode.DARK): GlassTokens(
        backdrop_top="#39343d",
        backdrop_bottom="#100e11",
        streak=Tint("#b8b1be", 0.36),
        tint=Tint("#19161b", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#c0a3e3", 0.18),
        primary_rim=Tint("#d0baec", 0.7),
        opaque="#19161b",
        rim_contrast="#c8c4cb",
    ),
    (ColorScheme.PLUM, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#eae6ed",
        backdrop_bottom="#cdc7d3",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#efedf2", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#603b85", 0.12),
        primary_rim=Tint("#603b85", 0.45),
        opaque="#f1eff2",
        rim_contrast="#6b666f",
    ),
    (ColorScheme.ROSE, ThemeMode.DARK): GlassTokens(
        backdrop_top="#3f3234",
        backdrop_bottom="#120e0e",
        streak=Tint("#c1afb1", 0.36),
        tint=Tint("#1c1516", 0.6),
        min_tint_alpha=0.60,
        sheen=Tint("#ffffff", 0.08),
        edge_light=Tint("#ffffff", 0.09),
        raised=Tint("#ffffff", 0.085),
        raised_sheen=Tint("#ffffff", 0.07),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        primary=Tint("#e699aa", 0.18),
        primary_rim=Tint("#efb2bf", 0.7),
        opaque="#1c1516",
        rim_contrast="#cdc3c4",
    ),
    (ColorScheme.ROSE, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#efe5e6",
        backdrop_bottom="#d5c5c7",
        streak=Tint("#ffffff", 0.55),
        tint=Tint("#ffffff", 0.52),
        min_tint_alpha=0.52,
        sheen=Tint("#ffffff", 0.4),
        edge_light=Tint("#ffffff", 0.85),
        raised=Tint("#ffffff", 0.78),
        raised_sheen=Tint("#ffffff", 0.3),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#f3eced", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        primary=Tint("#842d47", 0.12),
        primary_rim=Tint("#842d47", 0.45),
        opaque="#f3efef",
        rim_contrast="#716466",
    ),
}

DARK: Final = _TOKENS[ColorScheme.SLATE, ThemeMode.DARK]
LIGHT: Final = _TOKENS[ColorScheme.SLATE, ThemeMode.LIGHT]
GLASS_DARK: Final = _GLASS[ColorScheme.SLATE, ThemeMode.DARK]
GLASS_LIGHT: Final = _GLASS[ColorScheme.SLATE, ThemeMode.LIGHT]

# Metrics, in logical pixels.
RADIUS: Final = 8
SIDEBAR_WIDTH: Final = 128
TEXT_PX: Final = 13
SMALL_PX: Final = 12
CAPTION_PX: Final = 11
TITLE_PX: Final = 15
# A list row's padding and its left selection mark, which the stylesheet draws; a wrapping
# row needs them to wrap at the list's width.
ITEM_PAD_X: Final = 8
ITEM_PAD_Y: Final = 4
ITEM_MARK_PX: Final = 2


def tokens_for(mode: ThemeMode, scheme: ColorScheme = ColorScheme.SLATE) -> Tokens:
    return _TOKENS[scheme, mode]


def glass_for(mode: ThemeMode, scheme: ColorScheme = ColorScheme.SLATE) -> GlassTokens:
    return _GLASS[scheme, mode]
