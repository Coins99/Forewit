"""What the Calendar and Today say about workload and conflicts (ADR 0030), as plain text.

A day column's head carries a short load line; the line under the hours explains the day
or block chosen, naming every part of the sum and every reason a figure may be wrong.
"Spare" is a day's available time less what is planned for it, the whole day; "free", as
the free line and Today say it, is working time no busy event or block takes, from now on. Event
and task titles are the owner's, Google's or an email's text, so the explanations reach the
screen only through plain-text labels and ``drawText``.
"""

from datetime import datetime, timedelta
from typing import Final

from mailbrief.domain.availability import free_after
from mailbrief.domain.workload import Conflict, ConflictKind, DayWorkload, ProvisionalReason
from mailbrief.ui.deadline_text import length_text

SPARE: Final = " spare"
_REASONS: Final = {
    ProvisionalReason.GOOGLE_MISSING: "Google Calendar isn't synced for this day",
    ProvisionalReason.GOOGLE_OLD: "Google Calendar was last synced over a day ago",
}


def length(minutes: int) -> str:
    """ "3 hrs 30 mins"."""
    return length_text(timedelta(minutes=minutes))


def short_length(minutes: int) -> str:
    """ "3h 30m", for a day column's head."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest}m"
    return f"{hours}h" if hours else f"{rest}m"


def header_text(load: DayWorkload, *, wide: bool = False) -> tuple[str, bool]:
    """A day column's load line, and whether it warns: "3h spare", "≤ 3h spare" when
    provisional, "Over by 1h" (or "1h+") when overfilled. Nothing for a past day or without
    working hours, or on a day without working time and nothing planned for it. ``wide``
    (the Day view) adds what was planned of what is available."""
    available = load.available_minutes
    if not load.flagged or available is None:
        return "", False
    planned = load.planned_minutes
    if not available and not planned:  # No working time, and nothing planned for it.
        return ("Day off", False) if load.availability.day_off else ("", False)
    if load.overfilled:
        more = "+" if load.provisional else ""
        text = f"Over by {short_length(load.over_minutes)}{more}"
    else:
        text = f"{'≤ ' if load.provisional else ''}{short_length(available - planned)}{SPARE}"
    if wide and available:
        text = f"{text} · {short_length(planned)} planned of {short_length(available)}"
    return text, load.overfilled


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def day_reasons(load: DayWorkload, name: str) -> str:
    """The sums behind a day's load, and why they may be wrong: "Wed Oct 14: 7 hrs
    available, 8 hrs planned (5 hrs in blocks, 3 hrs for 2 focus tasks without time) · over
    by 1 hr · at least: 1 focus task has no estimate". ``name`` is the day as shown."""
    parts: list[str] = []
    if load.scheduled_working_minutes:
        blocks = f"{length(load.scheduled_working_minutes)} in blocks"
        if load.scheduled_total_minutes > load.scheduled_minutes:
            blocks += f" that overlap, {length(load.scheduled_total_minutes)} in all"
        parts.append(blocks)
    estimated = load.focus_tasks - load.focus_unestimated
    if estimated:
        tasks = _plural(estimated, "focus task")
        parts.append(f"{length(load.focus_minutes)} for {tasks} without time")
    if load.focus_unestimated:
        # Never as zero minutes: how long they take isn't known.
        parts.append(f"{_plural(load.focus_unestimated, 'focus task')} without an estimate")
    if load.outside_minutes and load.available_minutes is not None:
        parts.append(f"not counting {length(load.outside_minutes)} outside working hours")
    planned = f"{length(load.planned_minutes)} planned"
    if parts:
        planned += f" ({', '.join(parts)})"
    available = load.available_minutes
    if available is None:
        return f"{name}: {planned}. Set your working hours to see what fits."
    has = "a day off" if load.availability.day_off else f"{length(available)} available"
    text = f"{name}: {has}, {planned}"
    if load.flagged:
        if load.overfilled:
            text += f" · over by {'at least ' if load.provisional else ''}"
            text += length(load.over_minutes)
        else:
            spare = length(available - load.planned_minutes)
            text += f" · {'at most ' if load.provisional else ''}{spare} spare"
        why = [_REASONS[reason] for reason in load.provisional_reasons if reason in _REASONS]
        if load.focus_unestimated:
            task = _plural(load.focus_unestimated, "focus task")
            why.append(f"{task} {'has' if load.focus_unestimated == 1 else 'have'} no estimate")
        if why:
            text += f" · provisional: {'; '.join(why)}"
        if load.conflicted_blocks:
            text += f" · {_plural(load.conflicted_blocks, 'block')} in conflict"
    return text


def conflict_text(conflict: Conflict, estimate_minutes: int | None) -> str:
    """One conflict in words: "overlaps Standup", "within the buffer of 1:1 (Work)"."""
    label = conflict.label or ""
    match conflict.kind:
        case ConflictKind.BUSY | ConflictKind.BLOCK:
            return f"overlaps {label}"
        case ConflictKind.BUFFER:
            return f"within the buffer of {label}"
        case ConflictKind.DAY_OFF:
            return f"on a day off ({label})"
        case ConflictKind.OUTSIDE_HOURS:
            return "outside your working hours"
        case ConflictKind.AFTER_DEADLINE:
            return "ends after the task's deadline"
        case ConflictKind.OVER_ESTIMATE:
            estimate = "" if estimate_minutes is None else f" of {length(estimate_minutes)}"
            return f"more time set aside than the task's estimate{estimate}"


def block_reasons(title: str, conflicts: tuple[Conflict, ...], estimate: int | None) -> str:
    """ "Task · Draft report: overlaps Standup · outside your working hours"."""
    return f"Task · {title}: " + " · ".join(conflict_text(item, estimate) for item in conflicts)


def today_text(load: DayWorkload, now: datetime) -> str:
    """Today's line under the focus: the free time left from ``now``, and how far the day is
    over. Nothing without working hours, or on a day without working time."""
    available = load.available_minutes
    if available is None:
        return ""
    if not load.availability.working_minutes and not load.availability.day_off:
        return ""
    if load.availability.day_off:
        text = "Day off today"
    else:
        approx = "at most " if load.provisional else ""
        text = f"{approx}{length(free_after(load.availability, now))} free today"
        text = text[0].upper() + text[1:]
    if load.overfilled:
        text += f" · planned {length(load.planned_minutes)}, over by "
        text += f"{'at least ' if load.provisional else ''}{length(load.over_minutes)}"
    return text
