"""Tasks started together never outlive the call that started them."""

import asyncio

import pytest

from mailbrief.infra.tasks import gather_or_cancel, running


async def test_results_come_back_in_order() -> None:
    async def value(number: int, delay: float) -> int:
        await asyncio.sleep(delay)
        return number

    assert await gather_or_cancel(value(n, d) for n, d in ((1, 0.02), (2, 0), (3, 0.01))) == [
        1,
        2,
        3,
    ]
    assert await gather_or_cancel([]) == []


async def test_the_first_failure_cancels_the_rest() -> None:
    cancelled: list[str] = []

    async def slow() -> int:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append("slow")
            raise
        return 0

    async def fails() -> int:
        await asyncio.sleep(0)
        raise LookupError("first")

    with pytest.raises(LookupError, match="first"):
        await gather_or_cancel([slow(), fails()])
    assert cancelled == ["slow"]  # Cancelled and awaited before the error was raised.


async def test_cancelling_the_caller_cancels_every_task() -> None:
    started = asyncio.Event()
    cancelled: list[int] = []

    async def wait(number: int) -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(number)
            raise

    caller = asyncio.create_task(gather_or_cancel(wait(n) for n in range(3)))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert sorted(cancelled) == [0, 1, 2]


async def test_leaving_the_block_early_cancels_what_is_still_running() -> None:
    async def value(number: int, delay: float) -> int:
        await asyncio.sleep(delay)
        return number

    first = None
    async with running(value(n, d) for n, d in ((1, 0), (2, 10))) as tasks:
        for finished in asyncio.as_completed(tasks):
            first = await finished
            break
    assert first == 1
    assert all(task.done() for task in tasks)
    assert tasks[1].cancelled()
