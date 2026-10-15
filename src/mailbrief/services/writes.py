"""One write: one commit, a rollback on any failure, and for owner records the clock read once."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import normalize_utc


async def committed[T](session: AsyncSession, change: Awaitable[T]) -> T:
    """Await ``change``, then commit. Any failure rolls the session back, cancellation
    included, so an interrupted or refused change leaves nothing half done."""
    try:
        result = await change
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return result


async def commit_once[T](
    session: AsyncSession,
    clock: Callable[[], datetime],
    operation: Callable[[datetime], Awaitable[T]],
) -> T:
    """Run ``operation`` with the time read once from ``clock`` (aware, as UTC), then commit
    it as ``committed`` does."""
    return await committed(session, operation(normalize_utc(clock())))
