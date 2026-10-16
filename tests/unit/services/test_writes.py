"""commit_once: the clock read once, one commit, a rollback on any failure."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.services.writes import commit_once
from mailbrief.storage.database import Database
from mailbrief.storage.tables import AccountTable
from tests.factories import Clock

# 09:00 in UTC+2: the operation is handed 07:00 UTC.
LOCAL = datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=2)))


def account(name: str, created: datetime) -> AccountTable:
    return AccountTable(
        provider="gmail",
        provider_account_id=name,
        email_address=f"{name}@example.com",
        created_at_utc=created,
    )


async def accounts(database: Database) -> int:
    async with database.session() as other:
        return await other.scalar(select(func.count()).select_from(AccountTable)) or 0


async def test_it_reads_the_clock_once_as_utc_and_commits(
    session: AsyncSession, database: Database
) -> None:
    seen: list[datetime] = []

    async def operation(now: datetime) -> str:
        seen.append(now)
        session.add(account("first", now))
        return "done"

    assert await commit_once(session, Clock(LOCAL), operation) == "done"
    assert seen == [datetime(2026, 9, 1, 7, 0, tzinfo=UTC)]
    assert seen[0].tzinfo is UTC
    assert await accounts(database) == 1  # Committed: another session sees it.


async def test_a_failure_rolls_back_and_is_raised(
    session: AsyncSession, database: Database
) -> None:
    async def operation(now: datetime) -> None:
        session.add(account("refused", now))
        await session.flush()
        raise LookupError("stale")

    with pytest.raises(LookupError, match="stale"):
        await commit_once(session, Clock(LOCAL), operation)
    assert await accounts(database) == 0
    assert not session.in_transaction()  # Rolled back, nothing left pending.


async def test_cancellation_rolls_back_too(session: AsyncSession, database: Database) -> None:
    async def operation(now: datetime) -> None:
        session.add(account("cancelled", now))
        await session.flush()
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await commit_once(session, Clock(LOCAL), operation)
    assert await accounts(database) == 0
    assert not session.in_transaction()


async def test_a_naive_clock_is_refused_before_anything_runs(session: AsyncSession) -> None:
    async def operation(now: datetime) -> None:
        raise AssertionError("must not run")

    with pytest.raises(ValueError, match="time zone"):
        await commit_once(session, Clock(datetime(2026, 9, 1, 9, 0)), operation)
