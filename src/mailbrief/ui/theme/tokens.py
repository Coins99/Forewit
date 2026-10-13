"""Design tokens: the only place MailBrief's UI colours are defined.

Each colour scheme has a dark and a light set, and MailBrief follows the system's light or
dark mode unless the owner picks one. Studio (white and Studio Mist grey, one blue, after
Apple's product pages; docs/ui/studio.md) is the default, and DARK and LIGHT are its sets.
Slate is grey-blue, after the Liquid Glass prototype. Graphite is the warm grey measured
from the first mockup (docs/ui/mockup-three-pane-dark.png). Ocean, Sage, Plum and Rose keep
Slate's lightness and change its hue.
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

    STUDIO = "studio"  # White and Studio Mist with one blue, the default.
    SLATE = "slate"  # Grey-blue.
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
    border_strong: str  # Check box rims, strong cards, scroll bars, the palette field.
    accent_border: str  # Selected-row bar.
    text: str  # Titles and body.
    text_secondary: str  # Senders and meta.
    text_muted: str  # Section labels and notes.
    warning_fg: str  # Deadlines.
    warning_bg: str
    accent_fg: str  # Links, proposal labels and focus.
    accent_bg: str  # Ticked check boxes.
    card: str  # Cards in a pane: a fill, with no border.
    primary_fill: str  # The one primary button of a screen.
    on_primary: str  # Its text.


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
    raised pill and on pill buttons, `primary_text` on the primary pill. `text_muted` never
    sits on glass. A calm scheme (Studio) has a flat backdrop and turns its lights off with
    an alpha of 0."""

    backdrop_top: str  # The backdrop's gradient, top left to bottom right.
    backdrop_bottom: str
    streak: Tint  # Diagonal light across the backdrop; alpha is the brightest any reaches.
    tint: Tint  # A glass panel's fill over the blurred backdrop.
    min_tint_alpha: float  # The least opaque a panel's tint may ever be.
    sheen: Tint  # A panel's light at its top edge, fading downwards.
    edge_light: Tint  # A panel's lit top line.
    raised: Tint  # The selected page's pill, a fill on the glass.
    raised_sheen: Tint
    raised_rim: Tint  # The selected pill's rim at its top; alpha 0 draws no rim.
    rim_top: Tint  # A pill's rim, bright at the top left, dim along the bottom.
    rim_mid: Tint
    rim_bottom: Tint
    pill: Tint  # A pill button's fill.
    pill_sheen: Tint  # Its top half's light.
    pill_shade: Tint  # Its bottom half's shade.
    pill_hover: Tint  # A pill button's fill under the pointer, faded in with the hover.
    primary: Tint  # The one primary pill's fill.
    primary_rim: Tint
    primary_text: str  # The primary pill's label.
    specular: Tint  # The light a pointer casts on a pill's rim; never on its body.
    opaque: str  # A panel's fill when transparency is reduced or contrast increased.
    # The colour field behind the glass: three soft pools of light over the backdrop, as
    # colourful content spills onto Liquid Glass. Alpha 0 paints no pool.
    pool_a: Tint
    pool_b: Tint
    pool_c: Tint
    # Liquid Glass's layers on a pill (WWDC25), lit as a Liquid Glass tab is: the light on its
    # rim, along the top and the bottom; the glow it lenses into the body from both edges;
    # and the soft shadow it casts. Alpha 0 turns a layer off.
    highlight: Tint
    lens: Tint
    shadow: Tint
    # A pill's edges, as on a Liquid Glass tab: an even contact ring just outside it (for a
    # light page, where its light can't show), and the shade at its ends, where the light
    # that runs along its long edges never reaches.
    edge: Tint
    end_shade: Tint
    rim_contrast: str  # Every rim when contrast is increased (3:1 on `opaque`).


