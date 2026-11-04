"""Stopped operations cannot start, publish late results, or leave a task running."""

import asyncio

import pytest

from mailbrief.infra.tasks import OperationStopped, guarded


async def test_plain_operation_returns_its_result() -> None:
    async def value() -> int:
        return 7

    assert await guarded(value) == 7


@pytest.mark.parametrize("permission", [False, True])
async def test_refusal_does_not_start_the_operation(permission: bool) -> None:
    cancel = asyncio.Event()
    cancel.set()
    called = False

    async def work() -> None:
        nonlocal called
        called = True

    async def allowed() -> bool:
        return permission

    with pytest.raises(OperationStopped):
        await guarded(work, allowed=allowed, cancel=cancel if permission else None)
    assert not called


@pytest.mark.parametrize("revoke", [False, True])
async def test_in_flight_stop_joins_the_operation(revoke: bool) -> None:
    entered, finished, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    permission = True

    async def work() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    async def allowed() -> bool:
        return permission

    task = asyncio.create_task(guarded(work, allowed=allowed if revoke else None, cancel=cancel))
    await entered.wait()
    if revoke:
        permission = False
    else:
        cancel.set()
    with pytest.raises(OperationStopped):
        await asyncio.wait_for(task, 1)
    assert finished.is_set()


async def test_external_cancellation_joins_both_tasks() -> None:
    entered, finished = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    task = asyncio.create_task(guarded(work, cancel=asyncio.Event()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_permission_failure_stops_work_instead_of_allowing_it() -> None:
    entered, finished = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    calls = 0

    async def allowed() -> bool:
        nonlocal calls
        calls += 1
        if calls > 1:
            await entered.wait()
            raise ValueError("synthetic unreadable permission")
        return True

    with pytest.raises(ValueError, match="synthetic unreadable permission"):
        await guarded(work, allowed=allowed)
    assert finished.is_set()


async def test_completed_work_is_rechecked_before_publication() -> None:
    permission = True

    async def allowed() -> bool:
        return permission

    async def work() -> int:
        nonlocal permission
        permission = False
        return 7

    with pytest.raises(OperationStopped):
        await guarded(work, allowed=allowed)
