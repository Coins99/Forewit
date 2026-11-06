"""A glass sampler for every colour scheme: a glass header with flat buttons, a sidebar
with the selected page raised, and buttons on an opaque content pane; and every flat
button state, drawn by ThemeStyle.

With MAILBRIEF_UI_SHOTS set to a folder, each render is saved there as
``glass-{scheme}-{mode}-{dpr}x.png`` and ``buttons-{mode}-{dpr}x.png``.
"""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.ui.glass.backdrop import BackdropKey, render_backdrop
from mailbrief.ui.glass.painter import GlassMode, paint_panel, paint_raised
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
from mailbrief.ui.theme.style import button_colors
from mailbrief.ui.theme.tokens import FIELD_RADIUS
from tests.ui.test_workspace import build
from tests.ui.window_wait import show

WIDTH, HEIGHT, HEADER = 680, 220, 44
PILL_HEIGHT = 30


def pill_width(text: str) -> float:
    return ui_metrics(TEXT_PX).horizontalAdvance(text) + 28


def pill(painter: QPainter, left: float, top: float, text: str, *, primary: bool = False) -> float:
    """A flat button starting at ``left``, in ThemeStyle's colours; returns where the next
    one may start."""
    tokens = current_tokens()
    rect = QRectF(left, top, pill_width(text), PILL_HEIGHT)
    fill, edge = button_colors(tokens, QStyle.StateFlag.State_Enabled, primary=primary)
    painter.setPen(QColor(edge or fill))
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), FIELD_RADIUS, FIELD_RADIUS)
    painter.setPen(QColor(tokens.on_primary if primary else tokens.text))
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
    painter.drawText(QRectF(14, 0, 200, HEADER), Qt.AlignmentFlag.AlignVCenter, "Forewit")
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
def test_button_states_render(
    qtbot: QtBot, qapp: QApplication, themed: None, theme: ThemeMode
) -> None:
    """Every flat button state, drawn by ThemeStyle as a real button would be. Saved as
    ``buttons-{theme}-{dpr}x.png`` with MAILBRIEF_UI_SHOTS set."""
    apply_theme(qapp, theme)
    host = QWidget()
    qtbot.addWidget(host)
    host.setAutoFillBackground(True)
    plain = outline_button("Normal", "plainButton")
    primary = outline_button("Primary", "primaryButton", primary=True)
    for button in (plain, primary):
        button.setParent(host)
        button.ensurePolished()
    s = QStyle.StateFlag
    states = [
        ("Normal", plain, s.State_Enabled),
        ("Hover", plain, s.State_Enabled | s.State_MouseOver),
        ("Pressed", plain, s.State_Enabled | s.State_MouseOver | s.State_Sunken),
        ("Checked", plain, s.State_Enabled | s.State_On),
        ("Disabled", plain, s.State_None),
        ("Primary", primary, s.State_Enabled),
        ("Primary hover", primary, s.State_Enabled | s.State_MouseOver),
    ]
    ratio = 2.0
    width = sum(round(pill_width(label)) + 14 for label, _button, _state in states) + 12
    image = QImage(round(width * ratio), round(56 * ratio), QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(QColor(current_tokens().panel))
    painter = QPainter(image)
    left, samples = 12, []
    for label, button, state in states:
        option = QStyleOptionButton()
        button.initStyleOption(option)
        option.text = label
        option.state = state
        size = button.style().sizeFromContents(
            QStyle.ContentsType.CT_PushButton,
            option,
            option.fontMetrics.size(0, label),
            button,
        )
        option.rect = QRect(left, 10, size.width(), size.height())
        button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
        samples.append(QRect(left + 6, 10 + size.height() // 2, 1, 1))
        left += size.width() + 8
    painter.end()
    colours = [image.pixelColor(round(r.x() * ratio), round(r.y() * ratio)).name() for r in samples]
    assert len(set(colours[:4])) == 4, colours  # Rest, hover, pressed and checked differ.
    assert colours[5] != colours[6]  # So does the primary under the pointer.
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        assert image.save(str(Path(folder) / f"buttons-{theme.value}-{ratio:g}x.png"))
