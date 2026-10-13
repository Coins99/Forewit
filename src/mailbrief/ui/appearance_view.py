"""The Settings Appearance tab: the owner's colour scheme, applied at once."""

from functools import partial
from typing import Final

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QRadioButton, QVBoxLayout, QWidget

from mailbrief.ui.labels import plain_label
from mailbrief.ui.theme import TEXT_PX, ColorScheme, ThemeMode, tokens_for

SCHEME_NAMES: Final = {
    ColorScheme.SLATE: "Slate (grey-blue, default)",
    ColorScheme.GRAPHITE: "Graphite (warm grey)",
    ColorScheme.OCEAN: "Ocean (teal)",
    ColorScheme.SAGE: "Sage (green)",
    ColorScheme.PLUM: "Plum (purple)",
    ColorScheme.ROSE: "Rose (pink)",
}
SWATCH_PX: Final = 18


def swatch(scheme: ColorScheme, dpr: float = 2.0) -> QPixmap:
    """A dot of the scheme's dark panel, ringed with its accent."""
    tokens = tokens_for(ThemeMode.DARK, scheme)
    pixmap = QPixmap(round(SWATCH_PX * dpr), round(SWATCH_PX * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.accent_fg))
    painter.drawEllipse(QRectF(0, 0, SWATCH_PX, SWATCH_PX))
    painter.setBrush(QColor(tokens.panel))
    painter.drawEllipse(QRectF(3, 3, SWATCH_PX - 6, SWATCH_PX - 6))
    painter.end()
    return pixmap


class AppearancePanel(QWidget):
    """One radio button per scheme; arrow keys move between them. Choosing one emits
    ``scheme_chosen``; the window applies and saves it."""

    scheme_chosen = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appearancePanel")
        self.setAccessibleName("Appearance")
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("Colour", px=TEXT_PX, medium=True))
        self.group = QButtonGroup(self)
        self.buttons: dict[ColorScheme, QRadioButton] = {}
        for scheme in ColorScheme:
            button = QRadioButton(SCHEME_NAMES[scheme])
            button.setObjectName(f"scheme-{scheme.value}")
            button.setAccessibleName(SCHEME_NAMES[scheme])
            button.setIcon(QIcon(swatch(scheme)))
            button.setIconSize(QSize(SWATCH_PX, SWATCH_PX))
            self.group.addButton(button)
            self.buttons[scheme] = button
            layout.addWidget(button)
            button.toggled.connect(partial(self._toggled, scheme))
        layout.addWidget(
            plain_label(
                "Colours change straight away and are kept on this device.", tone="secondary"
            )
        )
        layout.addStretch(1)
        self.set_scheme(ColorScheme.SLATE)

    def set_scheme(self, scheme: ColorScheme) -> None:
        """Show ``scheme`` as chosen without emitting ``scheme_chosen``."""
        button = self.buttons[scheme]
        button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(False)

    def _toggled(self, scheme: ColorScheme, checked: bool) -> None:
        if checked:
            self.scheme_chosen.emit(scheme)

    def chosen(self) -> ColorScheme:
        return next(scheme for scheme, button in self.buttons.items() if button.isChecked())
