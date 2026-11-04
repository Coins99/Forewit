"""Completion episodes and correction semantics used by later dashboard readers."""

from datetime import UTC, datetime, timedelta

import pytest

from mailbrief.domain.actions import ActionActivity, ActionActivityKind
from mailbrief.domain.task_history import resolve_task_history

START = datetime(2026, 11, 1, tzinfo=UTC)


def event(
    revision: int, kind: str, undo: int | None = None, legacy: bool = False
) -> ActionActivity:
    return ActionActivity(
        revision=revision,
        kind=ActionActivityKind(kind),
        occurred_at_utc=START + timedelta(hours=revision),
        undo_of_revision=undo,
        legacy=legacy,
        project_public_id=f"project-{revision}",
        area_public_id=f"area-{revision}",
    )


def test_reopen_and_recompletion_preserve_distinct_episodes() -> None:
    events = [event(1, "created"), event(2, "completed"), event(3, "reopened")]
    events.append(event(4, "completed"))
    history = resolve_task_history(list(reversed(events)), 4)
    assert history.complete
    assert history.completions == (events[1], events[3])
    assert history.completions[0].project_public_id == "project-2"
    assert history.completions[0].area_public_id == "area-2"


def test_undo_reopen_does_not_duplicate_or_redate_completion() -> None:
    events = [event(1, "created"), event(2, "completed"), event(3, "reopened"), event(4, "undo", 3)]
    assert resolve_task_history(events, 4).completions == (events[1],)


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_undo_chains_alternate_effective_completion(depth: int) -> None:
    events = [event(1, "created"), event(2, "completed")]
    for revision in range(3, 3 + depth):
        events.append(event(revision, "undo", revision - 1))
    assert len(resolve_task_history(events, 2 + depth).completions) == (1 if depth % 2 == 0 else 0)


@pytest.mark.parametrize(
    "events,revision",
    [
        ([], 1),
        ([event(2, "completed")], 2),
        ([event(1, "created", legacy=True)], 1),
        ([event(1, "created")], 2),
    ],
)
def test_missing_or_legacy_history_never_claims_complete_coverage(
    events: list[ActionActivity], revision: int
) -> None:
    assert not resolve_task_history(events, revision).complete


@pytest.mark.parametrize(
    "events,revision",
    [
        ([event(1, "created"), event(1, "completed")], 1),
        ([event(1, "created")], 0),
        ([event(2, "completed")], 1),
        ([event(1, "undo", 1)], 1),
        ([event(1, "undo")], 1),
        ([event(2, "undo", 1)], 2),
        ([event(1, "created", 1)], 1),
        ([event(1, "completed"), event(2, "undo", 1), event(3, "undo", 1)], 3),
    ],
)
def test_invalid_history_fails_instead_of_returning_a_total(
    events: list[ActionActivity], revision: int
) -> None:
    with pytest.raises(ValueError, match="history|undo"):
        resolve_task_history(events, revision)
