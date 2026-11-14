"""Deterministic policy, duration conservation and independent verification boundaries."""

from datetime import UTC, date, datetime, timedelta

import pytest

from mailbrief.domain.planning_intervals import UtcInterval
from mailbrief.domain.scheduling import (
    ProposedBlock,
    SchedulingProposal,
    SchedulingRequest,
    SchedulingSnapshot,
    SchedulingTask,
    TaskDemand,
    propose,
    verify_proposal,
    workload,
)
from mailbrief.domain.working_hours import WorkingHours

DAY = date(2026, 11, 9)
START = datetime(2026, 11, 9, 9, tzinfo=UTC)

NOW = START.replace(hour=8)


def task_id(index: int) -> str:
    return f"{index:08d}-0000-4000-8000-000000000001"


def span(start: float, end: float) -> UtcInterval:
    return UtcInterval(start=NOW + timedelta(hours=start), end=NOW + timedelta(hours=end))


def snapshot(
    demands: tuple[TaskDemand, ...], working: tuple[UtcInterval, ...] | None = (span(1, 9),)
) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        fingerprint="a" * 64,
        captured_at_utc=NOW,
        owner_zone="UTC",
        profile=WorkingHours.unset(),
        working=working,
        busy=(),
        reserved=(),
        blocks=(),
        demands=demands,
        conflicts=(),
        knowledge="never successfully read",
    )


def demand(index: int = 1, minutes: int | None = 60, **changes: object) -> TaskDemand:
    return TaskDemand.model_validate(
        {
            "task_public_id": task_id(index),
            "task_revision": 1,
            "title": f"Task {index}",
            "requested_minutes": minutes,
        }
        | changes
    )


def request(*choices: SchedulingTask) -> SchedulingRequest:
    return SchedulingRequest(first_day=DAY, last_day=DAY, tasks=choices)


def test_priority_deadline_focus_and_id_order_is_stable() -> None:
    demands = (
        demand(1, priority_rank=3),
        demand(2, priority_rank=0, focus_position=1),
        demand(3, priority_rank=0, deadline_utc=NOW + timedelta(hours=8)),
        demand(4, priority_rank=0, focus_position=0),
    )
    choices = tuple(SchedulingTask(task_public_id=item.task_public_id) for item in demands)
    result = propose(snapshot(demands), request(*choices))
    assert [block.task_public_id for block in result.blocks] == [
        task_id(3),
        task_id(4),
        task_id(2),
        task_id(1),
    ]
    assert (
        propose(snapshot(tuple(reversed(demands))), request(*reversed(choices))).blocks
        == result.blocks
    )


@pytest.mark.parametrize("allow,expected", [(False, 0), (True, 2)])
def test_small_remainder_is_explicit(allow: bool, expected: int) -> None:
    state = snapshot((demand(minutes=20),), (span(1, 1.25), span(2, 2 + 5 / 60)))
    choice = SchedulingTask(task_public_id=task_id(1), split=True, allow_small_remainder=allow)
    result = propose(state, request(choice))
    assert len(result.blocks) == expected


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"eligible": False}, "eligible"),
        ({"needs_repair": True}, "repair"),
        ({"requested_minutes": None}, "unknown"),
        ({"deadline_utc": NOW}, "Not placed"),
    ],
)
def test_unplaced_reasons_do_not_invent_work(changes: dict[str, object], reason: str) -> None:
    result = propose(
        snapshot((TaskDemand.model_validate(demand().model_dump() | changes),)),
        request(SchedulingTask(task_public_id=task_id(1))),
    )
    assert not result.blocks and reason in result.unplaced[0].reason


def test_covered_work_and_unconfigured_hours_are_distinct() -> None:
    choice = SchedulingTask(task_public_id=task_id(1))
    result = propose(snapshot((demand(credited_seconds=3600),)), request(choice))
    assert not result.blocks and not result.unplaced
    unknown = snapshot((demand(),), None)
    result = propose(unknown, request(choice))
    assert result.unplaced[0].reason == "Working hours not set"
    assert workload(unknown).free_minutes is None
    assert workload(snapshot((demand(),), ())).free_minutes == 0


def test_proposal_limit_does_not_publish_half_a_task() -> None:
    # 100 one-hour tasks, each requiring four 15-minute fragments, exceed 200 rows.
    windows = tuple(
        UtcInterval(
            start=NOW + timedelta(minutes=index * 30), end=NOW + timedelta(minutes=index * 30 + 15)
        )
        for index in range(400)
    )
    demands = tuple(demand(index) for index in range(100))
    result = propose(
        snapshot(demands, windows),
        request(
            *(SchedulingTask(task_public_id=item.task_public_id, split=True) for item in demands)
        ).model_copy(update={"last_day": DAY + timedelta(days=9)}),
    )
    assert len(result.blocks) == 200 and len(result.unplaced) == 50
    assert all(item.reason == "Proposal limit reached" for item in result.unplaced)


