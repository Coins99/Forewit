"""Dialogs in the home screen's style: the window's glass, with its colour field, round a
rounded sheet that holds the dialog's content, so no dialog has a square edge.

The theme style installs `dialog_sheet()` on every dialog it polishes (the app's own,
message boxes and Qt's own), and gives each dialog's own layout DIALOG_MARGIN, so its
content sits inside the sheet.
"""

from collections import OrderedDict
from typing import Final

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog

from mailbrief.ui.glass.backdrop import Backdrop, BackdropKey, render_backdrop
from mailbrief.ui.glass.painter import paint_panel
from mailbrief.ui.glass.preferences import glass_preferences
from mailbrief.ui.hairline import crisp_fill_rect, crisp_rect, hairline_pen
from mailbrief.ui.theme import (
    DIALOG_GUTTER,
    SHEET_RADIUS,
    current_glass,
    current_mode,
    current_scheme,
    current_tokens,
)

CACHE_SIZE: Final = 4  # Backdrops kept, by dialog size and theme.


class DialogSheet(QObject):
    """Paints a dialog's glass and sheet before the dialog's content paints over them.
    A dialog's backdrop is small (a quarter of its size), so it is rendered on first paint
    at each size and kept."""

    def __init__(self) -> None:
        super().__init__()
        self._backdrops: OrderedDict[BackdropKey, Backdrop] = OrderedDict()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Paint and isinstance(watched, QDialog):
            self._paint(watched)
        return False  # The dialog and its content paint as usual, over the sheet.

    def backdrop(self, dialog: QDialog) -> Backdrop:
        key = BackdropKey(dialog.width(), dialog.height(), current_scheme(), current_mode())
        backdrop = self._backdrops.get(key)
        if backdrop is None:
            backdrop = render_backdrop(key)
            self._backdrops[key] = backdrop
            while len(self._backdrops) > CACHE_SIZE:
                self._backdrops.popitem(last=False)
        else:
            self._backdrops.move_to_end(key)
        return backdrop

    def _paint(self, dialog: QDialog) -> None:
        tokens = current_tokens()
        window = QRectF(dialog.rect())
        painter = QPainter(dialog)
        paint_panel(
            painter,
            window,
            current_glass(),
            self.backdrop(dialog),
            window,
            glass_preferences().mode,
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sheet = window.adjusted(DIALOG_GUTTER, DIALOG_GUTTER, -DIALOG_GUTTER, -DIALOG_GUTTER)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.panel))
        painter.drawRoundedRect(crisp_fill_rect(painter, sheet), SHEET_RADIUS, SHEET_RADIUS)
        painter.setPen(hairline_pen(tokens.hairline))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(crisp_rect(painter, sheet), SHEET_RADIUS, SHEET_RADIUS)
        painter.end()


_sheet: DialogSheet | None = None


def dialog_sheet() -> DialogSheet:
    """The one filter every dialog shares."""
    global _sheet
    if _sheet is None:
        _sheet = DialogSheet()
    return _sheet
