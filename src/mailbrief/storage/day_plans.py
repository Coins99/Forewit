"""Day-plan ORM access, collision-free ordering and purge participation."""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.events import FocusDay, FocusTask
from mailbrief.storage.tables import ActionTable, DayPlanItemTable, DayPlanTable


async def plan_row(session: AsyncSession, day: date) -> DayPlanTable | None:
    return (
        await session.scalars(
            select(DayPlanTable)
            .where(DayPlanTable.local_date == day)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def item_rows(session: AsyncSession, plan_id: int) -> list[DayPlanItemTable]:
    return list(
        await session.scalars(
            select(DayPlanItemTable)
            .where(DayPlanItemTable.plan_id == plan_id)
            .order_by(DayPlanItemTable.position)
            .execution_options(populate_existing=True)
        )
    )


async def order_items(
    session: AsyncSession, items: Sequence[DayPlanItemTable], now: datetime
) -> None:
    """Two flushes avoid SQLite's immediate unique(plan,position) collisions."""
    offset = max((item.position for item in items), default=0) + 1
    for position, item in enumerate(items):
        item.position = offset + position
        item.updated_at_utc = now
    await session.flush()
    for position, item in enumerate(items):
        item.position = position
    await session.flush()


async def remove_task_references(
    session: AsyncSession,
    task_ids: Sequence[str],
    now: datetime,
    changed_plans: set[int] | None = None,
) -> None:
    """Called inside permanent owner-record deletion, never committing independently."""
    if not task_ids:
        return
    changed_plans = set() if changed_plans is None else changed_plans
    references = list(
        await session.scalars(
            select(DayPlanItemTable).where(DayPlanItemTable.task_public_id.in_(task_ids))
        )
    )
    for plan_id in sorted({item.plan_id for item in references}):
        row = await session.get(DayPlanTable, plan_id, populate_existing=True)
        assert row is not None
        if plan_id not in changed_plans:
            row.revision += 1
            row.updated_at_utc = now
            await session.flush([row])
            changed_plans.add(plan_id)
        for item in references:
            if item.plan_id == plan_id:
                await session.delete(item)
        await session.flush()
        await order_items(session, await item_rows(session, plan_id), now)


async def focus_days(
    session: AsyncSession, first_day: date, last_day: date
) -> tuple[FocusDay, ...]:
    """The days from ``first_day`` through ``last_day`` with focus tasks the owner does, each
    with its live, open ones in their order and their estimates (ADR 0030). A day plan holds
    at most MAX_DAY_ITEMS, so the read is bounded by the range."""
    rows = await session.execute(
        select(DayPlanTable.local_date, ActionTable.public_id, ActionTable.estimate_minutes)
        .join(DayPlanItemTable, DayPlanItemTable.plan_id == DayPlanTable.id)
        .join(ActionTable, ActionTable.public_id == DayPlanItemTable.task_public_id)
        .where(
            DayPlanTable.local_date >= first_day,
            DayPlanTable.local_date <= last_day,
            ActionTable.deleted_at_utc.is_(None),
            ActionTable.status == ActionStatus.OPEN.value,
            ActionTable.ownership == ActionOwnership.MINE.value,
        )
        .order_by(DayPlanTable.local_date, DayPlanItemTable.position)
    )
    days: dict[date, list[FocusTask]] = {}
    for day, public_id, estimate in rows.tuples():
        days.setdefault(day, []).append(
            FocusTask(action_public_id=public_id, estimate_minutes=estimate)
        )
    return tuple(FocusDay(day=day, tasks=tuple(tasks)) for day, tasks in days.items())
