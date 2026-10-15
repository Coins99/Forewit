"""One write of the owner's records: the clock read once, one commit, a rollback on failure."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import normalize_utc


async def commit_once[T](
    session: AsyncSession,
    clock: Callable[[], datetime],
    operation: Callable[[datetime], Awaitable[T]],
) -> T:
    """Run ``operation`` with the time read once from ``clock`` (aware, as UTC), then commit.

    Any failure rolls the session back, cancellation included, so an interrupted or refused
    change leaves nothing half done.
    """
    now = normalize_utc(clock())
    try:
        result = await operation(now)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return result
