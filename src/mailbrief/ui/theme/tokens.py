"""Design tokens: the only place MailBrief's UI colours are defined.

DARK is measured from the approved mockup (docs/ui/mockup-three-pane-dark.png); LIGHT is
provisional until a light mockup is approved.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ThemeMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


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


DARK: Final = Tokens(
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
)

LIGHT: Final = Tokens(
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
)


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


GLASS_DARK: Final = GlassTokens(
    backdrop_top="#323640",
    backdrop_bottom="#0e0f12",
    streak=Tint("#aeb4c2", 0.36),
    tint=Tint("#15171c", 0.60),
    min_tint_alpha=0.60,
    sheen=Tint("#ffffff", 0.08),
    edge_light=Tint("#ffffff", 0.09),
    raised=Tint("#ffffff", 0.085),
    raised_sheen=Tint("#ffffff", 0.07),
    rim_top=Tint("#ffffff", 0.42),
    rim_mid=Tint("#ffffff", 0.06),
    rim_bottom=Tint("#ffffff", 0.16),
    pill=Tint("#ffffff", 0.055),
    pill_sheen=Tint("#ffffff", 0.10),
    pill_shade=Tint("#000000", 0.22),
    primary=Tint("#8db4ee", 0.18),
    primary_rim=Tint("#a9c7f5", 0.70),
    opaque="#15171c",
    rim_contrast="#c3c2b8",
)

GLASS_LIGHT: Final = GlassTokens(
    backdrop_top="#e4e8ef",
    backdrop_bottom="#c4cad6",
    streak=Tint("#ffffff", 0.55),
    tint=Tint("#ffffff", 0.52),
    min_tint_alpha=0.52,
    sheen=Tint("#ffffff", 0.40),
    edge_light=Tint("#ffffff", 0.85),
    raised=Tint("#ffffff", 0.78),
    raised_sheen=Tint("#ffffff", 0.30),
    rim_top=Tint("#ffffff", 1.0),
    rim_mid=Tint("#000000", 0.10),
    rim_bottom=Tint("#000000", 0.26),
    pill=Tint("#eceef3", 0.90),
    pill_sheen=Tint("#ffffff", 0.75),
    pill_shade=Tint("#000000", 0.07),
    primary=Tint("#1d4f91", 0.12),
    primary_rim=Tint("#1d4f91", 0.45),
    opaque="#eef0f3",
    rim_contrast="#6f6e69",
)

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


def tokens_for(mode: ThemeMode) -> Tokens:
    return DARK if mode is ThemeMode.DARK else LIGHT


def glass_for(mode: ThemeMode) -> GlassTokens:
    return GLASS_DARK if mode is ThemeMode.DARK else GLASS_LIGHT
