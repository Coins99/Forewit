"""Design tokens, palette, stylesheet and bundled assets."""

import dataclasses
import gc
import re
from collections.abc import Callable

import pytest
from PySide6.QtCore import QMetaMethod
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QFontMetrics, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QWidget

from mailbrief.ui.theme import (
    DARK,
    ICON_NAMES,
    LIGHT,
    ColorScheme,
    ThemeMode,
    Tokens,
    apply_theme,
    assets,
    build_stylesheet,
    current_mode,
    current_scheme,
    current_tokens,
    follow_theme,
    icon_pixmap,
    register_fonts,
    release_font_cache,
    tokens_for,
    tracking,
    ui_font,
    ui_metrics,
)
from mailbrief.ui.theme import style as theme_style
from mailbrief.ui.theme.style import ThemeStyle

# Every colour scheme in both modes.
SETS = [(scheme, mode) for scheme in ColorScheme for mode in ThemeMode]


def luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a #rrggbb colour."""
    channels = []
    for index in (1, 3, 5):
        value = int(color[index : index + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_formula_matches_wcag_reference_values() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_every_token_is_a_lowercase_hex_colour(scheme: ColorScheme, mode: ThemeMode) -> None:
    for value in dataclasses.asdict(tokens_for(mode, scheme)).values():
        assert re.fullmatch(r"#[0-9a-f]{6}", value)


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_text_meets_wcag_aa_contrast(scheme: ColorScheme, mode: ThemeMode) -> None:
    t = tokens_for(mode, scheme)
    pairs = [
        (foreground, background)
        for foreground in (t.text, t.text_secondary, t.text_muted)
        for background in (t.panel, t.selection, t.card)
    ]
    # Warning and accent text are also bare labels, on rows and cards.
    pairs += [
        (foreground, background)
        for foreground in (t.warning_fg, t.accent_fg)
        for background in (t.panel, t.selection, t.card)
    ]
    pairs += [(t.warning_fg, t.warning_bg), (t.accent_fg, t.accent_bg)]
    pairs += [(t.on_primary, t.primary_fill)]
    for foreground, background in pairs:
        assert contrast(foreground, background) >= 4.5, (scheme, mode, foreground, background)


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_stylesheet_substitutes_every_token(mode: ThemeMode) -> None:
    stylesheet = build_stylesheet(tokens_for(mode))
    assert "$" not in stylesheet
    assert tokens_for(mode).panel in stylesheet


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_apply_theme_sets_style_palette_and_tokens(
    qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    tokens = apply_theme(qapp, mode)
    assert isinstance(tokens, Tokens)
    assert qapp.styleSheet() == build_stylesheet(tokens)
    palette = qapp.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == tokens.panel
    assert palette.color(QPalette.ColorRole.Accent).name() == tokens.accent_fg
    assert palette.color(QPalette.ColorRole.Midlight).name() == tokens.selection
    assert palette.color(QPalette.ColorRole.ToolTipBase).name() == tokens.canvas
    assert current_tokens() == tokens_for(mode)
    qapp.setStyleSheet("")  # The stylesheet's proxy hides the base style's name.
    style = qapp.style()
    assert isinstance(style, ThemeStyle) and style.baseStyle().name() == "fusion"


def test_themed_fixture_restores_dark_tokens() -> None:
    assert current_tokens() == DARK


def test_medium_font_resolves_to_bundled_weight(qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    font = ui_font(13, medium=True)
    assert QFontInfo(font).weight() == 500
    assert QFontInfo(font).family() == "Inter"
    assert font.pixelSize() == 13


def test_semibold_font_resolves_to_bundled_weight(qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    font = ui_font(20, semibold=True)
    assert QFontInfo(font).weight() == 600
    assert QFontInfo(font).family() == "Inter"
    with pytest.raises(ValueError):
        ui_font(13, medium=True, semibold=True)
    assert ui_metrics(20, semibold=True) is not ui_metrics(20, medium=True)


def test_display_sizes_are_tracked_tighter(qapp: QApplication) -> None:
    """Inter's dynamic metrics: text sizes keep the font's spacing; display sizes tighten
    as they grow, as Apple sets its display type."""
    assert tracking(13) == 0.0 and tracking(14) == 0.0
    assert tracking(15) < 0
    assert tracking(28) < tracking(20) < tracking(17) < tracking(15) < 0
    assert ui_font(13).letterSpacing() == 0
    font = ui_font(20, semibold=True)
    assert font.letterSpacingType() is QFont.SpacingType.AbsoluteSpacing
    assert font.letterSpacing() == pytest.approx(tracking(20), abs=1 / 64)  # Qt's fixed point.


def test_icon_pixmap_renders_at_device_pixel_ratio(qapp: QApplication) -> None:
    pixmap = icon_pixmap("refresh", DARK.text, 16, 2.0)
    assert not pixmap.isNull()
    assert pixmap.devicePixelRatio() == 2.0
    assert pixmap.width() == 32
    assert pixmap.toImage().pixelColor(0, 0).alpha() == 0


def test_every_bundled_icon_renders(qapp: QApplication) -> None:
    for name in ICON_NAMES:
        image = icon_pixmap(name, DARK.text, 16, 2.0).toImage()
        assert any(
            image.pixelColor(x, y).alpha() for x in range(image.width()) for y in (12, 16, 20)
        ), name


def test_tabular_figures_line_digits_up(qapp: QApplication) -> None:
    register_fonts()
    tabular = ui_metrics(12, tabular=True)
    assert tabular.horizontalAdvance("11:11") == tabular.horizontalAdvance("00:00")
    assert ui_font(12, tabular=True).isFeatureSet(QFont.Tag("tnum"))
    assert not ui_font(12).isFeatureSet(QFont.Tag("tnum"))


def test_unknown_icon_is_refused(qapp: QApplication) -> None:
    with pytest.raises(ValueError):
        icon_pixmap("../../secrets", DARK.text, 16)


def test_registering_fonts_twice_is_harmless(qapp: QApplication) -> None:
    register_fonts()
    register_fonts()


def test_missing_fonts_still_apply_the_colours(
    qapp: QApplication, themed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assets, "_fonts_loaded", None)
    monkeypatch.setattr(QFontDatabase, "addApplicationFont", lambda path: -1)
    qapp.setFont(QFont("Helvetica", 9))
    before = qapp.font().family()
    tokens = apply_theme(qapp, ThemeMode.DARK)
    assert register_fonts() is False  # Cached: a failed load isn't retried.
    assert tokens == DARK and current_tokens() == DARK
    assert qapp.font().family() == before
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == DARK.panel
    assert qapp.styleSheet() == build_stylesheet(DARK)


def test_releasing_the_icon_cache_empties_it(qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    icon_pixmap("sun", DARK.text, 16)
    assert icon_pixmap.cache_info().currsize > 0
    assets.release_icon_cache()
    assert icon_pixmap.cache_info().currsize == 0
    # apply_theme runs it on aboutToQuit; tests never emit the shared app's signals.
    assert qapp.isSignalConnected(QMetaMethod.fromSignal(qapp.aboutToQuit))


def test_the_stylesheet_leaves_push_buttons_to_the_style() -> None:
    """Every push button is drawn as a glass pill by ThemeStyle (see test_controls); only
    the sidebar's rows keep a stylesheet rule."""
    sheet = build_stylesheet(DARK)
    rules = [line for line in sheet.splitlines() if line.startswith("QPushButton")]
    assert rules and all('[variant="nav"]' in rule for rule in rules)


def test_the_theme_style_never_underlines_mnemonics(qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    hint = QStyle.StyleHint.SH_UnderlineShortcut
    assert qapp.style().styleHint(hint) == 0
    qapp.setStyleSheet("")
    style = qapp.style()
    assert isinstance(style, ThemeStyle)
    # Every other hint is Fusion's own.
    fusion = style.baseStyle()
    other = QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons
    assert style.styleHint(other) == fusion.styleHint(other)
    apply_theme(qapp)  # Applying again keeps the same style.
    qapp.setStyleSheet("")
    assert qapp.style() is style


def test_a_changed_font_never_changes_the_cache(qapp: QApplication) -> None:
    font = ui_font(13, medium=True)
    font.setPixelSize(40)
    assert ui_font(13, medium=True).pixelSize() == 13


def test_metrics_are_cached_per_size_and_weight(qapp: QApplication) -> None:
    assert ui_metrics(13) is ui_metrics(13)
    assert ui_metrics(13) is not ui_metrics(13, medium=True)
    assert ui_metrics(12).height() == QFontMetrics(ui_font(12)).height()


def test_releasing_the_font_cache_measures_again(qapp: QApplication) -> None:
    cached = ui_metrics(11)
    release_font_cache()
    assert ui_metrics(11) is not cached


def test_registering_the_fonts_drops_cached_metrics(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = ui_metrics(11)
    monkeypatch.setattr(assets, "_fonts_loaded", None)
    register_fonts()
    assert ui_metrics(11) is not cached


def broken_stylesheet(*args: object) -> str:
    raise RuntimeError("Theme stylesheet failed.")


def test_a_failed_theme_puts_back_what_it_found(
    qapp: QApplication, themed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    qapp.setStyle("Fusion")  # A plain style, not ThemeStyle.
    qapp.setStyleSheet("QLabel { margin: 1px; }")
    palette = qapp.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("white"))
    qapp.setPalette(palette)
    font = qapp.font()
    font.setPixelSize(17)
    qapp.setFont(font)
    qapp.setStyleSheet("")  # A stylesheet's proxy style hides the real style's name.
    name = qapp.style().name()
    qapp.setStyleSheet("QLabel { margin: 1px; }")
    monkeypatch.setattr("mailbrief.ui.theme.style.build_stylesheet", broken_stylesheet)

    with pytest.raises(RuntimeError):
        apply_theme(qapp)

    assert qapp.styleSheet() == "QLabel { margin: 1px; }"
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor("white")
    assert qapp.font().pixelSize() == 17
    assert current_tokens() is LIGHT
    qapp.setStyleSheet("")
    assert not isinstance(qapp.style(), ThemeStyle)
    assert qapp.style().name() == name


def test_a_failed_theme_keeps_the_theme_style_it_found(
    qapp: QApplication, themed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_theme(qapp)
    monkeypatch.setattr("mailbrief.ui.theme.style.build_stylesheet", broken_stylesheet)

    with pytest.raises(RuntimeError):
        apply_theme(qapp)

    qapp.setStyleSheet("")
    assert isinstance(qapp.style(), ThemeStyle)
    assert theme_style._style is qapp.style()  # Its Python override stays alive.
    assert current_tokens() is DARK


def test_the_mode_follows_the_applied_theme_and_a_failure(
    qapp: QApplication, themed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stored beside the scheme, not guessed from which tokens object is current."""
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.SAGE)
    assert current_mode() is ThemeMode.LIGHT
    monkeypatch.setattr("mailbrief.ui.theme.style.build_stylesheet", broken_stylesheet)
    with pytest.raises(RuntimeError):
        apply_theme(qapp, ThemeMode.DARK, ColorScheme.SAGE)
    # Put back: the light palette, so the light tokens and mode.
    assert current_mode() is ThemeMode.LIGHT
    assert current_tokens() == tokens_for(ThemeMode.LIGHT, ColorScheme.SAGE)
    assert current_scheme() is ColorScheme.SAGE


def test_a_theme_change_never_lets_a_collection_free_a_widget_qt_is_visiting(
    qapp: QApplication, themed: None
) -> None:
    """Qt copies its list of every widget, then visits each one. Windows here can only be
    freed by Python's cycle collector, and the first theme follower Qt reaches sets a
    collection off mid-walk; without the pause, Qt then visits freed widgets and crashes."""
    apply_theme(qapp)
    threshold = gc.get_threshold()

    def follower(label: QLabel) -> Callable[[], None]:
        def follow() -> None:
            gc.set_threshold(1)  # From here, an allocation may collect…
            label.setText(" ".join([label.text()]))  # …and this one allocates.

        return follow

    gc.set_threshold(1_000_000)  # Nothing collects the orphans before the walk.
    try:
        for _ in range(20):
            orphan = QWidget()
            orphan.cycle = orphan  # type: ignore[attr-defined]  # Only the collector frees it.
            label = QLabel("orphan", orphan)
            label.ensurePolished()  # Qt restyles, and so sends events to, polished widgets.
            follow_theme(label, follower(label))
        del orphan, label
        gc.set_threshold(1_000_000)  # follow_theme ran each follower once.
        apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.PLUM)
    finally:
        gc.set_threshold(*threshold)
    gc.collect()
    assert current_scheme() is ColorScheme.PLUM
    assert gc.isenabled()
