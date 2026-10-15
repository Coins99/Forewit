"""Callbacks a window's widgets keep without referring back up to the window."""

import weakref
from collections.abc import Callable


def weak_method[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Call the bound ``method`` through a weak reference to its object.

    A widget that keeps a bound method of its window (a clock, a callback) refers back up
    to the window. That makes a cycle, and a dropped window in a cycle is left to Python's
    cycle collector, which deletes Qt widgets in no safe order and can crash (see
    `follow_theme`). Signal connections don't need this: Qt holds them. Calling the result
    once the object is gone raises ReferenceError.
    """
    ref = weakref.WeakMethod(method)

    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = ref()
        if bound is None:
            raise ReferenceError("the method's object is gone")
        return bound(*args, **kwargs)

    return call
