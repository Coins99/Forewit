"""Run a group of coroutines as tasks and never leave one running behind."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
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


async def joined_thread[**P, T](operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """A thread cannot be cancelled; join it before releasing its caller's resources."""
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await asyncio.gather(worker, return_exceptions=True)
        raise


class OperationStopped(Exception):
    """Cancellation or a withdrawn permission stopped an operation; contains no input."""


async def guarded[T](
    operation: Callable[[], Coroutine[object, object, T]],
    *,
    allowed: Callable[[], Awaitable[bool]] | None = None,
    cancel: asyncio.Event | None = None,
) -> T:
    """Check permission before starting, stop in flight, and join both tasks on exit.

    Permission checks must use a resource not concurrently used by ``operation``.
    The operation is a factory so refusing it creates no unawaited coroutine. A
    completed result is checked again before it can be published.
    """
    if (cancel is not None and cancel.is_set()) or (allowed is not None and not await allowed()):
        raise OperationStopped()
    if allowed is None and cancel is None:
        return await operation()

    async def stopped() -> None:
        if allowed is None:
            assert cancel is not None
            await cancel.wait()
            return
        while await allowed():
            if cancel is None:
                await asyncio.sleep(0.25)
            else:
                try:
                    await asyncio.wait_for(cancel.wait(), 0.25)
                    return
                except TimeoutError:
                    pass

    work = asyncio.create_task(operation())
    watch = asyncio.create_task(stopped())
    try:
        done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            await watch  # Propagate an unreadable permission instead of silently allowing.
            raise OperationStopped()
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
        result = await work
        if (cancel is not None and cancel.is_set()) or (
            allowed is not None and not await allowed()
        ):
            raise OperationStopped()
        return result
    finally:
        work.cancel()
        watch.cancel()
        await asyncio.gather(work, watch, return_exceptions=True)
