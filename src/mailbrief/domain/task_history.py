"""Effective task history for later metrics, independent of the capped UI timeline."""

from collections.abc import Sequence

from mailbrief.domain.actions import ActionActivity, ActionActivityKind
from mailbrief.domain.common import DomainModel


class TaskHistory(DomainModel):
    """Known effective transitions, with explicit limits on historical coverage.

    Completion episodes retain their original time and project/area snapshots.
    A normal reopen does not erase a completion; an undo corrects its referenced
    transition. This is per-task history, never a claim about purged tasks.
    """

    effective: tuple[ActionActivity, ...]
    complete: bool

    @property
    def completions(self) -> tuple[ActionActivity, ...]:
        return tuple(
            event for event in self.effective if event.kind is ActionActivityKind.COMPLETED
        )


def resolve_task_history(events: Sequence[ActionActivity], current_revision: int) -> TaskHistory:
    """Resolve full stored history; never pass the UI's latest 200 records.

    Walk corrections backwards: an active undo removes its target, while an undo
    of that undo restores the original event. Reject ambiguous correction graphs
    rather than manufacture a plausible total. Legacy/gapped history is partial.
    """
    ordered = sorted(events, key=lambda event: event.revision)
    by_revision = {event.revision: event for event in ordered}
    if current_revision < 1 or len(by_revision) != len(ordered):
        raise ValueError("Task history has invalid or duplicate revisions.")
    if ordered and ordered[-1].revision > current_revision:
        raise ValueError("Task history extends beyond the task revision.")
    active = set(by_revision)
    corrected: set[int] = set()
    for event in reversed(ordered):
        target = event.undo_of_revision
        if event.kind is not ActionActivityKind.UNDO:
            if target is not None:
                raise ValueError("Only an undo can reference a corrected revision.")
            continue
        if target is None or target >= event.revision or target not in by_revision:
            raise ValueError("Task history has an unavailable or invalid undo target.")
        if event.revision in active:
            if target in corrected:
                raise ValueError("Task history corrects the same revision more than once.")
            corrected.add(target)
            active.discard(target)
    return TaskHistory(
        effective=tuple(
            event
            for event in ordered
            if event.revision in active and event.kind is not ActionActivityKind.UNDO
        ),
        complete=(
            len(ordered) == current_revision
            and all(event.revision == index for index, event in enumerate(ordered, start=1))
            and not any(event.legacy for event in ordered)
        ),
    )
