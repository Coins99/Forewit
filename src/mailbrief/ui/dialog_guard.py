"""Dialogs that keep the window to themselves without a modal session.

On macOS, Qt runs a modal dialog inside an AppKit modal session, and AppKit drops quit
requests (Cmd+Q, Quit in the Dock) while one is running, so MailBrief could only be force
quit with a dialog open. Its dialogs are shown non-modal instead, and DialogGuard does what
modality did: a transparent cover over the window takes its clicks and keys, and activating
the window brings the dialog back to the front. A quit request then reaches
MainWindow.closeEvent, which closes the dialogs and shuts down.
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PySide6.QtWidgets import QDialog, QGraphicsBlurEffect, QLayout, QPushButton, QWidget

from mailbrief.ui.theme import current_tokens


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
        self._blur_target: QWidget | None = None
        self.hide()
        window.installEventFilter(self)

    def show_dialog(self, dialog: QDialog) -> None:
        """Show ``dialog`` in front of the window; the window takes no input until it closes.

        Use this instead of QDialog.open() or exec(), which start a modal session.
        """
        window = self.parentWidget()
        if window is None:
            return
        presentation = dialog.property("presentation")
        if presentation in ("drawer", "center"):
            dialog.setParent(window)
            dialog.setWindowFlags(Qt.WindowType.Widget)
            if (layout := dialog.layout()) is not None:
                layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            dialog.setMinimumSize(0, 0)
            self._position(dialog)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        # Its Hide, however it closes, lifts the cover. Installing again only moves the
        # filter, and a dialog's filters go with it when it is deleted.
        dialog.installEventFilter(self)
        if dialog in self._open:
            self._open.remove(dialog)
        self._open.append(dialog)
        if presentation == "center" and self._blur_target is None:
            target = window.findChild(QWidget, "workspace")
            if target is not None and target.graphicsEffect() is None:
                blur = QGraphicsBlurEffect(target)
                blur.setBlurRadius(2)
                target.setGraphicsEffect(blur)
                self._blur_target = target
        self.setGeometry(window.rect())
        self.raise_()
        self.show()
        self.update()
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
                for dialog in self._open:
                    self._position(dialog)
            elif self._open:
                QTimer.singleShot(0, self._front)  # After the activation settles.
        elif kind == QEvent.Type.Hide and not event.spontaneous() and watched in self._open:
            # A spontaneous Hide is the window system's, such as Windows hiding a minimized
            # window's dialogs: the dialog is still open, so the cover stays.
            self._open.remove(watched)
            if self._blur_target is not None and not any(
                dialog.property("presentation") == "center" for dialog in self._open
            ):
                # Qt accepts nullptr to remove/delete the effect; PySide's stub omits it.
                self._blur_target.setGraphicsEffect(None)  # type: ignore[arg-type]
                self._blur_target = None
            if not self._open:
                self.hide()
        return False

    def _position(self, dialog: QDialog) -> None:
        window = self.parentWidget()
        if window is None:
            return
        if dialog.property("presentation") == "drawer":
            width = min(390, max(320, window.width() * 46 // 100))
            # Extra native fields need more height than the web example. Fill short
            # windows so the footer remains accessible instead of clipping Save.
            full_height = dialog.minimumSizeHint().height() > window.height() - 185
            top, bottom = (0, 0) if full_height else (137, 48)
            dialog.setGeometry(window.width() - width, top, width, window.height() - top - bottom)
            close = dialog.findChild(QPushButton, "overlayClose")
            if close is None:
                close = QPushButton("×", dialog)
                close.setObjectName("overlayClose")
                close.setAccessibleName("Close panel")
                close.clicked.connect(dialog.reject)
            close.setGeometry(width - 38, 8, 28, 28)
            close.show()
            close.raise_()
        elif dialog.property("presentation") == "center":
            width = min(920, window.width() - 56)
            height = min(660, window.height() * 86 // 100)
            dialog.setGeometry(
                (window.width() - width) // 2,
                (window.height() - height) // 2,
                width,
                height,
            )

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._open and self._open[-1].property("presentation") == "center":
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(current_tokens().overlay))
            painter.end()

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
