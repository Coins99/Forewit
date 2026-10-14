"""Rules shared by the editors' typed fields."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QWidget


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
