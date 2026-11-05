# P2-03: durable daily focus

Implemented locally on `feat/p2-navigation`, continuing navigation and capture.
This checkpoint implements daily selection and the first focus-first Today view.
Native macOS and owner acceptance remain open; full P2 is not complete.

## Workflow

**My focus** appears first on Today. Choose **Plan today** there or on Actions to
search open tasks, add selections and change their order. Waiting tasks can be
selected as follow-ups. **Open** reveals the canonical task; **Complete** and
**Reopen** use the existing task lifecycle and Undo. Completed selections remain
visible. **Remove** removes only the daily selection.

In the chooser, use **Move up** / **Move down**, or **Alt+Up** / **Alt+Down** with
the selected list focused. The same shortcuts work on the compact Today list.
After saving a captured Task, **Add to today** selects that saved task. Notes retain
their writing workflow. No account, network or AI permission is needed.

The workload shows known estimated minutes, the number of open tasks without
estimates and waiting follow-ups separately. Estimates on waiting work do not count
as the owner's execution time. Due/overdue reasons remain visible on selected rows.
The agenda preview and **Other commitments needing attention** follow focus; the
latter excludes selected tasks. **Your day** expands the agenda, other commitments
and new replies. The saved mail brief remains below them for this checkpoint.
The agenda previews up to two appointments with elided titles; **Agenda** opens
today's full calendar. Screen readers retain each appointment's complete text.

## Persistence and failure behavior

- One plan per profile/date, with stable selection IDs and explicit order. Empty
  reads do not write. The creation zone remains historical metadata.
- Every membership/order command requires the displayed plan revision. Conflicts
  reload the plan. An uncertain result is reloaded for review before retrying.
- Selecting requires a live open task; duplicate selection is a no-op. The cap is
  100 selections per day. Searching returns up to 50 title matches; narrow the query
  to find older work. Already-selected tasks are excluded before applying that
  limit. Queries use the existing local search word rules.
- Completion and reopening project current task state without creating another
  lifecycle fact. Recurring work keeps its exact occurrence ID, including after
  completion. A selected successor survives undo of the prior completion.
- Soft-deleted tasks appear unavailable; restoring them restores their projection.
  Permanent cleanup removes their selections and normalizes order, retaining the
  plan and incrementing its revision once. The cleanup preview counts selections.
- Membership/order survives restart, backup/restore and portable writing export.
  Cache cleanup and disconnected email do not remove it.
- Midnight selects the new current date without carrying work forward. Zone changes
  select the relevant current local date without renaming historical plans. Focus
  refreshes independently while email/review work occupies the foreground slot.
- Focus writes run independently of email work; shutdown joins them before closing
  storage. Late reads are guarded by date, zone, query and closing state.

See [ADR 0026](adr/0026-durable-daily-focus.md). Implementation lives in
`domain/day_plans.py`, `storage/day_plans.py`, `services/day_plans.py` and
`ui/focus_view.py`; migration 0028 adds the two owner-data tables.

## Verification

- Focused service/migration/package-scenario checks: 21 passed. They cover independent
  session conflicts and competing first inserts, no-ops, rollback, the 100-item cap,
  task lifecycle, exact recurring occurrences, restore/export and safe purge.
- Initial focus/Today/keyboard regression: 52 passed. Final focus-specific checks:
  13 passed, including actual keyboard completion/reorder and shutdown joining an
  in-flight save, narrow layouts, workload, conflicts, midnight, capture and restart.
- Final Windows regression after the review repairs: **5,057 passed**, nine expected
  platform/intentional skips, 25 warnings; **95.73%** combined coverage. Every service
  clears the 90% combined statement/branch gate. This run includes all three repairs
  and their regression cases; the repair-focused service/UI/keyboard run passed 51.
- Ruff lint/format and strict win32/darwin typing passed (482 source/test files in
  each typing run).
- Final gallery/theme checks: 316 passed and one intentional skip at each of 1x
  and 2x. Light/dark full and compact focus, chooser and repaired long-agenda renders
  were visually inspected at both scales. [The two-round review and repair record](p2-focus-review.md)
  traces the three findings to regression coverage.
- Fresh Windows x64 single-file package checks passed: offline 1x/2x, native
  Windows launch, repeated persistence, renamed-copy profile locking, old-schema
  upgrade and synthetic backup/verify/restore/export/cache cleanup. Package smoke
  seeds a captured task and day selection, verifies them on the next launch without
  reseeding, checks their export and verifies the restored selection after cleanup.

Verified executable SHA-256:
`e75ffaf2b1d5ba37118ccc73efde948ca1d704f3ac7ce08cd9e62cb076eaf773`.
The executable is an engineering build from the local working tree, not a published
release. The manifest records the baseline commit and dirty working-tree state.

Compared with the Studio references, the persistent focus card replaces the former
mail-metric strip and uses more vertical space; Today has its own heading and Capture
remains in the header. Shared fonts, surfaces, rounded cards, dialog margins and pill
controls remain consistent. Short-window checks use actual window geometry and
preserve a readable brief row and complete commitments-card rows. Empty status space
was removed from the preview. Approved reference images remain unchanged pending
owner acceptance.

Validation uses disposable synthetic records, without owner profiles, credentials,
real mailboxes or paid AI. Logs are under ignored `out/p2-focus-*`; renders under
`scratch/p2-focus-shots/`. Repair logs use `out/p2-fixes-*` and renders use
`scratch/p2-fixes-shots/`. Native macOS and hands-on owner usability remain open.

## Next checkpoints

P2-04 adds Notes and project context: [P2-04a](p2-notes.md) adds the Notes page and
note links. PR #102 ([the workspace register](p2-workspace.md)) adds the Projects page,
the Tasks/Mail workspace, historical date browsing, carry/move and Upcoming (P2-04b to
P2-09). This checkpoint does not implement them.

## Windows CI time-zone follow-up

The first PR #99 Windows run exposed two test failures on its Tokyo-configured
runner: travel rollover started in Tokyo already, and the shutdown assertion
looked for a Toronto-date selection that had been saved for the Tokyo date.
The shared UI helper applied a Toronto zone before initialization, which then
reloaded the fake backend's unset preference and replaced it with the system zone.

The helper now seeds the backend preference before initialization. Two explicit
UTC/Tokyo-host cases assert that the saved fixture zone and local date survive
startup. All 81 focus/palette/navigation/capture checks passed with `TZ=Asia/Tokyo`.
Production code is unchanged by this follow-up; PR CI reruns the full platform checks.