_TOKENS: Final[Mapping[tuple[ColorScheme, ThemeMode], Tokens]] = {
    (ColorScheme.STUDIO, ThemeMode.DARK): Tokens(
        canvas="#161617",
        panel="#1d1d1f",
        selection="#2c2c2e",
        hairline="#3a3a3c",
        border_strong="#6e6e73",
        accent_border="#0a84ff",
        text="#f5f5f7",
        text_secondary="#a1a1a6",
        text_muted="#98989d",
        warning_fg="#ff9f0a",
        warning_bg="#3a2405",
        accent_fg="#2997ff",
        accent_bg="#0f2a47",
        card="#2a2a2c",
        primary_fill="#0071e3",
        on_primary="#ffffff",
    ),
    (ColorScheme.STUDIO, ThemeMode.LIGHT): Tokens(
        canvas="#f5f5f7",
        panel="#ffffff",
        selection="#ebebef",
        hairline="#d6d6d6",
        border_strong="#86868b",
        accent_border="#0071e3",
        text="#1d1d1f",
        text_secondary="#515154",
        text_muted="#68686d",
        warning_fg="#b64400",
        warning_bg="#fff1e6",
        accent_fg="#0066cc",
        accent_bg="#e8f1fb",
        card="#f5f5f7",
        primary_fill="#0071e3",
        on_primary="#ffffff",
    ),
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
        card="#22242a",
        primary_fill="#13284a",
        on_primary="#8db4ee",
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
        card="#eceef2",
        primary_fill="#e3edfb",
        on_primary="#1d4f91",
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
        card="#151515",
        primary_fill="#0b1f40",
        on_primary="#7aa5e6",
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
        card="#f5f4f0",
        primary_fill="#e3edfb",
        on_primary="#1d4f91",
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
        card="#1e2627",
        primary_fill="#012f38",
        on_primary="#64c2d7",
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
        card="#eaeff0",
        primary_fill="#ddf0f5",
        on_primary="#035a6a",
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
        card="#202621",
        primary_fill="#073215",
        on_primary="#86c393",
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
        card="#ebefec",
        primary_fill="#e2f0e5",
        on_primary="#03612a",
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
        card="#262328",
        primary_fill="#311f43",
        on_primary="#c0a3e3",
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
        card="#efedf1",
        primary_fill="#efe9f8",
        on_primary="#603b85",
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
        card="#2a2223",
        primary_fill="#431824",
        on_primary="#e699aa",
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
        card="#f2eced",
        primary_fill="#fae6ea",
        on_primary="#842d47",
    ),
}

