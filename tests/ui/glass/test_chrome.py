"""The header and sidebar as glass over the workspace's backdrop, the selected page as a
raised pill, and the sidebar's muted text raised to secondary on glass."""

import pytest
from PySide6.QtCore import QAbstractAnimation, QPoint
from PySide6.QtGui import QImage, QPalette
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from mailbrief.ui.glass.motion import GLIDE_MS
from mailbrief.ui.glass.painter import GlassMode
from mailbrief.ui.glass.preferences import GlassPreferences, set_glass_preferences
from mailbrief.ui.theme import (
    ColorScheme,
    ThemeMode,
    apply_theme,
    current_tokens,
    glass_for,
)
from mailbrief.ui.workspace import ThreePaneWorkspace
from tests.ui.test_workspace import build
from tests.ui.window_wait import show


@pytest.fixture
def workspace(qtbot: QtBot, qapp: QApplication, themed: None) -> ThreePaneWorkspace:
    apply_theme(qapp)
    made = build(qtbot)
    made.resize(680, 520)
    show(qtbot, made)
    return made


def header_row(header: QImage) -> list[str]:
    """Colours along the header's middle, every 10 px between the title on the left and
    the status on the right, so no sample lands on a glyph on any platform."""
    return [at(header, x, 20) for x in range(130, 440, 10)]


def at(image: QImage, x: int, y: int) -> str:
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(x * ratio), round(y * ratio)).name()


def test_the_backdrop_follows_the_size_and_scheme(
    workspace: ThreePaneWorkspace, qapp: QApplication
) -> None:
    backdrop = workspace.backdrop
    assert backdrop is not None
    assert (backdrop.key.width, backdrop.key.height) == (workspace.width(), workspace.height())
    assert backdrop.key.scheme is ColorScheme.STUDIO and backdrop.key.mode is ThemeMode.DARK
    workspace.resize(700, 540)
    assert workspace.backdrop is not None and workspace.backdrop.key.width == 700
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.PLUM)
    assert workspace.backdrop is not None
    assert workspace.backdrop.key.scheme is ColorScheme.PLUM
    assert workspace.backdrop.key.mode is ThemeMode.LIGHT


def test_the_header_and_sidebar_are_glass(
    workspace: ThreePaneWorkspace, qapp: QApplication
) -> None:
    apply_theme(qapp, ThemeMode.DARK, ColorScheme.SLATE)  # A scheme with streaks.
    tokens = current_tokens()
    header = workspace.header.grab().toImage()
    sidebar = workspace.sidebar.grab().toImage()
    # Neither is a flat token colour any more: the backdrop shows through the tint.
    row = header_row(header)
    assert not set(row) & {tokens.canvas, tokens.panel}
    assert at(sidebar, 64, workspace.sidebar.height() - 140) not in (tokens.canvas, tokens.panel)
    assert len(set(row)) > 1  # The streaks vary across it.


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_studio_glass_shows_its_colour_field(
    workspace: ThreePaneWorkspace, qapp: QApplication, mode: ThemeMode
) -> None:
    """Studio's glass is calm (a flat backdrop with no streaks) but not empty: its pools of
    colour show through the header and the sidebar, and differ from one to the other."""
    apply_theme(qapp, mode, ColorScheme.STUDIO)
    glass = glass_for(mode, ColorScheme.STUDIO)
    assert glass.backdrop_top == glass.backdrop_bottom and glass.streak.alpha == 0
    header = workspace.header.grab().toImage()
    sidebar = workspace.sidebar.grab().toImage()
    row = header_row(header)
    assert len(set(row)) > 1  # The field flows across the header.
    assert at(sidebar, 64, workspace.sidebar.height() - 20) not in row
    assert glass.opaque not in row  # It isn't the solid fallback.


@pytest.mark.parametrize("mode", [GlassMode.REDUCED_TRANSPARENCY, GlassMode.INCREASED_CONTRAST])
def test_accessibility_modes_paint_the_chrome_opaque(
    workspace: ThreePaneWorkspace, mode: GlassMode
) -> None:
    set_glass_preferences(GlassPreferences(mode=mode))
    header = workspace.header.grab().toImage()
    opaque = glass_for(ThemeMode.DARK).opaque
    assert set(header_row(header)) == {opaque}


def test_the_selected_page_is_a_raised_pill(workspace: ThreePaneWorkspace) -> None:
    nav = workspace.sidebar.nav
    image = workspace.sidebar.grab().toImage()
    offset = nav.mapTo(workspace.sidebar, QPoint())
    today = nav.visualRect(nav.model().index(0, 0)).translated(offset)
    actions = nav.visualRect(nav.model().index(1, 0)).translated(offset)
    x = today.left() + 4  # Inside the pill, left of the icon.
    raised = image.pixelColor(
        round(x * image.devicePixelRatio()), round(today.center().y() * image.devicePixelRatio())
    )
    flat = image.pixelColor(
        round(x * image.devicePixelRatio()), round(actions.center().y() * image.devicePixelRatio())
    )
    assert raised.lightness() > flat.lightness()


def test_muted_text_in_the_sidebar_shows_as_secondary(workspace: ThreePaneWorkspace) -> None:
    """text_muted fails contrast on glass, so the sidebar's note and status lines use the
    secondary colour there; muted text elsewhere is unchanged."""
    tokens = current_tokens()
    note = workspace.sidebar.note
    assert note.property("tone") == "muted"
    assert note.palette().color(QPalette.ColorRole.WindowText).name() == tokens.text_secondary
    elsewhere = workspace.coverage
    assert isinstance(elsewhere, QLabel)
    assert elsewhere.palette().color(QPalette.ColorRole.WindowText).name() == tokens.text_muted


def test_the_selected_page_glides_to_the_next(workspace: ThreePaneWorkspace, qtbot: QtBot) -> None:
    """The raised pill moves from the old page to the new one on the fluid curve rather
    than jumping; with motion reduced it is there at once."""
    nav = workspace.sidebar.nav
    model = nav.model()
    start = nav.shown_pill()
    assert start is not None
    nav.setCurrentIndex(model.index(2, 0))
    assert nav.glide.state() is QAbstractAnimation.State.Running
    assert nav.glide.duration() == GLIDE_MS
    end = nav.pill_rect(model.index(2, 0))
    qtbot.waitUntil(lambda: nav.glide.state() is QAbstractAnimation.State.Stopped, timeout=3000)
    assert nav.shown_pill() == end
    set_glass_preferences(GlassPreferences(reduce_motion=True))
    nav.setCurrentIndex(model.index(0, 0))
    assert nav.glide.state() is QAbstractAnimation.State.Stopped
    assert nav.shown_pill() == nav.pill_rect(model.index(0, 0))
