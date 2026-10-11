"""The owner's calendar events (ADR 0018); the service owns commits and rollbacks.

Events change only through ORM objects, never bulk statements, so rows already loaded in
the session always show what was last written.
"""

from datetime import date, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.events import CalendarEvent, EventEdit
from mailbrief.storage.tables import ActionTable, CalendarEventTable


def event_from_row(row: CalendarEventTable) -> CalendarEvent:
    """Rebuild a stored event; raises ValueError when the row no longer validates."""
    return CalendarEvent(
        public_id=row.public_id,
        title=row.title,
        notes=row.notes,
        all_day=row.all_day,
        starts_at_utc=row.starts_at_utc,
        ends_at_utc=row.ends_at_utc,
        start_date=row.start_date,
        last_date=row.last_date,
        time_zone=row.time_zone,
        revision=row.revision,
        created_at_utc=row.created_at_utc,
        updated_at_utc=row.updated_at_utc,
    )


def apply_edit(row: CalendarEventTable, edit: EventEdit) -> None:
    """Replace every field the owner edits with the edit's values."""
    row.title = edit.title
    row.notes = edit.notes
    row.all_day = edit.all_day
    row.starts_at_utc = edit.starts_at_utc
    row.ends_at_utc = edit.ends_at_utc
    row.start_date = edit.start_date
    row.last_date = edit.last_date
    row.time_zone = edit.time_zone


class EventRepository:
    """Read and add events, and read the open actions' dates beside them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, public_id: str, *, include_deleted: bool = False
    ) -> CalendarEventTable | None:
        query = select(CalendarEventTable).where(CalendarEventTable.public_id == public_id)
        if not include_deleted:
            query = query.where(CalendarEventTable.deleted_at_utc.is_(None))
        return (await self._session.scalars(query)).one_or_none()

    def add(self, row: CalendarEventTable) -> None:
        self._session.add(row)

    async def touching(
        self, start_utc: datetime, end_utc: datetime, first_day: date, last_day: date
    ) -> list[CalendarEventTable]:
        """Live events that touch the instants ``[start_utc, end_utc)`` or, all-day, the
        days ``first_day`` through ``last_day``."""
        result = await self._session.scalars(
            select(CalendarEventTable)
            .where(
                CalendarEventTable.deleted_at_utc.is_(None),
                or_(
                    and_(
                        CalendarEventTable.all_day.is_(False),
                        CalendarEventTable.starts_at_utc < end_utc,
                        CalendarEventTable.ends_at_utc > start_utc,
                    ),
                    and_(
                        CalendarEventTable.all_day.is_(True),
                        CalendarEventTable.start_date <= last_day,
                        CalendarEventTable.last_date >= first_day,
                    ),
                ),
            )
            .order_by(CalendarEventTable.id)
        )
        return list(result)

    async def dated_actions(
        self, start_utc: datetime, end_utc: datetime, first_day: date, last_day: date
    ) -> list[ActionTable]:
        """Live, open actions with a target date, a deadline day or a deadline instant in
        the range; the caller decides which of their dates to show."""
        result = await self._session.scalars(
            select(ActionTable)
            .where(
                ActionTable.deleted_at_utc.is_(None),
                ActionTable.status == ActionStatus.OPEN.value,
                or_(
                    ActionTable.target_date.between(first_day, last_day),
                    ActionTable.deadline_date.between(first_day, last_day),
                    and_(
                        ActionTable.deadline_at_utc >= start_utc,
                        ActionTable.deadline_at_utc < end_utc,
                    ),
                ),
            )
            .order_by(ActionTable.id)
        )
        return list(result)