_GLASS: Final[Mapping[tuple[ColorScheme, ThemeMode], GlassTokens]] = {
    # Liquid Glass after WWDC25, over a fluid colour field after monopo saigon: three soft
    # pools of sage, amber and rose light behind frosted chrome, with no streak, sheen or
    # lit edge. Every pill, wherever it sits, is frosted at rest with a specular rim, a
    # lensing band and a soft shadow, as the sidebar's Disconnect is. The selected page is
    # a vibrant white fill with no rim (no glass on glass); the primary pill is solid blue
    # with white text.
    (ColorScheme.STUDIO, ThemeMode.DARK): GlassTokens(
        backdrop_top="#161617",
        backdrop_bottom="#161617",
        streak=Tint("#ffffff", 0.0),
        tint=Tint("#2a2a2c", 0.6),
        min_tint_alpha=0.6,
        sheen=Tint("#ffffff", 0.0),
        edge_light=Tint("#ffffff", 0.0),
        raised=Tint("#ffffff", 0.14),
        raised_sheen=Tint("#ffffff", 0.0),
        raised_rim=Tint("#ffffff", 0.0),
        rim_top=Tint("#ffffff", 0.28),
        rim_mid=Tint("#ffffff", 0.28),
        rim_bottom=Tint("#ffffff", 0.28),
        pill=Tint("#ffffff", 0.03),
        pill_sheen=Tint("#ffffff", 0.0),
        pill_shade=Tint("#000000", 0.0),
        pill_hover=Tint("#ffffff", 0.07),
        primary=Tint("#0071e3", 1.0),
        primary_rim=Tint("#0071e3", 1.0),
        primary_text="#ffffff",
        specular=Tint("#ffffff", 0.5),
        opaque="#242426",
        rim_contrast="#a1a1a6",
        pool_a=Tint("#2f5a3c", 0.7),
        pool_b=Tint("#6b4614", 0.6),
        pool_c=Tint("#5a1f22", 0.65),
        highlight=Tint("#ffffff", 0.24),
        lens=Tint("#ffffff", 0.18),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.45),
    ),
    (ColorScheme.STUDIO, ThemeMode.LIGHT): GlassTokens(
        backdrop_top="#f5f5f7",
        backdrop_bottom="#f5f5f7",
        streak=Tint("#ffffff", 0.0),
        tint=Tint("#f5f5f7", 0.55),
        min_tint_alpha=0.55,
        sheen=Tint("#ffffff", 0.0),
        edge_light=Tint("#ffffff", 0.0),
        raised=Tint("#ffffff", 0.72),
        raised_sheen=Tint("#ffffff", 0.0),
        raised_rim=Tint("#000000", 0.0),
        rim_top=Tint("#86868b", 1.0),
        rim_mid=Tint("#86868b", 1.0),
        rim_bottom=Tint("#86868b", 1.0),
        pill=Tint("#ffffff", 0.5),
        pill_sheen=Tint("#ffffff", 0.0),
        pill_shade=Tint("#000000", 0.0),
        pill_hover=Tint("#ffffff", 0.92),
        primary=Tint("#0071e3", 1.0),
        primary_rim=Tint("#0071e3", 1.0),
        primary_text="#ffffff",
        specular=Tint("#ffffff", 0.8),
        opaque="#f5f5f7",
        rim_contrast="#6e6e73",
        pool_a=Tint("#a0e0ab", 0.55),
        pool_b=Tint("#ffc46b", 0.5),
        pool_c=Tint("#eaa3a8", 0.45),
        highlight=Tint("#ffffff", 0.95),
        lens=Tint("#ffffff", 0.6),
        shadow=Tint("#000000", 0.07),
        edge=Tint("#000000", 0.065),
        end_shade=Tint("#000000", 0.14),
    ),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#8db4ee", 0.18),
        primary_rim=Tint("#a9c7f5", 0.7),
        primary_text="#eff0f2",
        specular=Tint("#ffffff", 0.9),
        opaque="#15171c",
        rim_contrast="#c3c6cd",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#eceef3", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#eceef3", 0.0),
        primary=Tint("#1d4f91", 0.12),
        primary_rim=Tint("#1d4f91", 0.45),
        primary_text="#1c1d20",
        specular=Tint("#1d4f91", 0.6),
        opaque="#eef0f3",
        rim_contrast="#646872",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#8db4ee", 0.18),
        primary_rim=Tint("#a8c7f5", 0.7),
        primary_text="#f0efec",
        specular=Tint("#ffffff", 0.9),
        opaque="#181715",
        rim_contrast="#c3c2b8",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#eeeeec", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#eeeeec", 0.0),
        primary=Tint("#204e91", 0.12),
        primary_rim=Tint("#204e91", 0.45),
        primary_text="#1f1e1b",
        specular=Tint("#204e91", 0.6),
        opaque="#f0f0ef",
        rim_contrast="#6f6e69",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#64c2d7", 0.18),
        primary_rim=Tint("#8dd2e3", 0.7),
        primary_text="#eef1f1",
        specular=Tint("#ffffff", 0.9),
        opaque="#121919",
        rim_contrast="#bfc8c9",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#e9f0f0", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#e9f0f0", 0.0),
        primary=Tint("#035a6a", 0.12),
        primary_rim=Tint("#035a6a", 0.45),
        primary_text="#1a1e1e",
        specular=Tint("#035a6a", 0.6),
        opaque="#edf1f1",
        rim_contrast="#5e6b6d",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#86c393", 0.18),
        primary_rim=Tint("#a4d3ad", 0.7),
        primary_text="#eff1ef",
        specular=Tint("#ffffff", 0.9),
        opaque="#141815",
        rim_contrast="#c2c8c3",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#ebefec", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#ebefec", 0.0),
        primary=Tint("#03612a", 0.12),
        primary_rim=Tint("#03612a", 0.45),
        primary_text="#1b1e1c",
        specular=Tint("#03612a", 0.6),
        opaque="#eef1ee",
        rim_contrast="#626b63",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#c0a3e3", 0.18),
        primary_rim=Tint("#d0baec", 0.7),
        primary_text="#f1eff1",
        specular=Tint("#ffffff", 0.9),
        opaque="#19161b",
        rim_contrast="#c8c4cb",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#efedf2", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#efedf2", 0.0),
        primary=Tint("#603b85", 0.12),
        primary_rim=Tint("#603b85", 0.45),
        primary_text="#1e1c1f",
        specular=Tint("#603b85", 0.6),
        opaque="#f1eff2",
        rim_contrast="#6b666f",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
        pill=Tint("#ffffff", 0.055),
        pill_sheen=Tint("#ffffff", 0.1),
        pill_shade=Tint("#000000", 0.22),
        pill_hover=Tint("#ffffff", 0.0),
        primary=Tint("#e699aa", 0.18),
        primary_rim=Tint("#efb2bf", 0.7),
        primary_text="#f2efef",
        specular=Tint("#ffffff", 0.9),
        opaque="#1c1516",
        rim_contrast="#cdc3c4",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        pill=Tint("#f3eced", 0.9),
        pill_sheen=Tint("#ffffff", 0.75),
        pill_shade=Tint("#000000", 0.07),
        pill_hover=Tint("#f3eced", 0.0),
        primary=Tint("#842d47", 0.12),
        primary_rim=Tint("#842d47", 0.45),
        primary_text="#201c1c",
        specular=Tint("#842d47", 0.6),
        opaque="#f3efef",
        rim_contrast="#716466",
        pool_a=Tint("#ffffff", 0.0),
        pool_b=Tint("#ffffff", 0.0),
        pool_c=Tint("#ffffff", 0.0),
        highlight=Tint("#ffffff", 0.0),
        lens=Tint("#ffffff", 0.0),
        shadow=Tint("#000000", 0.0),
        edge=Tint("#000000", 0.0),
        end_shade=Tint("#000000", 0.0),
    ),
}

