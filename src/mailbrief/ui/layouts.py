"""Moving a widget from one layout to another without leaving PySide a stale wrapper."""

from PySide6.QtWidgets import QLayout, QWidget


def take_widget(widget: QWidget) -> None:
    """Take ``widget`` out of the layout that holds it; call it before another layout, or
    another cell of the same one, takes the widget.

    PySide wraps every item of a filled layout added to another (``addLayout``,
    ``insertLayout``) and keeps the wrappers. When another layout takes the widget, Qt
    deletes its item but PySide is never told: the wrapper stays valid and registered at
    the freed address, and a QObject later made there is handed it as its own wrapper. Its
    meta-object is then read from a layout item, and the app crashes (Windows CI, in a
    QCompleter's constructor). ``removeWidget`` on the layout that holds the item
    invalidates its wrapper first. Called on an outer layout instead, Qt finds the widget
    but PySide doesn't, so this finds the layout that holds it.

    Too late, it can't help: once ``setParent`` or another layout has taken the widget, its
    item is gone and this finds nothing to do. The UI tests check after every test that no
    such wrapper is left (``tests/ui/layout_items.py``)."""
    parent = widget.parentWidget()
    top = parent.layout() if parent is not None else None
    if top is None:
        return
    # The parent's own layouts: those of the widgets inside it can't hold this one.
    for layout in (top, *top.findChildren(QLayout)):
        if layout.indexOf(widget) >= 0:
            layout.removeWidget(widget)
            return
