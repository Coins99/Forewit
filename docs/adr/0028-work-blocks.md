# ADR 0028: Work blocks, time set aside for a task

Date: 2026-11-05

Status: Accepted for P3-1 implementation; owner acceptance remains open.

Amends ADR 0018 (calendar), whose phase 3 left work blocks to the planner roadmap's P3.

## Context

The roadmap's P3 (`docs/future-implementations.md` §3.3 and §7) is the local calendar:
C03 task time blocks, then working hours (C04), and conflict and workload checks (C05).
Before P3 the Calendar showed the owner's events, Google's cached events and the open
actions' deadlines and targets, but nothing said when the owner meant to work on a task.
A task's estimate (`estimate_minutes`) said how long, never when.

The roadmap asks for several blocks per task, moved, resized and unscheduled with the
keyboard and Undo, and keeps planned time apart from deadlines, target dates and actual
work. On 2026-11-05 the owner chose that a block is separate from its task: changing a
block never counts as a change to the task.

## Decision

- **A table of its own.** Migration 0032 adds `work_blocks`:
  - a unique `public_id`;
  - `action_id`, a foreign key to `actions.id` with `ON DELETE CASCADE`, as `action_steps`
    has, so purging a task removes its blocks;
  - `starts_at_utc` and `ends_at_utc`, with CHECK `ends_at_utc > starts_at_utc`;
  - `time_zone`, the IANA zone the block was entered in, as a timed event keeps one;
  - created, updated and a nullable `deleted_at_utc`, since unscheduling is soft for Undo;
  - its own `revision`.

  A block has no title, notes, lock or account. It shows its task's live title.
- **A block never changes its task.** `WorkBlockService` never writes `actions`: no
  revision, no `updated_at_utc`, no activity, no deadline or target date. Moving a deadline
  or target date never moves a block either. An open task editor therefore never goes stale
  because a block moved. The roadmap's §5.4 sketch, which had block changes in task history,
  is not followed; a later scheduled-time metric reads blocks directly.
- **Writes.**
  - Each write needs the revision the caller saw, except restoring an unscheduled block.
  - Each makes one block revision and commits once.
  - Changes go through ORM objects only.
  - Only a live, open task takes a new or moved block (waiting included). A completed or
    skipped task keeps its blocks, shown muted. They can be unscheduled but not moved until
    the task is reopened.
  - Undo of a move works only while the block is as the move left it.
  - Restoring a block doesn't check its task's state, so Undo works after the task was
    completed.
  - Errors carry static messages and never a title.
- **Limits.** A block lasts 5 minutes to 12 hours (`BlockTime`), and a task holds at most 50
  live blocks. Since no block lasts a day, a range read is bounded on the start index.
- **Lifecycle.**
  - Deleting a task hides its blocks; reads join live tasks. Restoring the task brings
    them back.
  - Unaccepting a task deletes it softly, so its blocks hide the same way.
  - Purging a task cascades to its blocks, and the cleanup preview counts them.
  - A repeating task's next occurrence starts with none.
  - Unscheduled blocks aren't purged yet, the same known limit deleted events have.
- **Warnings never block.** A save says which timed entries the block overlaps:
  - the owner's events;
  - Google's timed events, every one counting as busy until P3-2 caches free or busy;
  - other blocks, the same task's included.

  It also says when the block ends after the task's deadline. At most three overlaps are
  named. They are computed locally.
- **AI never sees blocks.** Plan with AI keeps the window's blocks only to warn locally
  ("Task · title") when a proposed change overlaps one. The request, its disclosure version
  and `validate_plan` are unchanged, and a test checks that no block data is sent.
- **Exports and recovery.**
  - `work_blocks` joins `EXPORT_TABLES` after `calendar_events`; the format version stays,
    as it did for B05.
  - Backup and recovery take the table through the ORM schema check, and restore keeps it,
    unlike the Google cache.
  - The cleanup fingerprint covers it.
- **The desktop.**
  - The Calendar shows blocks in the Week, Month and List views, and Today's agenda lists
    today's. A block is drawn with text on a card with an accent rim, or secondary text
    with a strong rim once its task is done.
  - Schedule task, beside New event, chooses an open task and opens the block editor.
  - The task detail has Scheduled time and Schedule time.
  - In the week grid an open task's block can be dragged to move it, by its bottom edge to
    change its length, and moved or resized with Ctrl or Cmd and the arrows; Delete or
    Backspace unschedules it.
  - Undo is the window's single slot. Moves of one block in a row undo together.

## Consequences

- P3-2 (working hours, Busy/Free, the unscheduled-task panel and the Day view) and P3-3
  (conflicts and overfilled days) build on these blocks without changing tasks.
- A block never stands for measured work. Actual time (C08) needs explicit sessions, not
  blocks.
- Letting AI propose blocks would mean naming actions to the model, which AGENTS.md
  forbids. It needs its own ADR, probably with C06.