DEFAULT_SCHEME: Final = ColorScheme.STUDIO
DARK: Final = _TOKENS[DEFAULT_SCHEME, ThemeMode.DARK]
LIGHT: Final = _TOKENS[DEFAULT_SCHEME, ThemeMode.LIGHT]
GLASS_DARK: Final = _GLASS[DEFAULT_SCHEME, ThemeMode.DARK]
GLASS_LIGHT: Final = _GLASS[DEFAULT_SCHEME, ThemeMode.LIGHT]

# Metrics, in logical pixels.
RADIUS: Final = 10  # Rows, list selections and small controls.
CARD_RADIUS: Final = 18  # Cards in a pane.
SHEET_RADIUS: Final = 12  # The rounded sheet the pages sit on.
# A dialog in the home screen's style: its glass round a rounded sheet, DIALOG_GUTTER from
# the window's edge, and its content DIALOG_MARGIN in (inside the sheet, clear of its
# rounded corners).
DIALOG_GUTTER: Final = 8
DIALOG_MARGIN: Final = 22
# The command palette's search field is a pill: half its height. Buttons are glass pills,
# drawn by GlassButton and ThemeStyle.
PILL_RADIUS: Final = 14
TAB_RADIUS: Final = 12  # Tabs are segments of a pill.
FIELD_RADIUS: Final = 8  # Text fields, combo boxes and date and time fields.
MENU_ITEM_RADIUS: Final = 6  # Menu items and tooltips.
SIDEBAR_WIDTH: Final = 128
TEXT_PX: Final = 13
READING_PX: Final = 14  # An email's summary in the reading pane.
SMALL_PX: Final = 12
CAPTION_PX: Final = 11
HEADING_PX: Final = 17  # In-pane headings, Semibold: the brief's day.
TITLE_PX: Final = 20  # Page and email titles, Semibold.
# A list row's padding and its left selection mark, which the stylesheet draws; a wrapping
# row needs them to wrap at the list's width. Selections are rounded fills, with no mark.
ITEM_PAD_X: Final = 10
ITEM_PAD_Y: Final = 5
ITEM_MARK_PX: Final = 0


def tokens_for(mode: ThemeMode, scheme: ColorScheme = DEFAULT_SCHEME) -> Tokens:
    return _TOKENS[scheme, mode]


def glass_for(mode: ThemeMode, scheme: ColorScheme = DEFAULT_SCHEME) -> GlassTokens:
    return _GLASS[scheme, mode]
