"""List widgets shared by the window's panels and dialogs."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QListWidget

from mailbrief.ui.theme import paused_collection


class ActivatingList(QListWidget):
    """Return and Enter activate the current row exactly once, on every platform.

    Qt's item views differ by platform: on macOS they only try to edit an item on Return,
    and elsewhere they activate it and then let the key reach the dialog's default button,
    which would act a second time. Here the key activates the row and is consumed.
    """

    def keyPressEvent(self, event: QKeyEvent) -> None:
        item = self.currentItem()
        if item is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.itemActivated.emit(item)
            event.accept()
            return
        super().keyPressEvent(event)


def clear_list(widget: QListWidget) -> None:
    """Remove every row of ``widget``; use it wherever ``QListWidget.clear()`` would go.

    ``clear()`` deletes the items inside Qt's bulk model reset, and a garbage collection of
    their Python wrappers or data during that native walk can crash on Cocoa. Here each
    item is taken out and released on its own, with collection paused. The selection is
    cleared first, as ``clear()`` does, so the current row changes once (to none), not once
    per row removed."""
    widget.selectionModel().clear()
    with paused_collection():
        while count := widget.count():
            item = widget.takeItem(count - 1)
            del item
