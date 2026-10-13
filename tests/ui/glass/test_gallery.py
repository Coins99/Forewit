"""A glass sampler for every colour scheme: a header with pill buttons, a sidebar with the
selected page raised, and pills on an opaque content pane.

With MAILBRIEF_UI_SHOTS set to a folder, each render is saved there as
``glass-{scheme}-{mode}-{dpr}x.png``, to compare with the Liquid Glass reference.
"""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.ui.glass.backdrop import BackdropKey, render_backdrop
from mailbrief.ui.glass.button import PRESSED_SCALE, GlassButton
from mailbrief.ui.glass.painter import GlassMode, paint_panel, paint_pill, paint_raised
from mailbrief.ui.glass.preferences import GlassPreferences, set_glass_preferences
from mailbrief.ui.labels import outline_button
from mailbrief.ui.theme import (
    SIDEBAR_WIDTH,
    TEXT_PX,
    ColorScheme,
    ThemeMode,
    apply_theme,
    current_glass,
    current_tokens,
    ui_font,
    ui_metrics,
)
from tests.ui.test_workspace import build
from tests.ui.window_wait import show

WIDTH, HEIGHT, HEADER = 680, 220, 44
PILL_HEIGHT = 30


def pill_width(text: str) -> float:
    return ui_metrics(TEXT_PX).horizontalAdvance(text) + 28


def pill(painter: QPainter, left: float, top: float, text: str, *, primary: bool = False) -> float:
    """A pill starting at ``left``; returns where the next one may start."""
    rect = QRectF(left, top, pill_width(text), PILL_HEIGHT)
    paint_pill(painter, rect, current_glass(), primary=primary)
    painter.setPen(QColor(current_tokens().text))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    return rect.right() + 8


def sampler(scheme: ColorScheme, mode: ThemeMode, dpr: float) -> QImage:
    tokens, glass = current_tokens(), current_glass()
    backdrop = render_backdrop(BackdropKey(WIDTH, HEIGHT, scheme, mode))
    picture = QImage(
        round(WIDTH * dpr), round(HEIGHT * dpr), QImage.Format.Format_ARGB32_Premultiplied
    )
    picture.setDevicePixelRatio(dpr)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(ui_font(TEXT_PX))
    header = QRectF(0, 0, WIDTH, HEADER)
    sidebar = QRectF(0, HEADER, SIDEBAR_WIDTH, HEIGHT - HEADER)
    content = QRectF(SIDEBAR_WIDTH, HEADER, WIDTH - SIDEBAR_WIDTH, HEIGHT - HEADER)
    for area in (header, sidebar):
        paint_panel(painter, area, glass, backdrop, area)
    painter.fillRect(content, QColor(tokens.panel))
    painter.setPen(QColor(tokens.text))
    painter.drawText(QRectF(14, 0, 200, HEADER), Qt.AlignmentFlag.AlignVCenter, "MailBrief")
    top = (HEADER - PILL_HEIGHT) / 2
    left = WIDTH - 12 - pill_width("Sync and review") - 8 - pill_width("Cancel")
    left = pill(painter, left, top, "Sync and review", primary=True)
    pill(painter, left, top, "Cancel")
    for row, label in enumerate(("Today", "Actions", "Waiting", "Drafts")):
        rect = QRectF(6, HEADER + 8 + row * 32, SIDEBAR_WIDTH - 12, 28)
        if row == 0:
            paint_raised(painter, rect, glass)
        painter.setPen(QColor(tokens.text if row == 0 else tokens.text_secondary))
        painter.drawText(rect.adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, label)
    painter.setPen(QColor(tokens.text))
    title = QRectF(content.left() + 16, content.top() + 12, 400, 24)
    painter.drawText(title, Qt.AlignmentFlag.AlignVCenter, "Q3 budget: approval needed by Friday")
    left = pill(painter, content.left() + 16, content.top() + 50, "Accept")
    pill(painter, left, content.top() + 50, "Dismiss")
    painter.end()
    return picture


@pytest.mark.parametrize("dpr", [1.0, 2.0])
@pytest.mark.parametrize("mode", list(ThemeMode))
@pytest.mark.parametrize("scheme", list(ColorScheme))
def test_glass_sampler_renders(
    qapp: QApplication, themed: None, scheme: ColorScheme, mode: ThemeMode, dpr: float
) -> None:
    apply_theme(qapp, mode, scheme)
    picture = sampler(scheme, mode, dpr)
    assert picture.width() == round(WIDTH * dpr)
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        assert picture.save(str(Path(folder) / f"glass-{scheme.value}-{mode.value}-{dpr:g}x.png"))


@pytest.mark.parametrize("mode", list(GlassMode))
def test_workspace_renders_in_each_glass_mode(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: GlassMode
) -> None:
    """Saved as ``accessibility-{mode}-{dpr}x.png`` with MAILBRIEF_UI_SHOTS set."""
    apply_theme(qapp)
    set_glass_preferences(GlassPreferences(mode=mode))
    workspace = build(qtbot)
    workspace.resize(680, 520)
    show(qtbot, workspace)
    image = workspace.grab()
    assert not image.isNull()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        name = f"accessibility-{mode.value}-{image.devicePixelRatio():g}x.png"
        assert image.save(str(Path(folder) / name))


@pytest.mark.parametrize("theme", list(ThemeMode))
def test_glass_button_states_render(
    qtbot: QtBot, qapp: QApplication, themed: None, theme: ThemeMode
) -> None:
    """Every pill state on the glass header. Saved as ``glass-states-{theme}-{dpr}x.png``
    with MAILBRIEF_UI_SHOTS set."""
    apply_theme(qapp, theme)
    workspace = build(qtbot)
    pills: dict[str, GlassButton] = {}
    for state in ("Normal", "Hover", "Pressed", "Focus", "Disabled", "Primary"):
        pill = outline_button(state, f"{state.lower()}Pill", glass=True)
        assert isinstance(pill, GlassButton)
        workspace.header.add_widget(pill)
        pills[state] = pill
    workspace.resize(1100, 720)
    show(qtbot, workspace)
    with qtbot.waitActive(workspace, timeout=5000):
        workspace.activateWindow()
    for pill in pills.values():
        pill.leaveEvent(QEvent(QEvent.Type.Leave))  # Shown at the pointer, one may be hovered.
    point = QPointF(12, pills["Hover"].height() / 2)
    pills["Hover"].enterEvent(QEnterEvent(point, point, point))
    pills["Hover"]._set_hover(1.0)  # Settled, not mid-animation.
    pills["Pressed"].setDown(True)
    pills["Pressed"]._set_scale(PRESSED_SCALE)
    pills["Focus"].setFocus()
    qtbot.waitUntil(pills["Focus"].hasFocus)
    pills["Disabled"].setEnabled(False)
    pills["Primary"].primary = True
    image = workspace.header.grab()
    assert not image.isNull()
    states = [pill.grab().toImage() for pill in pills.values()]
    assert len({state.size().width() for state in states}) > 1  # Each its own label.
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        name = f"glass-states-{theme.value}-{image.devicePixelRatio():g}x.png"
        assert image.save(str(Path(folder) / name))
