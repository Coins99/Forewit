# P3: Local calendar

P3 is the roadmap's local calendar ([future implementations §7](future-implementations.md)):
C01–C05, work blocks, working hours and overload warnings. Its gate: plan a week; move,
resize and unschedule with the keyboard and Undo; respect fixed events, date semantics and
missing estimates. The owner approved it on 2026-11-05 as three pull requests, one at a
time:

| Part | Scope | Migration / ADR | State |
| --- | --- | --- | --- |
| P3-1 | C03 work blocks: storage, editor, every view, Today, task detail, drag, resize, keyboard, Undo | `20261105_0032` / [ADR 0028](adr/0028-work-blocks.md) | Implemented, awaiting owner acceptance |
| P3-2 | Unscheduled-task panel and Day view; Busy/Free on events (Google's "Show as" too); weekly working hours, up to 3 windows a weekday, and a buffer (C04) | `0033` / ADR 0029 | Not started |
| P3-3 | C05: conflicts, overfilled and provisional days, with their reasons; a read model only | none expected | Not started |

The owner's decisions (2026-11-05):

- **Three larger PRs.** These replace the five smaller ones first proposed.
- **Blocks are separate from tasks.** They have their own revision and never touch the task.
- **Busy or free.** Events get a Busy/Free choice: timed events are busy and all-day ones
  free by default. Google events use Google's own "Show as".
- **Working hours.** These are weekly hours plus a buffer. A day off is an all-day Busy event.

## P3-1: Work blocks

What it does is in [Calendar: Time for tasks](calendar.md#time-for-tasks), and the decisions
are in [ADR 0028](adr/0028-work-blocks.md).

### Where it lives

- **Storage.** Migration `20261105_0032_work_blocks` follows `20261105_0031`. The ORM
  model is `WorkBlockTable`, and the storage helpers are in `storage/work_blocks.py`.
- **Domain.**
  - `domain/work_blocks.py` holds `BlockTime`, `WorkBlock`, the limits, `scheduled_time`
    and `suggested_minutes`.
  - `domain/intervals.py` holds `union`, `clip`, `minutes`, `total_minutes` and
    `overlaps`; Upcoming's occupancy now uses it too.
  - `ScheduledBlock` sits in `domain/events.py`, beside `ActionMarker`.
  - The new fields are `CalendarRange.blocks`, `Action.blocks` and the `("block", id)`
    entry on `CalendarRoute`.
- **Service.** `services/work_blocks.py` holds `WorkBlockService`.
  `EventService.list_range` and the action loader read blocks. Plan with AI keeps them
  for local overlap warnings only.
- **Desktop.**
  - `ui/block_editor.py` is the block editor.
  - The Calendar page and grids (`calendar_view.py`, `calendar_grid.py` and
    `calendar_layout.py`) show blocks and handle dragging and the keys.
  - Schedule task reuses `TaskPickerDialog`.
  - The task detail shows Scheduled time and Schedule time, and Today's agenda lists
    blocks.
  - The window saves blocks and offers Undo.
- **Export and cleanup.** `work_blocks` is in `EXPORT_TABLES`, and the deleted-records
  cleanup counts the blocks it removes.

### Known limits

- **Unscheduled blocks are kept.** No cleanup purges them yet, as for deleted events.
- **Everything counts as busy.** Every timed Google event counts as busy for overlaps,
  until P3-2 caches Google's free or busy setting.
- **Moves wait for each other.** A key nudge pressed while the last move is still saving
  is refused as busy; the next press works. Local saves take milliseconds.
- **Owner events can't be dragged.** Event edits have no Undo yet.

### Verification

These were run on macOS (Apple silicon, Python 3.13) on 2026-11-05, from main `d5dd420`
plus this branch:

- **Linting.** Ruff format and lint pass.
- **Types.** Strict mypy passes for the native platform, win32 and darwin (511 files).
- **Tests.** The full suite, with the Qt tests offscreen, had 5,189 passed, 7 skipped and
  1 failed. The failure is
  `tests/unit/services/test_calendar.py::test_day_window_matches_the_window_of_any_moment_that_day[America/Havana-day5-25]`,
  which fails the same way on unchanged main code with this machine's time-zone data. It
  has nothing to do with blocks.
- **Coverage.** Coverage is 94.83% overall, and every service meets the 90% gate.
  `services/work_blocks.py` is at 98%, `domain/work_blocks.py` at 99% and
  `domain/intervals.py` and `storage/work_blocks.py` at 100%.
- **Migration.** Migration 0031 → 0032 → 0031 → 0032 is clean, and `alembic check`
  reports no difference between the migration and the ORM.
- **Gallery.** The Calendar gallery (Week, Month and List, light and dark, 1x) was
  rendered with blocks and inspected.
- **Not run locally.** The Cocoa-platform UI run and the native package build were left to
  CI, so that nothing opened windows on the owner's screen or touched their profile.

### Owner acceptance (desktop)

The first run from this branch applies migration 0032 to your database. If the database
is older, as yours was on 2026-11-05 (at `20261014_0020`), it also applies 0021–0031.

1. Give a task a 2-hour estimate. **Schedule time…** should suggest 2 hours. Save it, then
   check it shows in Week, Month, List and Today, and that the detail says 2 hrs.
2. Add a second block on another day.
3. Drag one block to another day and time, then press **Undo move**.
4. Resize a block by its bottom edge, then press **Undo resize**.
5. Select a block and press Ctrl or Cmd with the arrows, then Ctrl or Cmd with Shift+Down.
   Press **Undo**: the whole run goes back.
6. Unschedule a block with Delete, then press **Undo unschedule**.
7. Schedule a block over an event and over a Google event. The status line should name
   both.
8. Schedule a block after the task's deadline. You should get a warning, and the task's
   dates should stay unchanged.
9. Complete the task: its blocks should show greyed and stop moving. Reopen it, delete it,
   then Undo.
10. Complete a repeating occurrence that has a block. The next occurrence should have none.
11. In Plan with AI, the preview should show no block. A proposed change onto a block
    should warn "Task · …".
12. Restart, back up, restore and export. Then check light and dark mode, and the same
    steps on Windows.
