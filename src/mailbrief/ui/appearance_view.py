"""The Settings Appearance tab: the owner's colour scheme, light or dark mode and how
glass is drawn, applied at once."""

from collections.abc import Mapping
from enum import StrEnum
from functools import partial
from typing import Final

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QRadioButton, QVBoxLayout, QWidget

from mailbrief.ui.appearance import ModeChoice
from mailbrief.ui.glass.preferences import Motion, Transparency
from mailbrief.ui.glass.system import SystemAccessibility
from mailbrief.ui.labels import plain_label
from mailbrief.ui.theme import (
    DEFAULT_SCHEME,
    TEXT_PX,
    ColorScheme,
    ThemeMode,
    current_mode,
    follow_theme,
    tokens_for,
)

SCHEME_NAMES: Final = {
    ColorScheme.STUDIO: "Studio (white and grey, default)",
    ColorScheme.SLATE: "Slate (grey-blue)",
    ColorScheme.GRAPHITE: "Graphite (warm grey)",
    ColorScheme.OCEAN: "Ocean (teal)",
    ColorScheme.SAGE: "Sage (green)",
    ColorScheme.PLUM: "Plum (purple)",
    ColorScheme.ROSE: "Rose (pink)",
}
MODE_NAMES: Final = {
    ModeChoice.SYSTEM: "Follow the system",
    ModeChoice.LIGHT: "Light",
    ModeChoice.DARK: "Dark",
}
TRANSPARENCY_NAMES: Final = {
    Transparency.SYSTEM: "Follow the system",
    Transparency.SOLID: "Always solid",
}
MOTION_NAMES: Final = {
    Motion.SYSTEM: "Follow the system",
    Motion.REDUCED: "Reduce motion",
}
SWATCH_PX: Final = 18


def swatch(scheme: ColorScheme, dpr: float = 2.0, mode: ThemeMode = ThemeMode.DARK) -> QPixmap:
    """A dot of the scheme's panel in ``mode``, ringed with its accent."""
    tokens = tokens_for(mode, scheme)
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


def system_note(system: SystemAccessibility) -> str:
    """What the system's own settings do to glass, whatever the owner chose here."""
    notes = []
    if system.increase_contrast:
        notes.append("Your system increases contrast, so glass is solid with clear edges.")
    elif system.reduce_transparency:
        notes.append("Your system reduces transparency, so glass is solid.")
    if system.reduce_motion:
        notes.append("Your system reduces motion, so glass doesn't move.")
    return " ".join(notes)


def _check_quietly(button: QRadioButton) -> None:
    button.blockSignals(True)
    button.setChecked(True)
    button.blockSignals(False)


class AppearancePanel(QWidget):
    """Four sets of radio buttons: the colour scheme, mode, glass and motion; arrow keys
    move within a set. A choice emits ``scheme_chosen``, ``mode_chosen``,
    ``transparency_chosen`` or ``motion_chosen``; the window applies and saves it."""

    scheme_chosen = Signal(object)
    mode_chosen = Signal(object)
    transparency_chosen = Signal(object)
    motion_chosen = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appearancePanel")
        self.setAccessibleName("Appearance")
        self._layout = QVBoxLayout(self)
        self.buttons = self._choices("Colour", "scheme", SCHEME_NAMES)
        for scheme, button in self.buttons.items():
            # Each swatch shows its scheme in the mode in effect.
            follow_theme(button, partial(self._paint_swatch, button, scheme))
            button.setIconSize(QSize(SWATCH_PX, SWATCH_PX))
        self.mode_buttons = self._choices("Mode", "mode", MODE_NAMES, qualify=True)
        # Both sets offer "Follow the system", so screen readers hear the heading too.
        self.transparency_buttons = self._choices(
            "Glass", "glass", TRANSPARENCY_NAMES, qualify=True
        )
        self.motion_buttons = self._choices("Motion", "motion", MOTION_NAMES, qualify=True)
        self.system_note = plain_label("", tone="secondary")
        self.system_note.setObjectName("appearanceSystemNote")
        self.system_note.hide()
        self._layout.addWidget(self.system_note)
        self._layout.addWidget(
            plain_label(
                "Changes apply straight away and are kept on this device.", tone="secondary"
            )
        )
        self._layout.addStretch(1)
        self.set_scheme(DEFAULT_SCHEME)
        self.set_mode(ModeChoice.SYSTEM)
        self.set_choices(Transparency.SYSTEM, Motion.SYSTEM)

    @staticmethod
    def _paint_swatch(button: QRadioButton, scheme: ColorScheme) -> None:
        button.setIcon(QIcon(swatch(scheme, mode=current_mode())))

    def _choices[Choice: StrEnum](
        self,
        heading: str,
        prefix: str,
        names: Mapping[Choice, str],
        *,
        qualify: bool = False,
    ) -> dict[Choice, QRadioButton]:
        """A heading and one radio button per choice, in a group of their own."""
        self._layout.addWidget(plain_label(heading, px=TEXT_PX, medium=True))
        group = QButtonGroup(self)
        buttons: dict[Choice, QRadioButton] = {}
        for choice, name in names.items():
            button = QRadioButton(name)
            button.setObjectName(f"{prefix}-{choice.value}")
            button.setAccessibleName(f"{heading}: {name}" if qualify else name)
            group.addButton(button)
            buttons[choice] = button
            self._layout.addWidget(button)
            # A bound method of the panel, so the connection never outlives it.
            button.toggled.connect(partial(self._toggled, choice))
        return buttons

    def _toggled(self, choice: StrEnum, checked: bool) -> None:
        if not checked:
            return
        if isinstance(choice, ColorScheme):
            self.scheme_chosen.emit(choice)
        elif isinstance(choice, ModeChoice):
            self.mode_chosen.emit(choice)
        elif isinstance(choice, Transparency):
            self.transparency_chosen.emit(choice)
        else:
            self.motion_chosen.emit(choice)

    def set_scheme(self, scheme: ColorScheme) -> None:
        """Show ``scheme`` as chosen without emitting ``scheme_chosen``."""
        _check_quietly(self.buttons[scheme])

    def set_mode(self, choice: ModeChoice) -> None:
        """Show the mode choice without emitting ``mode_chosen``."""
        _check_quietly(self.mode_buttons[choice])

    def set_choices(self, transparency: Transparency, motion: Motion) -> None:
        """Show the glass and motion choices without emitting."""
        _check_quietly(self.transparency_buttons[transparency])
        _check_quietly(self.motion_buttons[motion])

    def set_system(self, system: SystemAccessibility) -> None:
        note = system_note(system)
        self.system_note.setText(note)
        self.system_note.setVisible(bool(note))

    def chosen(self) -> ColorScheme:
        return next(scheme for scheme, button in self.buttons.items() if button.isChecked())

    def chosen_mode(self) -> ModeChoice:
        return next(m for m, button in self.mode_buttons.items() if button.isChecked())

    def chosen_transparency(self) -> Transparency:
        return next(t for t, button in self.transparency_buttons.items() if button.isChecked())

    def chosen_motion(self) -> Motion:
        return next(m for m, button in self.motion_buttons.items() if button.isChecked())
