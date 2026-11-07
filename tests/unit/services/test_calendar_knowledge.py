"""An attempt, partial list or retained row never proves successful coverage."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.google_calendar import CalendarKnowledge
from mailbrief.ports.errors import ProviderError
from mailbrief.services.events import EventService
from mailbrief.services.google_calendar import CalendarSyncService, needs_sync
from mailbrief.storage.google_calendar import GoogleCalendarRepository
from tests.unit.services.test_google_calendar import (
    NOW,
    PRIMARY,
    TEAM,
    TODAY,
    TORONTO,
    FakeReader,
    google,
)


async def test_partial_then_complete_list_failure_and_clear(session: AsyncSession) -> None:
    reader = FakeReader()
    reader.events = {
        PRIMARY.calendar_id: (google(PRIMARY, "busy", NOW),),
        TEAM.calendar_id: ProviderError("Synthetic partial page"),
    }
    service = CalendarSyncService(session, reader, clock=lambda: NOW)
    await service.sync(TODAY, TORONTO)
    repository = GoogleCalendarRepository(session)
    partial = await repository.knowledge()
    assert partial.failed and await repository.coverage() is None
    checked = await repository.checked_coverage()
    assert not needs_sync(checked, TODAY, TODAY, NOW, TORONTO)
    reader.events[TEAM.calendar_id] = ()
    await service.sync(TODAY, TORONTO)
    complete = await repository.knowledge()
    assert complete.generation != partial.generation
    assert complete.status(TODAY, TODAY, TORONTO, NOW) == "fresh shown-calendar coverage"
    assert complete.status(TODAY, TODAY, TORONTO, NOW + timedelta(minutes=15)) == "stale"
    reader.list_fail = ProviderError("Synthetic list failure")
    with pytest.raises(ProviderError):
        await service.sync(TODAY, TORONTO)
    failed = await repository.knowledge()
    assert not failed.list_succeeded and failed.generation != complete.generation
    assert await repository.coverage() is None
    shown = await EventService(session).list_range(TODAY, TODAY, TORONTO)
    assert len(shown.google_events) == 1
    await service.clear()
    assert (await repository.knowledge()).generation != failed.generation
    assert (await repository.knowledge()).status(
        TODAY, TODAY, TORONTO, NOW
    ) == "no calendar connection"
    assert not (await EventService(session).list_range(TODAY, TODAY, TORONTO)).google_events


async def test_empty_success_and_wrong_zone_are_distinct(session: AsyncSession) -> None:
    from zoneinfo import ZoneInfo

    repository = GoogleCalendarRepository(session)
    assert await repository.knowledge() == CalendarKnowledge()
    await repository.replace_calendars((), NOW, TODAY, TODAY, TORONTO.key)
    await session.commit()
    knowledge = await repository.knowledge()
    assert knowledge.status(TODAY, TODAY, TORONTO, NOW) == "fresh shown-calendar coverage"
    assert (
        knowledge.status(TODAY, TODAY, ZoneInfo("UTC"), NOW)
        == "partial range or different time zone"
    )
    assert knowledge.status(TODAY, TODAY, TORONTO, NOW - timedelta(seconds=1)) == "stale"
    assert not needs_sync(await repository.checked_coverage(), TODAY, TODAY, NOW, TORONTO)
    assert needs_sync(await repository.checked_coverage(), TODAY, TODAY, NOW, ZoneInfo("UTC"))


async def test_old_attempt_metadata_is_unknown_success(session: AsyncSession) -> None:
    repository = GoogleCalendarRepository(session)
    rows = await repository.replace_calendars((PRIMARY,), NOW)
    await repository.mark_checked(rows.values(), TODAY, TODAY, NOW)
    await session.commit()
    assert await repository.checked_coverage() is not None
    assert await repository.coverage() is None
    assert (await repository.knowledge()).failed
