"""Bundled DM Sans and Inter fallback (SIL OFL 1.1) and Tabler icons (MIT), resolved here so they
also load from the PyInstaller package."""

import functools
import math
from pathlib import Path
from typing import Final

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

FONT_FAMILY: Final = "DM Sans"
ICON_NAMES: Final = frozenset(
    {
        "sun",
        "checkbox",
        "hourglass",
        "pencil",
        "calendar",
        "calendar-week",
        "settings",
        "refresh",
        "clock",
        "external-link",
        "mail",
        "database",
        "sparkles",
        "plus",
        "bookmark",
        "check",
        "trash",
        "search",
        "x",
        "chevron-left",
        "chevron-right",
        "paperclip",
        "star",
        "flag",
        "alert-circle",
    }
)

_HERE: Final = Path(__file__).resolve().parent
_FONTS: Final = tuple(
    _HERE / "fonts" / f"Inter-{weight}.ttf" for weight in ("Regular", "Medium", "SemiBold")
)
# Display sizes are tracked tighter, as Inter's dynamic metrics advise (tracking = A + B *
# e^(C * size), in ems) and Apple sets its display type; text sizes keep Inter's spacing.
_TRACK_FROM_PX: Final = 15
_TRACK_A: Final = -0.0223
_TRACK_B: Final = 0.185
_TRACK_C: Final = -0.1745
_fonts_loaded: bool | None = None  # None until the first attempt; then cached.


def register_fonts() -> bool:
    """Load the bundled fonts once per process; True only when all fonts loaded.

    A missing or unreadable font never stops the app: the theme then keeps Qt's font.
    """
    global _fonts_loaded
    if _fonts_loaded is None:
        _fonts_loaded = all(
            QFontDatabase.addApplicationFont(str(path)) != -1
            for path in (*_FONTS, _HERE / "fonts" / "DM-Sans.ttf")
        )
        release_font_cache()
    return _fonts_loaded


def tracking(px: int) -> float:
    """The letter spacing for Inter at ``px``, in pixels: none at text sizes, tighter as
    display sizes grow (-0.1 at 15 px, -0.33 at 20 px)."""
    if px < _TRACK_FROM_PX:
        return 0.0
    return round(px * (_TRACK_A + _TRACK_B * math.exp(_TRACK_C * px)), 2)


def _weight(medium: bool, semibold: bool) -> QFont.Weight:
    if medium and semibold:
        raise ValueError("A font is medium or semibold, not both.")
    if semibold:
        return QFont.Weight.DemiBold
    return QFont.Weight.Medium if medium else QFont.Weight.Normal


# Inter's tabular figures: every digit as wide as the others, so times and counts line up.
_TABULAR: Final = QFont.Tag("tnum")


@functools.cache
def _font(px: int, weight: QFont.Weight, tabular: bool = False) -> QFont:
    font = QFont("Inter" if tabular else FONT_FAMILY)
    font.setPixelSize(px)
    font.setWeight(weight)
    spacing = tracking(px)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    if tabular:
        font.setFeature(_TABULAR, 1)
    return font


def ui_font(
    px: int, *, medium: bool = False, semibold: bool = False, tabular: bool = False
) -> QFont:
    """Inter at a pixel size; point sizes would scale differently on each platform. Medium
    is for emphasis in text, SemiBold for titles, and ``tabular`` gives equal-width digits
    for times and counts in columns. A copy, so a caller that changes it never changes the
    cached font."""
    return QFont(_font(px, _weight(medium, semibold), tabular))


@functools.cache
def _metrics(px: int, weight: QFont.Weight, tabular: bool = False) -> QFontMetrics:
    return QFontMetrics(_font(px, weight, tabular))


def ui_metrics(
    px: int, *, medium: bool = False, semibold: bool = False, tabular: bool = False
) -> QFontMetrics:
    """The metrics of ``ui_font(px, ...)``, built once per size, weight and figure style."""
    return _metrics(px, _weight(medium, semibold), tabular)


def release_font_cache() -> None:
    """Drop the cached fonts and metrics: when the bundled fonts are registered, so nothing
    measured with a fallback font is reused, and when the app is about to quit."""
    _font.cache_clear()
    _metrics.cache_clear()


@functools.cache
def icon_pixmap(name: str, color: str, px: int, dpr: float = 1.0) -> QPixmap:
    """Render a bundled icon in one colour. Needs a QApplication."""
    if name not in ICON_NAMES:
        raise ValueError("Unknown icon.")
    svg = (_HERE / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    renderer = QSvgRenderer(
        QByteArray(svg.replace('stroke="currentColor"', f'stroke="{color}"').encode())
    )
    size = round(px * dpr)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def release_icon_cache() -> None:
    """Drop the cached icon pixmaps; ``apply_theme`` runs this when the app is about to quit,
    while Qt can still free them."""
    icon_pixmap.cache_clear()


def icon(name: str, color: str, px: int = 16) -> QIcon:
    result = QIcon()
    for dpr in (1.0, 2.0):
        result.addPixmap(icon_pixmap(name, color, px, dpr))
    return result
