"""The owner's working hours row (ADR 0029); the service owns commits and rollbacks.

Rows change only through ORM objects.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from mailbrief.domain.working_hours import HoursEdit, WorkingHours, WorkingWindow
from mailbrief.storage.tables import WorkingHoursTable

logger = logging.getLogger(__name__)

HOURS_ROW_ID = 1
_INVALID = "Saved working hours are invalid."


class WorkingHoursRepository:
    """Read and add the single working hours row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_row(self) -> WorkingHoursTable | None:
        """The row freshly read, or None when never saved."""
        result = await self._session.execute(
            select(WorkingHoursTable)
            .where(WorkingHoursTable.id == HOURS_ROW_ID)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_row_to_replace(self) -> WorkingHoursTable | None:
        """The row with only its identity and revision read, so unreadable hours never
        stop the owner from saving new ones."""
        result = await self._session.execute(
            select(WorkingHoursTable)
            .where(WorkingHoursTable.id == HOURS_ROW_ID)
            .options(load_only(WorkingHoursTable.id, WorkingHoursTable.revision))
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    def add(self, row: WorkingHoursTable) -> None:
        self._session.add(row)

    async def read(self) -> WorkingHours:
        """The saved hours, or none set. Hours that can't be read count as none, with the
        row's revision so that saving replaces them: they are only shown and never decide
        what is sent anywhere, so they never fail a read of the calendar."""
        try:
            return hours_from_row(await self.get_row())
        except ValueError:
            logger.warning("Saved working hours could not be read")
            row = await self.get_row_to_replace()
            return WorkingHours(revision=0 if row is None else row.revision)


def _window(pair: object) -> WorkingWindow:
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or not all(type(minute) is int for minute in pair)
    ):
        raise ValueError(_INVALID)
    return WorkingWindow(start_minute=pair[0], end_minute=pair[1])


def hours_from_row(row: WorkingHoursTable | None) -> WorkingHours:
    """The domain working hours; None gives hours never set.

    Raises ValueError with a static message when the row no longer validates.
    """
    if row is None:
        return WorkingHours.unset()
    try:
        days = row.windows_json
        if not isinstance(days, list):
            raise ValueError(_INVALID)
        week: list[tuple[WorkingWindow, ...]] = []
        for windows in days:
            if not isinstance(windows, list):
                raise ValueError(_INVALID)
            week.append(tuple(_window(pair) for pair in windows))
        return WorkingHours(
            days=tuple(week),
            buffer_minutes=row.buffer_minutes,
            revision=row.revision,
            updated_at_utc=row.updated_at_utc,
        )
    except ValueError:
        raise ValueError(_INVALID) from None


def apply_hours(row: WorkingHoursTable, edit: HoursEdit) -> None:
    """Replace the week and the buffer with the edit's."""
    row.windows_json = [
        [[window.start_minute, window.end_minute] for window in windows] for windows in edit.days
    ]
    row.buffer_minutes = edit.buffer_minutes
