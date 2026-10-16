"""Shared Today-based heading and spacing for native desktop dialogs."""

from PySide6.QtWidgets import QDialog, QVBoxLayout

from mailbrief.ui.labels import plain_label
from mailbrief.ui.theme import ui_font


def dialog_heading(dialog: QDialog, layout: QVBoxLayout, title: str, description: str = "") -> None:
    dialog.setFont(ui_font(10))
    if not dialog.objectName():
        dialog.setObjectName(type(dialog).__name__)
    if not dialog.accessibleName():
        dialog.setAccessibleName(title)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(12)
    heading = plain_label(title, px=21, medium=True)
    heading.setObjectName("dialogHeading")
    layout.addWidget(heading)
    if description:
        caption = plain_label(description, tone="muted", px=12)
        caption.setObjectName("dialogDescription")
        layout.addWidget(caption)