@pytest.mark.parametrize(
    "kind", ["locked", "early", "busy", "duration", "outside", "identity", "range"]
)
def test_independent_verifier_rejects_tampering(kind: str) -> None:
    state = snapshot((demand(),))
    proposal = propose(state, request(SchedulingTask(task_public_id=task_id(1))))
    block = proposal.blocks[0]
    changes: dict[str, object] = {}
    if kind == "locked":
        changes["locked"] = True
    elif kind == "early":
        changes.update(starts_at_utc=NOW - timedelta(hours=1), ends_at_utc=NOW)
    elif kind == "busy":
        state = state.model_copy(update={"busy": (span(1, 2),)})
    elif kind == "duration":
        changes["ends_at_utc"] = block.ends_at_utc + timedelta(minutes=1)
    elif kind == "outside":
        changes.update(
            starts_at_utc=NOW + timedelta(hours=10), ends_at_utc=NOW + timedelta(hours=11)
        )
    elif kind == "range":
        state = state.model_copy(update={"working": (span(1, 49),)})
        changes.update(
            starts_at_utc=NOW + timedelta(hours=25), ends_at_utc=NOW + timedelta(hours=26)
        )
    else:
        changes["task_public_id"] = task_id(99)
    forged = proposal.model_copy(update={"blocks": (block.model_copy(update=changes),)})
    with pytest.raises(ValueError):
        verify_proposal(state, forged)


def test_long_continuous_window_can_hold_multiple_elapsed_day_pieces() -> None:
    state = snapshot((demand(minutes=2160),), (span(1, 49),))
    choices = request(SchedulingTask(task_public_id=task_id(1), split=True)).model_copy(
        update={"last_day": DAY + timedelta(days=2)}
    )
    result = propose(state, choices)
    assert [
        (block.ends_at_utc - block.starts_at_utc).total_seconds() for block in result.blocks
    ] == [43200, 43200, 43200]


def test_microsecond_credit_is_conserved_without_rounding_work() -> None:
    state = snapshot((demand(minutes=90, credited_seconds=3600.000001),))
    result = propose(state, request(SchedulingTask(task_public_id=task_id(1))))
    assert result.blocks[0].ends_at_utc - result.blocks[0].starts_at_utc == timedelta(
        minutes=30, microseconds=-1
    )


def test_a_piece_of_a_day_or_more_conserves_every_second() -> None:
    """C4-5: conservation counts a whole day as 86,400 seconds. A block lasts at most 12
    hours today, so the piece is built by hand to reach the arithmetic."""
    state = snapshot((demand(minutes=25 * 60),), working=(span(1, 26),))
    piece = ProposedBlock.model_construct(
        task_public_id=task_id(1),
        starts_at_utc=NOW + timedelta(hours=1),
        ends_at_utc=NOW + timedelta(hours=26),
        time_zone="UTC",
        locked=False,
    )
    proposal = SchedulingProposal.model_construct(
        request=SchedulingRequest(
            first_day=DAY,
            last_day=DAY + timedelta(days=1),
            tasks=(SchedulingTask(task_public_id=task_id(1)),),
        ),
        fingerprint=state.fingerprint,
        generated_at_utc=NOW,
        knowledge=state.knowledge,
        blocks=(piece,),
        unplaced=(),
    )
    verify_proposal(state, proposal)


def test_verification_holds_suggestions_to_the_quarter_hour() -> None:
    """C4-3: placement and verification share one earliest start, so a block at the bare
    five minutes is refused as placement would never make it."""
    state = snapshot((demand(),), working=(span(0, 9),))
    result = propose(state, request(SchedulingTask(task_public_id=task_id(1))))
    assert result.blocks[0].starts_at_utc == NOW + timedelta(minutes=15)
    early = result.blocks[0].model_copy(
        update={
            "starts_at_utc": NOW + timedelta(minutes=5),
            "ends_at_utc": NOW + timedelta(minutes=65),
        }
    )
    with pytest.raises(ValueError, match="constraints"):
        verify_proposal(state, result.model_copy(update={"blocks": (early,)}))


def test_missing_or_duplicate_task_outcomes_are_refused() -> None:
    state = snapshot((demand(),), ())
    result = propose(state, request(SchedulingTask(task_public_id=task_id(1))))
    for outcomes in ((), result.unplaced * 2):
        with pytest.raises(ValueError):
            verify_proposal(state, result.model_copy(update={"unplaced": outcomes}))
