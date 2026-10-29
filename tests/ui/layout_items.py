"""Find PySide wrappers of layout items that no layout holds any more.

Qt deletes a layout item when its widget moves to another layout, and PySide is never told:
the item's wrapper stays registered at the freed address, and a QObject later made there
gets it as its own (`mailbrief.ui.layouts.take_widget`). Nothing here calls
``QLayout.itemAt``, which wraps every item it returns and keeps the wrapper: a check that
did would make the very wrappers it looks for."""

import re

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLayout, QLayoutItem
from shiboken6 import Shiboken

# Shiboken.dump names the wrapper that keeps an item's wrapper alive: the layout it was
# added under, which holds the item or has a layout inside it that does.
_PARENT = re.compile(r"^parent\.+ <.* at (0x[0-9a-f]+)>$", re.MULTILINE)


def stale_item_wrappers() -> list[str]:
    """Describe each valid wrapper of a widget or spacer item that no layout under its
    parent layout holds. An item at the same address in another layout doesn't count: Qt
    may have reused the freed item's memory for it."""
    wrappers = Shiboken.getAllValidWrappers()
    by_id = {id(wrapper): wrapper for wrapper in wrappers}
    stale = []
    for wrapper in wrappers:
        # A layout is an item too, but a QObject: PySide invalidates it when it's deleted.
        if not isinstance(wrapper, QLayoutItem) or isinstance(wrapper, QObject):
            continue
        if Shiboken.ownedByPython(wrapper):
            continue  # Python's to delete, so deleting it unregisters it.
        match = _PARENT.search(Shiboken.dump(wrapper))
        parent = by_id.get(int(match.group(1), 16)) if match else None
        if isinstance(parent, QLayout) and any(
            layout.indexOf(wrapper) >= 0  # Compares addresses only.
            for layout in (parent, *parent.findChildren(QLayout))
        ):
            continue
        stale.append(Shiboken.dump(wrapper).splitlines()[1])
    return stale
