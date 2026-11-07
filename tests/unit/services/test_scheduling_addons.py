"""Cross-feature behavior on main's task/block/hours schema."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.events import EventEdit
from mailbrief.domain.work_blocks import BlockSaved, BlockTime
from mailbrief.domain.working_hours import DatedHours, HoursEdit, WorkingWindow
from mailbrief.services.availability import AvailabilityService
from mailbrief.services.data import export_writing
from mailbrief.services.events import EventService
from mailbrief.services.scheduling import SchedulingChangedError
from mailbrief.services.work_blocks import BlockConflictError, WorkBlockService
from mailbrief.storage.database import Database
from mailbrief.storage.tables import ActionTable, BlockCommandTable, WorkBlockTable
from tests.unit.services.test_scheduling import DAY, NOW, START, ZONE, acknowledged, request, setup


async def test_hours_overrides_buffers_and_free_events_feed_the_same_planner(
    session: AsyncSession,
) -> None:
    service, task = await setup(session)
    hours = await AvailabilityService(session).hours()
    edit = hours.edit().model_copy(
        update={
            "buffer_before_minutes": 15,
            "buffer_after_minutes": 30,
            "overrides": (
                DatedHours(day=DAY, windows=(WorkingWindow(start_minute=600, end_minute=900),)),
            ),
        }
    )
    await AvailabilityService(session).save_hours(edit, hours.revision)
    for busy, start in ((True, 11), (False, 13)):
        await EventService(session).create(
            EventEdit(
                title="Synthetic commitment",
                all_day=False,
                busy=busy,
                starts_at_utc=START.replace(hour=start),
                ends_at_utc=START.replace(hour=start + 1),
                time_zone="UTC",
            )
        )
    shown = await EventService(session).list_range(DAY, DAY, ZONE)
    from mailbrief.domain.availability import availability

    free = availability(shown, shown.hours, ZONE)[0]
    assert free.working_minutes == 300 and free.busy_minutes == 105
    proposal = await service.suggest(request(task))
    assert proposal.blocks[0].starts_at_utc.hour == 12
    assert proposal.blocks[0].starts_at_utc.minute == 30
    assert (await service.workload(request(task))).free_minutes == free.free_minutes


async def test_locked_blocks_refuse_move_unschedule_and_batch_undo(session: AsyncSession) -> None:
    service, task = await setup(session)
    saved = await acknowledged(service, task)
    assert saved.undo is not None
    blocks = WorkBlockService(session)
    locked = await blocks.set_locked(saved.blocks[0].public_id, 1, True)
    with pytest.raises(BlockConflictError, match="Unlock"):
        await blocks.move(locked.block.public_id, 2, locked.block.time)
    with pytest.raises(BlockConflictError, match="Unlock"):
        await blocks.unschedule(locked.block.public_id, 2)
    with pytest.raises(SchedulingChangedError):
        await service.undo(saved.undo, str(uuid4()))
    unlocked = await blocks.set_locked(locked.block.public_id, 2, False)
    assert unlocked.block.revision == 3 and not unlocked.block.locked
    await blocks.unschedule(unlocked.block.public_id, 3)


@pytest.mark.parametrize("operation", ["move", "undo_move", "unschedule", "restore", "lock"])
async def test_manual_commands_replay_without_extra_revisions(
    session: AsyncSession, operation: str
) -> None:
    _, task = await setup(session)
    blocks = WorkBlockService(session, clock=lambda: NOW)
    time = BlockTime(starts_at_utc=START, ends_at_utc=START + timedelta(hours=1), time_zone="UTC")
    first_key = str(uuid4())
    created = await blocks.command(first_key, "schedule", task, time=time)
    assert isinstance(created, BlockSaved)
    assert (await blocks.command(first_key, "schedule", task, time=time)) == created
    block = created.block
    if operation == "restore":
        await blocks.unschedule(block.public_id, block.revision)
    key = str(uuid4())
    revision = None if operation == "restore" else block.revision
    time_arg = time if operation in ("move", "undo_move") else None
    lock_arg = True if operation == "lock" else None
    saved = await blocks.command(
        key, operation, block.public_id, revision=revision, time=time_arg, locked=lock_arg
    )
    replay = await blocks.command(
        key, operation, block.public_id, revision=revision, time=time_arg, locked=lock_arg
    )
    assert saved == replay
    with pytest.raises(BlockConflictError, match="different input"):
        await blocks.command(key, "schedule", task, time=time)
    row = (await session.scalars(select(WorkBlockTable))).one()
    row.revision += 1
    await session.commit()
    with pytest.raises(BlockConflictError, match="changed or was removed"):
        await blocks.command(
            key, operation, block.public_id, revision=revision, time=time_arg, locked=lock_arg
        )


async def test_manual_receipts_rollback_and_survive_purge_without_resurrection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, task = await setup(session)
    blocks = WorkBlockService(session)
    time = BlockTime(starts_at_utc=START, ends_at_utc=START + timedelta(hours=1), time_zone="UTC")
    key = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic failure")))
        with pytest.raises(RuntimeError):
            await blocks.command(key, "schedule", task, time=time)
    assert not list((await session.scalars(select(BlockCommandTable))).all())
    await session.rollback()
    await blocks.command(key, "schedule", task, time=time)
    row = (await session.scalars(select(ActionTable).where(ActionTable.public_id == task))).one()
    await session.delete(row)
    await session.commit()
    with pytest.raises(BlockConflictError, match="removed"):
        await blocks.command(key, "schedule", task, time=time)
    with pytest.raises(ValueError):
        await blocks.command(str(uuid4()), "invalid", task)


async def test_export_keeps_hours_and_locks_but_omits_command_receipts(
    session: AsyncSession, tmp_path: Path, database: Database
) -> None:
    _, task = await setup(session)
    saved = await WorkBlockService(session).command(
        str(uuid4()),
        "schedule",
        task,
        time=BlockTime(
            starts_at_utc=START, ends_at_utc=START + timedelta(hours=1), time_zone="UTC"
        ),
    )
    assert isinstance(saved, BlockSaved)
    await WorkBlockService(session).set_locked(saved.block.public_id, 1, True)
    destination = tmp_path / "writing.json"
    await export_writing(database, destination)
    data = json.loads(destination.read_text())
    assert "block_commands" not in data["records"] and "planning_commands" not in data["records"]
    assert data["records"]["work_blocks"][0]["locked"] is True


@pytest.mark.parametrize("value", [(-1, 0), (0, 121)])
def test_buffer_limits(value: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        HoursEdit(buffer_before_minutes=value[0], buffer_after_minutes=value[1])


def test_dated_windows_share_weekly_validation() -> None:
    with pytest.raises(ValueError):
        DatedHours(
            day=DAY,
            windows=(
                WorkingWindow(start_minute=600, end_minute=720),
                WorkingWindow(start_minute=660, end_minute=780),
            ),
        )
    with pytest.raises(ValueError):
        HoursEdit(overrides=(DatedHours(day=DAY), DatedHours(day=DAY)))
