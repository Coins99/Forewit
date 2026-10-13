"""Keeps how glass is drawn in step with the system's accessibility settings and the
owner's choices, and repaints when that changes."""

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from mailbrief.ui.glass.preferences import (
    Motion,
    Transparency,
    effective_preferences,
    glass_preferences,
    set_glass_preferences,
)
from mailbrief.ui.glass.system import SystemAccessibility, read_system
from mailbrief.ui.theme import paused_collection


class GlassAccessibility(QObject):
    """Reads the system's settings now, whenever MailBrief becomes the active app (after
    the owner changes one in System Settings, say) and whenever Qt reports a contrast
    change. ``changed`` emits the system's settings after every check.

    It listens to the application's signals, never filters its events: a filter on the
    application sees every event of every object."""

    changed = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        transparency: Transparency = Transparency.SYSTEM,
        motion: Motion = Motion.SYSTEM,
        read: Callable[[], SystemAccessibility] = read_system,
    ) -> None:
        super().__init__(parent)
        self._read = read
        self.transparency = transparency
        self.motion = motion
        self.system = SystemAccessibility()
        application = QApplication.instance()
        if isinstance(application, QApplication):
            # Slots of this object: Qt drops the connections when it is destroyed.
            application.applicationStateChanged.connect(self._state_changed)
            hints = application.styleHints().accessibility()
            hints.contrastPreferenceChanged.connect(self._contrast_changed)
        self.refresh()

    def set_choices(self, transparency: Transparency, motion: Motion) -> None:
        self.transparency = transparency
        self.motion = motion
        self._apply()

    def refresh(self) -> None:
        self.system = self._read()
        self._apply()

    def _state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.refresh()

    def _contrast_changed(self, preference: Qt.ContrastPreference) -> None:
        self.refresh()

    def _apply(self) -> None:
        preferences = effective_preferences(self.system, self.transparency, self.motion)
        if preferences != glass_preferences():
            set_glass_preferences(preferences)
            # Glass is read at paint time. A window repaints its children with it, so the
            # windows are enough, with no wrapper made for every widget in the app.
            with paused_collection():
                for window in QApplication.topLevelWidgets():
                    window.update()
        self.changed.emit(self.system)
