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
# added under, which holds the item or has a layout inside it that does. Its repr's address
# is upper-case hex on Windows (0x0000022865B70F10) and lower-case on macOS.
_PARENT_LINE = re.compile(r"^parent\.+ (.*)$", re.MULTILINE)
_ADDRESS = re.compile(r"^<.* at (0x[0-9A-Fa-f]+)>$")


def parent_id(dump: str) -> int | None:
    """The ``id`` of the parent wrapper ``Shiboken.dump`` names, or None without one."""
    line = _PARENT_LINE.search(dump)
    if line is None:
        return None
    address = _ADDRESS.match(line.group(1).strip())
    # Unread, every item would look stale: say so instead.
    assert address is not None, f"Unrecognized Shiboken.dump parent: {line.group(1)!r}"
    return int(address.group(1), 16)


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
        found = parent_id(Shiboken.dump(wrapper))
        parent = by_id.get(found) if found is not None else None
        if isinstance(parent, QLayout) and any(
            layout.indexOf(wrapper) >= 0  # Compares addresses only.
            for layout in (parent, *parent.findChildren(QLayout))
        ):
            continue
        stale.append(Shiboken.dump(wrapper).splitlines()[1])
    return stale
