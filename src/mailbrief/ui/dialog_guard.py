"""Dialogs that keep the window to themselves without a modal session.

On macOS, Qt runs a modal dialog inside an AppKit modal session, and AppKit drops quit
requests (Cmd+Q, Quit in the Dock) while one is running, so MailBrief could only be force
quit with a dialog open. Its dialogs are shown non-modal instead, and DialogGuard does what
modality did: a transparent cover over the window takes its clicks and keys, and activating
the window brings the dialog back to the front. A quit request then reaches
MainWindow.closeEvent, which closes the dialogs and shuts down.
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QDialog, QWidget


class DialogGuard(QWidget):
    """A transparent cover over ``window`` while a dialog shown through it is open."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("dialogGuard")
        self.setAccessibleName("A dialog is open")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # The window is the parent, never kept here: a child holding its parent would leave
        # a dropped window (a dialog with its own guard) to Python's cycle collector, which
        # deletes Qt widgets in no safe order (see `follow_theme`).
        self._open: list[QDialog] = []  # The newest last: it is the one brought forward.
        self.hide()
        window.installEventFilter(self)

    def show_dialog(self, dialog: QDialog) -> None:
        """Show ``dialog`` in front of the window; the window takes no input until it closes.

        Use this instead of QDialog.open() or exec(), which start a modal session.
        """
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        # Its Hide, however it closes, lifts the cover. Installing again only moves the
        # filter, and a dialog's filters go with it when it is deleted.
        dialog.installEventFilter(self)
        if dialog in self._open:
            self._open.remove(dialog)
        self._open.append(dialog)
        window = self.parentWidget()
        if window is not None:
            self.setGeometry(window.rect())
        self.raise_()
        self.show()
        self.setFocus()  # So the window's shortcuts stop here if it is activated.
        dialog.show()
        self._front()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.WindowActivate):
            window = self.parentWidget()  # Only now: this filter sees every event.
            if window is None or watched is not window:
                return False  # A dialog's own resize or activation.
            if kind == QEvent.Type.Resize:
                self.setGeometry(window.rect())
            elif self._open:
                QTimer.singleShot(0, self._front)  # After the activation settles.
        elif kind == QEvent.Type.Hide and not event.spontaneous() and watched in self._open:
            # A spontaneous Hide is the window system's, such as Windows hiding a minimized
            # window's dialogs: the dialog is still open, so the cover stays.
            self._open.remove(watched)
            if not self._open:
                self.hide()
        return False

    def _front(self) -> None:
        """Bring the newest open dialog in front, where a modal one would have stayed."""
        if self._open:
            dialog = self._open[-1]
            dialog.raise_()
            dialog.activateWindow()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # The window's shortcuts don't fire under a dialog.
            return True
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        self._front()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        event.accept()
