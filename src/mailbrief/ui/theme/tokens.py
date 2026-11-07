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

    @property
    def sidebar(self) -> str:
        return self.canvas

    @property
    def nav_selection(self) -> str:
        return "#e3e8e6" if self is _TOKENS[ColorScheme.STUDIO, ThemeMode.LIGHT] else self.selection

    @property
    def primary(self) -> str:
        """The accent as text and icons (a selected page, an unread dot, a state line). Not
        the primary button's fill: in most schemes that is a pale or deep tint made to sit
        under ``on_primary``, which as text all but disappears into the panel."""
        return self.accent_fg

    @property
    def primary_text(self) -> str:
        return self.on_primary

    @property
    def summary(self) -> str:
        return "#f7f9fc" if self is _TOKENS[ColorScheme.STUDIO, ThemeMode.LIGHT] else self.card

    @property
    def surface_alt(self) -> str:
        return "#fbfcfb" if self is _TOKENS[ColorScheme.STUDIO, ThemeMode.LIGHT] else self.canvas

    @property
    def success_fg(self) -> str:
        return (
            "#2e7659"
            if self is _TOKENS[ColorScheme.STUDIO, ThemeMode.LIGHT]
            else self.text_secondary
        )

    @property
    def overlay(self) -> str:
        return "#731a211d"

    @property
    def off_hours(self) -> "Tint":
        """The Calendar's wash over the hours outside the working hours (ADR 0029): muted
        text, faint, so it shows on the panel and on today's card alike and every entry
        drawn over it keeps its own proven fill."""
        return Tint(self.text_muted, OFF_HOURS_ALPHA)

    # A flat button (ThemeStyle): `panel` inside a `hairline` edge at rest, `card` under the
    # pointer, and these, derived so every scheme has them and the contrast tests cover them.
    @property
    def button_pressed(self) -> str:
        """A pressed button's fill: the hover fill a little toward the text."""
        return _mix(self.card, self.text, 0.08)

    @property
    def primary_hover(self) -> str:
        """The primary button under the pointer: its fill moved away from its label, so the
        label's contrast only rises."""
        return _away(self.primary_fill, self.on_primary, 0.08)

    @property
    def primary_pressed(self) -> str:
        """The primary button while pressed, further from its label than when hovered."""
        return _away(self.primary_fill, self.on_primary, 0.16)


OFF_HOURS_ALPHA: Final = 0.08


def _channels(hex_colour: str) -> tuple[int, int, int]:
    value = int(hex_colour[1:7], 16)
    return value >> 16, (value >> 8) & 0xFF, value & 0xFF


def _mix(colour: str, toward: str, amount: float) -> str:
    """``colour`` moved ``amount`` (0 to 1) of the way to ``toward``, as #rrggbb."""
    mixed = (
        round(a + (b - a) * amount)
        for a, b in zip(_channels(colour), _channels(toward), strict=True)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _luminance(hex_colour: str) -> float:
    """WCAG's relative luminance."""
    linear = []
    for channel in _channels(hex_colour):
        c = channel / 255
        linear.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _away(fill: str, label: str, amount: float) -> str:
    """``fill`` moved ``amount`` toward black under a lighter label, or toward white under a
    darker one, so the label stands out more, never less."""
    darker = _luminance(label) > _luminance(fill)
    return _mix(fill, "#000000" if darker else "#ffffff", amount)


@dataclass(frozen=True, slots=True)
class Tint:
    """A translucent colour: an opaque #rrggbb and its own alpha, so there is no doubt
    whether a hex string's alpha comes first (Qt) or last (CSS)."""

    color: str
    alpha: float  # 0 is clear, 1 is opaque.


# A layer turned off: alpha 0 paints nothing. Only Studio lights the colour field; every
# other scheme leaves it at this default.
_LIGHT_OFF: Final = Tint("#ffffff", 0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class GlassTokens:
    """Liquid Glass for navigation chrome only (header, sidebar and a dialog's frame);
    buttons, lists and the reading pane are flat. Glass lies over a backdrop painted from
    these tokens, so its text contrast can be proven: `text` and `text_secondary` on a
    panel and `text` on a raised pill. `text_muted` never sits on glass. A calm scheme
    (Studio) has a flat backdrop and turns its lights off with an alpha of 0."""

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
    opaque: str  # A panel's fill when transparency is reduced or contrast increased.
    # The colour field behind the glass: three soft pools of light over the backdrop, as
    # colourful content spills onto Liquid Glass. Alpha 0 paints no pool.
    pool_a: Tint = _LIGHT_OFF
    pool_b: Tint = _LIGHT_OFF
    pool_c: Tint = _LIGHT_OFF
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
        canvas="#f5f7f6",
        panel="#ffffff",
        selection="#edf3ff",
        hairline="#dfe3df",
        border_strong="#86868b",
        accent_border="#2f62d4",
        text="#202421",
        text_secondary="#59615c",
        text_muted="#69716c",
        warning_fg="#b64b39",
        warning_bg="#fff1ed",
        accent_fg="#2f62d4",
        accent_bg="#edf3ff",
        card="#f5f7f6",
        primary_fill="#2f62d4",
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
    # lit edge. The selected page is a vibrant white fill with no rim (no glass on glass).
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
        opaque="#242426",
        rim_contrast="#a1a1a6",
        pool_a=Tint("#2f5a3c", 0.7),
        pool_b=Tint("#6b4614", 0.6),
        pool_c=Tint("#5a1f22", 0.65),
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
        opaque="#f5f5f7",
        rim_contrast="#6e6e73",
        pool_a=Tint("#a0e0ab", 0.55),
        pool_b=Tint("#ffc46b", 0.5),
        pool_c=Tint("#eaa3a8", 0.45),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
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
        raised_rim=Tint("#ffffff", 0.42),
        rim_top=Tint("#ffffff", 0.42),
        rim_mid=Tint("#ffffff", 0.06),
        rim_bottom=Tint("#ffffff", 0.16),
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
        raised_rim=Tint("#ffffff", 1.0),
        rim_top=Tint("#ffffff", 1.0),
        rim_mid=Tint("#000000", 0.1),
        rim_bottom=Tint("#000000", 0.26),
        opaque="#f3efef",
        rim_contrast="#716466",
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
# The command palette's search field is a pill: half its height. Buttons are flat rounded
# rectangles with FIELD_RADIUS corners, drawn by ThemeStyle.
PILL_RADIUS: Final = 14
TAB_RADIUS: Final = 12  # Tabs are segments of a pill.
FIELD_RADIUS: Final = 8  # Text fields, combo boxes, date and time fields and buttons.
MENU_ITEM_RADIUS: Final = 6  # Menu items and tooltips.
SIDEBAR_WIDTH: Final = 214
BODY_PX: Final = 11
META_PX: Final = 9
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
