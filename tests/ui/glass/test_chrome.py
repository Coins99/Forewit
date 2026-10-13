"""The header and sidebar as glass over the workspace's backdrop, the selected page as a
raised pill, and the sidebar's muted text raised to secondary on glass."""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QPalette
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

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


@pytest.fixture(autouse=True)
def default_preferences() -> Iterator[None]:
    yield
    set_glass_preferences(GlassPreferences())


@pytest.fixture
def workspace(qtbot: QtBot, qapp: QApplication, themed: None) -> ThreePaneWorkspace:
    apply_theme(qapp)
    made = build(qtbot)
    made.resize(680, 520)
    show(qtbot, made)
    return made


def at(image: QImage, x: int, y: int) -> str:
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(x * ratio), round(y * ratio)).name()


def test_the_backdrop_follows_the_size_and_scheme(
    workspace: ThreePaneWorkspace, qapp: QApplication
) -> None:
    backdrop = workspace.backdrop
    assert backdrop is not None
    assert (backdrop.key.width, backdrop.key.height) == (workspace.width(), workspace.height())
    assert backdrop.key.scheme is ColorScheme.SLATE and backdrop.key.mode is ThemeMode.DARK
    workspace.resize(700, 540)
    assert workspace.backdrop is not None and workspace.backdrop.key.width == 700
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.PLUM)
    assert workspace.backdrop is not None
    assert workspace.backdrop.key.scheme is ColorScheme.PLUM
    assert workspace.backdrop.key.mode is ThemeMode.LIGHT


def test_the_header_and_sidebar_are_glass(workspace: ThreePaneWorkspace) -> None:
    tokens = current_tokens()
    header = workspace.header.grab().toImage()
    sidebar = workspace.sidebar.grab().toImage()
    # Neither is a flat token colour any more: the backdrop shows through the tint.
    assert at(header, 300, 20) not in (tokens.canvas, tokens.panel)
    assert at(sidebar, 64, workspace.sidebar.height() - 140) not in (tokens.canvas, tokens.panel)
    assert at(header, 300, 20) != at(header, 600, 20)  # The streaks vary across it.


@pytest.mark.parametrize("mode", [GlassMode.REDUCED_TRANSPARENCY, GlassMode.INCREASED_CONTRAST])
def test_accessibility_modes_paint_the_chrome_opaque(
    workspace: ThreePaneWorkspace, mode: GlassMode
) -> None:
    set_glass_preferences(GlassPreferences(mode=mode))
    header = workspace.header.grab().toImage()
    opaque = glass_for(ThemeMode.DARK).opaque
    assert at(header, 300, 20) == at(header, 600, 20) == opaque


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
