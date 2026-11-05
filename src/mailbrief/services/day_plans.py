"""Revisioned daily selection; no task dates, priorities or lifecycle are written here."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.actions import Action, ActionStatus
from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.day_plans import MAX_DAY_ITEMS, DayPlan, DayPlanItem, PlanTransfer
from mailbrief.domain.search import query_terms
from mailbrief.services.writes import commit_once
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.day_plans import item_rows, order_items, plan_row
from mailbrief.storage.tables import ActionTable, DayPlanItemTable, DayPlanTable

_STALE = "Your day plan changed. Reload it before making another change."


class DayPlanConflictError(ValueError):
    """A stale plan, missing reference or invalid selection; messages contain no owner text."""


class DayPlanService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = new_public_id,
    ) -> None:
        self.session = session
        self.clock = clock
        self.ids = id_factory
        self.actions = ActionRepository(session)

    async def load(self, day: date) -> DayPlan:
        row = await plan_row(self.session, day)
        if row is None:
            return DayPlan(day=day)
        items = await item_rows(self.session, row.id)
        task_rows = (
            list(
                await self.session.scalars(
                    select(ActionTable)
                    .where(
                        ActionTable.public_id.in_([item.task_public_id for item in items]),
                        ActionTable.deleted_at_utc.is_(None),
                    )
                    .execution_options(populate_existing=True)
                )
            )
            if items
            else []
        )
        tasks = {action.public_id: action for action in await self.actions.load(task_rows)}
        return DayPlan(
            day=row.local_date,
            public_id=row.public_id,
            creation_zone=row.creation_zone,
            revision=row.revision,
            created_at_utc=row.created_at_utc,
            updated_at_utc=row.updated_at_utc,
            items=tuple(
                DayPlanItem(
                    public_id=item.public_id,
                    task_public_id=item.task_public_id,
                    position=item.position,
                    action=tasks.get(item.task_public_id),
                )
                for item in items
            ),
        )

    async def candidates(self, text: str = "", *, day: date | None = None) -> tuple[Action, ...]:
        terms = query_terms(text)
        if text.strip() and not terms:
            return ()
        selected = (
            None
            if day is None
            else select(DayPlanItemTable.task_public_id)
            .join(DayPlanTable, DayPlanItemTable.plan_id == DayPlanTable.id)
            .where(DayPlanTable.local_date == day)
        )
        rows = await self.actions.title_matches(terms, open_only=True, excluded=selected)
        return tuple(await self.actions.load(rows))

    async def preview_transfer(
        self, source: date, destination: date, *, move: bool = False
    ) -> PlanTransfer:
        if source == destination:
            raise DayPlanConflictError("Choose two different plan dates.")
        before, after = await self.load(source), await self.load(destination)
        eligible = tuple(
            item.task_public_id
            for item in before.items
            if item.action is not None and item.action.status is ActionStatus.OPEN
        )
        return PlanTransfer(
            source=before,
            destination=after,
            move=move,
            task_ids=eligible,
            skipped_ids=tuple(
                item.task_public_id for item in before.items if item.task_public_id not in eligible
            ),
        )

    async def transfer(self, preview: PlanTransfer, zone: ZoneInfo) -> DayPlan:
        """Apply a whole preview or nothing, preserving task dates and destination order."""

        async def run(now: datetime) -> DayPlan:
            if preview.source.day == preview.destination.day:
                raise DayPlanConflictError("Choose two different plan dates.")
            source = await self._checked(preview.source.day, preview.source.revision)
            destination = await self._checked(preview.destination.day, preview.destination.revision)
            current = await self.load(preview.source.day)
            if current != preview.source:
                raise DayPlanConflictError("The preview changed. Review it again.")
            eligible = tuple(
                item.task_public_id
                for item in current.items
                if item.action is not None and item.action.status is ActionStatus.OPEN
            )
            if preview.task_ids != eligible:
                raise DayPlanConflictError("The preview changed. Review it again.")
            source_items = [] if source is None else await item_rows(self.session, source.id)
            destination_items = (
                [] if destination is None else await item_rows(self.session, destination.id)
            )
            existing = {item.task_public_id for item in destination_items}
            additions = [key for key in eligible if key not in existing]
            if len(destination_items) + len(additions) > MAX_DAY_ITEMS:
                raise DayPlanConflictError("Choose at most 100 tasks for one day.")
            if additions:
                if destination is None:
                    destination = DayPlanTable(
                        public_id=self.ids(),
                        local_date=preview.destination.day,
                        creation_zone=zone.key,
                        revision=1,
                        created_at_utc=now,
                        updated_at_utc=now,
                    )
                    self.session.add(destination)
                    await self.session.flush()
                else:
                    await self._touch(destination, now)
                for position, key in enumerate(additions, len(destination_items)):
                    self.session.add(
                        DayPlanItemTable(
                            public_id=self.ids(),
                            plan_id=destination.id,
                            task_public_id=key,
                            position=position,
                            created_at_utc=now,
                            updated_at_utc=now,
                        )
                    )
            if preview.move and eligible:
                assert source is not None
                await self._touch(source, now)
            # After the first plan write holds SQLite's writer lock, recheck each
            # eligible occurrence; task edits while the preview was open invalidate it.
            for selected_item in current.items:
                if selected_item.task_public_id not in eligible:
                    continue
                task = await self.session.scalar(
                    select(ActionTable)
                    .where(ActionTable.public_id == selected_item.task_public_id)
                    .execution_options(populate_existing=True)
                )
                if (
                    task is None
                    or task.deleted_at_utc is not None
                    or task.status != "open"
                    or selected_item.action is None
                    or task.revision != selected_item.action.revision
                ):
                    raise DayPlanConflictError("The tasks changed. Review the preview again.")
            if preview.move and eligible:
                kept = []
                for item in source_items:
                    if item.task_public_id in eligible:
                        await self.session.delete(item)
                    else:
                        kept.append(item)
                await self.session.flush()
                await order_items(self.session, kept, now)
            await self.session.flush()
            return await self.load(preview.destination.day)

        return await self._write(run)

    async def _write(self, operation: Callable[[datetime], Awaitable[DayPlan]]) -> DayPlan:
        try:
            return await commit_once(self.session, self.clock, operation)
        except StaleDataError:
            raise DayPlanConflictError(_STALE) from None
        except IntegrityError as exc:
            if "day_plans.local_date" in str(exc.orig):
                raise DayPlanConflictError(_STALE) from None
            raise

    async def _checked(self, day: date, revision: int) -> DayPlanTable | None:
        row = await plan_row(self.session, day)
        if (0 if row is None else row.revision) != revision:
            raise DayPlanConflictError(_STALE)
        return row

    async def _touch(self, row: DayPlanTable, now: datetime) -> None:
        row.revision += 1
        row.updated_at_utc = now
        # Acquire/check the plan's write revision before changing ordered membership.
        await self.session.flush([row])

    async def select(
        self, day: date, zone: ZoneInfo, task_id: str, expected_revision: int
    ) -> DayPlan:
        async def run(now: datetime) -> DayPlan:
            row = await self._checked(day, expected_revision)
            items = [] if row is None else await item_rows(self.session, row.id)
            if any(item.task_public_id == task_id for item in items):
                return await self.load(day)
            if len(items) >= MAX_DAY_ITEMS:
                raise DayPlanConflictError("Choose at most 100 tasks for one day.")
            if row is None:
                row = DayPlanTable(
                    public_id=self.ids(),
                    local_date=day,
                    creation_zone=zone.key,
                    revision=1,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
                self.session.add(row)
                await self.session.flush()
            else:
                await self._touch(row, now)
            # The plan write now holds SQLite's writer lock; eligibility cannot change
            # between this fresh read and the committed membership.
            task = (
                await self.session.scalars(
                    select(ActionTable)
                    .where(ActionTable.public_id == task_id)
                    .execution_options(populate_existing=True)
                )
            ).one_or_none()
            if task is None or task.deleted_at_utc is not None or task.status != "open":
                raise DayPlanConflictError("Choose an available, open task for My focus.")
            self.session.add(
                DayPlanItemTable(
                    public_id=self.ids(),
                    plan_id=row.id,
                    task_public_id=task.public_id,
                    position=len(items),
                    created_at_utc=now,
                    updated_at_utc=now,
                )
            )
            await self.session.flush()
            return await self.load(day)

        return await self._write(run)

    async def deselect(self, day: date, item_id: str, expected_revision: int) -> DayPlan:
        async def run(now: datetime) -> DayPlan:
            row = await self._checked(day, expected_revision)
            items = [] if row is None else await item_rows(self.session, row.id)
            chosen = next((item for item in items if item.public_id == item_id), None)
            if chosen is None:
                return await self.load(day)
            assert row is not None
            await self._touch(row, now)
            await self.session.delete(chosen)
            await self.session.flush()
            await order_items(self.session, [item for item in items if item is not chosen], now)
            return await self.load(day)

        return await self._write(run)

    async def reorder(self, day: date, item_ids: Sequence[str], expected_revision: int) -> DayPlan:
        async def run(now: datetime) -> DayPlan:
            row = await self._checked(day, expected_revision)
            items = [] if row is None else await item_rows(self.session, row.id)
            by_id = {item.public_id: item for item in items}
            if len(item_ids) != len(items) or set(item_ids) != set(by_id):
                raise DayPlanConflictError("Reorder all of the current day-plan items once.")
            if list(item_ids) == [item.public_id for item in items]:
                return await self.load(day)
            assert row is not None
            await self._touch(row, now)
            await order_items(self.session, [by_id[key] for key in item_ids], now)
            return await self.load(day)

        return await self._write(run)
