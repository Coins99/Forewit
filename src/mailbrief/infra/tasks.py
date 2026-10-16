"""Run a group of coroutines as tasks and never leave one running behind."""

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterable
from contextlib import asynccontextmanager


@asynccontextmanager
async def running[T](
    work: Iterable[Coroutine[object, object, T]],
) -> AsyncIterator[list[asyncio.Task[T]]]:
    """Start each coroutine as a task. Leaving the block cancels those still running and
    waits for every one, so none outlives it, whether the block ends, fails or is cancelled."""
    tasks: list[asyncio.Task[T]] = []
    try:
        # Inside the try, so the tasks already started are cancelled if ``work`` fails.
        tasks.extend(asyncio.create_task(item) for item in work)
        yield tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def gather_or_cancel[T](work: Iterable[Coroutine[object, object, T]]) -> list[T]:
    """Run the coroutines together and return their results in order. The first failure, or
    cancelling the caller, cancels the rest before it is raised."""
    async with running(work) as tasks:
        return list(await asyncio.gather(*tasks))
