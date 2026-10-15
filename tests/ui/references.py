"""What in a window refers back to it.

A reference from inside a window back up to the window (an attribute, or a function or
bound method kept in one) makes a cycle, and a dropped window in a cycle is left to Python's
cycle collector, which deletes Qt widgets in no safe order and can crash (see
`follow_theme`). Signal connections don't count: Qt holds them, and they make no cycle the
collector sees.
"""

import gc
import types
from collections.abc import Iterator

from PySide6.QtCore import QObject

# Not followed: they lead to whole modules, never back to a particular window.
_SKIP = (type, types.ModuleType, types.FrameType, types.CodeType)


def references_to(window: QObject) -> list[str]:
    """Each object in ``window``'s tree whose own state leads back to ``window``, as
    "Class 'objectName'"."""
    return [
        f"{type(obj).__qualname__} {obj.objectName()!r}"
        for obj in window.findChildren(QObject)
        if any(target is window for target in _reached(obj))
    ]


def _reached(start: QObject) -> Iterator[QObject]:
    """The QObjects ``start``'s state refers to, without going through another QObject."""
    seen = {id(start)}
    stack: list[object] = list(gc.get_referents(start))
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, _SKIP):
            continue
        seen.add(id(obj))
        if isinstance(obj, QObject):
            yield obj
        elif isinstance(obj, types.FunctionType):  # Its closure and defaults, not globals.
            stack.extend(_contents(cell) for cell in obj.__closure__ or ())
            stack.extend(obj.__defaults__ or ())
        elif not (isinstance(obj, dict) and "__builtins__" in obj):  # Not module globals.
            stack.extend(gc.get_referents(obj))


def _contents(cell: types.CellType) -> object:
    try:
        return cell.cell_contents
    except ValueError:  # Not yet assigned.
        return None
