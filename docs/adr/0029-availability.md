# ADR 0029: Busy or free, working hours, and the tasks to schedule

Date: 2026-11-06

Status: Accepted for P3-2 implementation; owner acceptance remains open.

Amends ADR 0018 (calendar) and ADR 0028 (work blocks).

## Context

P3-1 put work blocks on the Calendar, but nothing said which events take time, when the
owner works or which tasks still need time. Every timed Google event counted as busy and
every all-day event as nothing. The roadmap's C04 asks for availability: configured
windows less the busy time, with buffers, never counting overlapping meetings twice. On
2026-11-05 the owner decided: events get Busy or Free (timed busy and all-day free by
default; Google events use Google's own "Show as"); working hours are weekly windows plus a
buffer; a day off is an all-day Busy event.

## Decision

- **Busy or Free.** Migration `20261105_0033` adds `busy` to `calendar_events` and to
  `google_events`. Existing rows are filled in as timed busy and all-day free, so they mean
  what they meant before. Left out, a new event's `busy` follows its shape
  (`shape_busy`). The event editor's **Show as** follows All day until the owner chooses.
- **Google's "Show as".** The events request adds `transparency` to its named `fields`.
  An event is busy unless Google says `transparent`, Google's own default whatever the
  shape. No other field is requested; descriptions, locations, guests and conference
  details are still never fetched or stored. The cache stays out of exports and the
  cleanup fingerprint, and restore still drops it.
- **Working hours.** A new single-row, revisioned `working_hours` table: seven lists,
  Monday first, of up to three `[start, end]` minute windows on quarter hours (24:00 ends a
  day), and a buffer of 0, 5, 10, 15 or 30 minutes. No row means none are set; nothing is
  shaded and no free time is worked out until the owner sets them. They are kept apart
  from the preferences row: unreadable preferences stop email work (ADR 0014), and a
  calendar setting must never do that, nor conflict with an open Settings form. Hours that
  can't be read count as none, keeping the row's revision so that saving replaces them;
  they are only shown and never decide what is sent. Every save needs the revision the
  caller saw, goes through ORM objects and commits once. They are wall-clock times in the
  owner's zone. They are in the writing export and the cleanup fingerprint, and restore
  keeps them.
- **Free time** (`domain/availability.py`) is each day's working windows less busy events
  (the owner's and Google's; each timed one widened by the buffer on both sides), less the
  blocks of open tasks. An all-day busy event takes its whole days: a day off. Free events
  take nothing. Unions make each instant count once. It is worked out locally from what
  the calendar holds and never sent anywhere.
- **Warnings.** A block's save names the busy entries it overlaps, all-day busy ones on its
  days included, and says when it falls outside the working hours. Free events never
  overlap. Plan with AI's local warnings follow the same rule. All of these stay warnings:
  nothing is refused.
- **Plan with AI.** The "Google busy times" part sends only Google events shown as busy;
  the request schema and prompt version are unchanged. A proposed update keeps the
  event's Busy or Free while it keeps its shape, and a new event, or one changing shape,
  takes its shape's default: the model never chooses it.
- **Tasks to schedule.** `AvailabilityService.unscheduled` lists the owner's live, open
  tasks (not waiting ones) that still need time: an estimate more than their blocks from
  today on, or, without an estimate, no block from today on. Earlier blocks don't count, as
  a block is never work done. They are in the Actions page's order (`urgency`), at most 50,
  with the total. The query reads columns, not ORM rows, and sums blocks in SQL.
- **The desktop.** A Day view joins Week, Month and List. Week and Day shade the hours
  outside the working hours with a faint wash of muted text (`Tokens.off_hours`), and draw
  free events as outlines on the page's own fill. A line under the hours says how much
  working time is still free from now on. **Working hours** opens a drawer with one line of text per weekday.
  **Tasks to schedule** shows the panel beside the hours, remembered in appearance.json.
  Return on a task opens the block editor at the next free stretch of its suggested length
  in the next 14 days; dragging a task onto the hours schedules it there at once, with
  Undo. The drag is the panel's own press, move and release, never `QDrag`, whose
  `exec()` runs a nested event loop, the hazard `dialog_guard` exists to avoid.

## Consequences

- P3-3 builds conflicts, overfilled and provisional days on `DayAvailability`, including
  buffer conflicts, which P3-2 only uses to place new time.
- Days off are events, not settings, so they show on the calendar and in Google.
- An older build reading `"day"` from appearance.json opens the Week view; it ignores
  `calendar_tasks`.
