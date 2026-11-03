# P2-03 two-round review

Date: 2026-11-03. Scope: the current local P2-03 daily-focus implementation and
its integration with navigation, capture, task lifecycle and data recovery.

The review found three confirmed P2 issues. All three are now fixed in the repair
round below. The finding descriptions and locations retain the reviewed snapshot
for traceability; the previous checkpoint's tests did not cover these edge cases.

## Round 1: design and owner workflow

Reviewed the P2 design sections 11.2-11.4, the design critique's first Today
hierarchy, implementation-plan checkpoint P2-03, ADR 0026 and the delivered workflow.
The independent day-plan model, stable task occurrence references, remaining-work
calculation and separation from task dates/completion history follow the design.
Historical planning, capacity and the later Mail workspace retain their planned scope.

### D1 — P2: appointment text is silently clipped in short windows

Location: `src/mailbrief/ui/focus_view.py:187-188`.

The agenda combines the full titles of two events into one wrapping label. There
is no preview line budget or elision. In an actual 849-by-544 window with one
selected task and two valid 184-character appointment titles, the themed label
received 49 pixels while its text required 85. Both light and dark modes clipped
the remaining appointment text without an ellipsis. The screenshot confirms the
second event is cut off; its full details cannot be read from this preview.

This misses the design's bounded, readable small-window hierarchy. Render at most
two bounded agenda rows with elision and an explicit way to open the full agenda.
Preserve room for focus controls and the brief. Add long-title/short-window checks
that validate readable content, rather than only the presence of the attention button.

## Round 2: code and correctness

Examined the domain and schema, migration, optimistic writes, two-stage ordering,
candidate search, current task hydration, recurrence withdrawal, cleanup batching,
export transactions and UI async/read/write lifecycle.

### C1 — P2: selected tasks exhaust the chooser's result limit

Locations: `src/mailbrief/services/day_plans.py:79-96` and
`src/mailbrief/ui/focus_view.py:297-306`.

The database limits matching open tasks to 50 before the dialog removes tasks
already selected in this day plan. With 51 open tasks titled "Follow up", selecting
the 50 offered matches leaves zero choices even though another matching open task
exists and the daily cap is 100. Because the titles are identical, narrowing the
query cannot reveal the remaining task.

Exclude this date's selected task IDs in the database query before applying the
50-result limit, or provide pagination. Add a regression covering selected matches
at the result boundary and an older unselected task with the same title.

### C2 — P2: busy email work delays the focus date rollover

Location: `src/mailbrief/ui/main_window.py:1501-1507`; integration with
`MainWindow._refresh_day` at line 2273.

The minute tick updates the current date but queues its refresh through the shared
foreground `start()` slot. An active email/review operation refuses that start, so
the focus read never runs until the foreground slot becomes free and a later tick
retries. A probe advancing October 13 to October 14 during a held operation showed
the current date as October 14 while `focus_panel.plan.day` remained October 13.

The existing command date guard prevents writing the old plan, but the owner sees
yesterday's work under today's heading and must trigger a reload before using it.
Refresh/clear the current focus date through its independent local read lifecycle
on midnight or a zone transition, while retaining the queued legacy view refresh.
Test the actual minute-tick path with a held foreground operation, rather than
calling `_refresh_day()` directly as the current midnight test does.

## Validation and limits

- The current focused baseline passed **30 tests**: day-plan service, migration and
  focus UI tests. These include independent-session conflicts, rollback, recurrence,
  restart, backup/export/restore and purge.
- Disposable characterization probes reproduced all three findings. The agenda
  case was checked in both themes and its light-mode render was visually inspected.
  The probes assert the observed behavior; passing them confirms reproduction.
- Review logs: ignored `out/p2-review-baseline.log` and
  `out/p2-review-probes.log`. Disposable probes and renders:
  ignored `scratch/p2-review/`.
- No additional issue was found in the revision, reorder, recurrence-identity or
  recovery/purge code paths examined. Native macOS and owner usability acceptance
  remain open as recorded in the checkpoint.

Recommended repair order: candidate exclusion, independent date rollover, then the
bounded agenda preview. Re-run the corresponding regression cases and focused
baseline after changes; refresh visual evidence for the agenda adjustment.

## Repair round

- **D1 resolved:** the agenda uses two single-line `ElidedLabel` rows, preserving
  full text for screen readers. An **Agenda** button opens today's canonical calendar
  view and joins the main keyboard order. Long-title tests in both themes check
  complete row geometry, room for the brief and actual keyboard activation.
- **C1 resolved:** the dated candidate query excludes selected task IDs in SQL
  before applying its 50-result limit. The runtime passes the query's captured date.
  The regression creates 51 identically titled tasks, selects the first 50 and
  requires the remaining match to stay available. It also verifies another date
  and that removing a selection makes it available again.
- **C2 resolved:** date/zone transitions schedule an independent local focus read,
  retaining the queued refresh for legacy views. Closing prevents new reads.
  Minute-tick regressions hold foreground work open through midnight and a travel
  zone change, check the preview and open chooser, and preserve the previous plan.

The initial repair validation passed 51 service/focus/keyboard tests. Final full
regression, typing, gallery and package evidence is recorded in
[the checkpoint verification register](p2-focus.md).
