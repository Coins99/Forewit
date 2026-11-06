# ADR 0030: Workload and conflicts

Date: 2026-11-06

Status: Accepted for P3-3 implementation; owner acceptance remains open.

Builds on ADR 0028 (work blocks) and ADR 0029 (Busy/Free and working hours).

## Context

After P3-2 the owner can see how much working time is free and is warned when a block is
saved over something busy. Looking at a week, nothing says which days hold more than they
have time for, which blocks clash, or when the figures can't be trusted. The roadmap's C05
asks for conflict and workload detection, and §5.2 defines "selected workload", "scheduled
task time" and "available time", with incomplete calendars making capacity provisional.
P3-2 also left one gap: a block inside a busy event's buffer was never warned about.

On 2026-11-06 the owner decided that a day is provisional only while Google Calendar is
connected and its cache misses the day or is over a day old, or when a focus task has no
estimate. Without Google Calendar, the owner's own events are the whole calendar.

## Decision

- **A read model only.** `domain/workload.py` works it out from the `CalendarRange` the
  window already reads: no migration, no table, no write, no second read. It never changes
  a task, a block or an event, and nothing about it is sent to AI; Plan with AI is
  unchanged.
- **The range carries what it needs.** `ScheduledBlock` gains its task's estimate, when its
  deadline falls due and `upcoming_minutes`, the task's live blocks from the owner's today
  on, as the tasks to schedule count them (ADR 0029; `storage.work_blocks.upcoming_minutes`
  is the one query for both). `CalendarRange.focus` holds each day's focus tasks the owner
  does, live and open, with their estimates, read with one bounded query
  (`storage.day_plans.focus_days`). Only the Calendar page and Today ask for these
  (`list_range(..., with_workload=True)`); Plan with AI, Upcoming and the next free time
  don't pay for them.
- **Definitions, per local day in the owner's zone.**
  - *Available*: working time less busy time, as P3-2 works it out (busy events widened by
    the buffer; a day off takes the whole day). Unknown without working hours.
  - *Scheduled*: the open tasks' blocks clipped to the day, both their total and the
    unique minutes they cover.
  - *Focus without time*: the known estimates of the day's focus tasks with no block that
    day, and how many have none. A focus task with a block that day counts through its
    blocks only.
  - *Planned* = unique scheduled minutes inside the working windows + focus without time;
    a block's time outside them uses no working time (it is a conflict instead).
    *Overfilled*: planned is more than available.
  - *Provisional*: Google Calendar is on and its cache misses the day or was last filled
    more than `PROVISIONAL_AFTER` (24 hours) ago, which is apart from the 15 minutes after
    which a refresh is due; or a focus task has no estimate. A provisional day is never
    shown as fitting: "≤ 3h spare", "Over by 1h+", "at most", "at least".
  - *Spare* is available less planned, for the whole day. *Free*, as the free line and
    Today say it, stays P3-2's: working time no busy event or block takes, from now on.
- **Past days are shown but never flagged.** Only today and later can be overfilled,
  provisional or in conflict, and only open tasks' blocks that end after the start of
  today are checked.
- **Conflicts** (`conflicts_for`): a block overlapping a busy event; sitting in a busy
  event's buffer without overlapping it; overlapping another block; on a day off; partly
  outside the working hours; ending after its task's deadline; or its task having more
  time set aside from today on than its estimate, marked on the task's latest block shown
  only. Free events never conflict. Saving a
  block warns from the same function, so a save's "Overlaps …", "Within the buffer of …",
  "Ends after the task's deadline" and "Outside your working hours" match the Calendar's
  marks. Every conflict stays a warning; nothing is refused.
- **The desktop.** Each day's head in the Week and Day views carries its load ("3h
  spare", "Over by 1h" in the warning colour); a block in conflict has a small mark; the
  line under the hours explains the day or block chosen, naming every part of the sum and
  every reason. Today's focus says how much time is free today and how far the day is over.
  Upcoming points to the Calendar for free time and workload.

## Consequences

- P3's gate (plan a week; respect fixed events, date semantics and missing estimates) has
  its last part: the owner sees where a plan doesn't fit and why.
- The over-estimate conflict is known only where the range was read; a save's warning
  doesn't include it, since the save reads no other blocks of the task.
- The Month view shows no load, and the List view only marks a block "· conflict"; the
  explanations are in the Day and Week views.
- C06 (suggested daily plans) can build on `DayWorkload` and `conflicts_for` without new
  storage.
