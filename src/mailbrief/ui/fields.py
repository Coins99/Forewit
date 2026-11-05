"""Rules shared by the editors' typed fields."""

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QComboBox, QWidget

from mailbrief.domain.projects import Project

# Keys a closed combo box would use to change its choice; a PopupChoice opens instead.
_STEP_KEYS: Final = frozenset(
    {
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
    }
)


def takes_wheel(field: QWidget, event: QWheelEvent) -> bool:
    """Whether the wheel may change ``field``: only while it has focus, so scrolling over a
    dialog never changes a field, and never by a trackpad's coasting after the fingers lift.
    When it may not, ``event`` is ignored, to scroll what is beneath, or taken, to swallow
    the coasting."""
    if not field.hasFocus():
        event.ignore()
        return False
    if event.phase() == Qt.ScrollPhase.ScrollMomentum:
        event.accept()
        return False
    return True


class PopupChoice(QComboBox):
    """A combo box whose choice changes only in its popup, for a choice saved as soon as it
    is made (a note's project, ADR 0027).

    A closed QComboBox changes its choice, and emits ``activated``, on the wheel, the arrow
    keys and typed letters. Here the wheel scrolls what is beneath and those keys open the
    popup, so passing over the field or stepping through it saves nothing; ``activated``
    comes only from a choice in the popup.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        plain = event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        )
        typed = bool(event.text().strip()) and plain
        if (event.key() in _STEP_KEYS and plain) or typed:
            event.accept()
            self.showPopup()
            return
        super().keyPressEvent(event)


def fill_project_choice(
    combo: QComboBox,
    projects: tuple[Project, ...],
    chosen: str | None,
    *,
    none_label: str,
    fallback_name: str | None = None,
) -> None:
    """The projects a record may choose: ``none_label``, then every project that takes new
    links, plus the record's own archived one, labelled as such; ``chosen`` is selected.

    When ``chosen`` isn't in ``projects`` (the list hasn't loaded it), it is kept under
    ``fallback_name``, if there is one. Signals are the caller's to block.
    """
    combo.clear()
    combo.addItem(none_label, None)
    for project in projects:
        if project.archived_at_utc is None or project.public_id == chosen:
            suffix = " (archived)" if project.archived_at_utc else ""
            combo.addItem(project.name + suffix, project.public_id)
    if chosen is not None and combo.findData(chosen) < 0 and fallback_name is not None:
        combo.addItem(fallback_name, chosen)
    combo.setCurrentIndex(max(0, combo.findData(chosen)))
