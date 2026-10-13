"""A glass sampler for every colour scheme: a header with pill buttons, a sidebar with the
selected page raised, and pills on an opaque content pane.

With MAILBRIEF_UI_SHOTS set to a folder, each render is saved there as
``glass-{scheme}-{mode}-{dpr}x.png``, to compare with the Liquid Glass reference.
"""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mailbrief.ui.glass.backdrop import BackdropKey, render_backdrop
from mailbrief.ui.glass.painter import paint_panel, paint_pill, paint_raised
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
