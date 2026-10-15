import gc

import pytest

from mailbrief.ui.weak import weak_method


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def tick(self, by: int, *, twice: bool = False) -> int:
        self.calls += by * (2 if twice else 1)
        return self.calls


def test_a_weak_method_calls_through_to_its_object() -> None:
    clock = _Clock()
    tick = weak_method(clock.tick)
    assert tick(2) == 2
    assert tick(1, twice=True) == 4


def test_a_weak_method_keeps_nothing_alive_and_refuses_once_its_object_is_gone() -> None:
    clock = _Clock()
    tick = weak_method(clock.tick)
    del clock
    gc.collect()
    with pytest.raises(ReferenceError):
        tick(1)
