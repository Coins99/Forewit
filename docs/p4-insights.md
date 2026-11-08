# P4: Insights implementation register

Started 2026-11-08 on `feat/p4-insights`, from refreshed main
`7ecb649` (PR #109 merges P3 scheduling add-ons).
Status: milestones 1–4 (schema/recovery, metric lifecycle, task aggregates and
query/workload engine) implemented locally. Durable Insights is not yet complete
or accepted.

Product scope is `future-implementations.md`, sections 3.6–3.7 and 5–7:
F02, G01–G03, G05–G07 and G09. Architecture decisions for this increment are in
[ADR 0032](adr/0032-metric-contracts.md).

## Current increment

| Package | State |
| --- | --- |
| P4-00 baseline | Refreshed remote main; verified P3 add-ons integrated; source history and live day-plan/workload contracts inspected |
| P4-00 renderer | Qt line/bar/number candidate with exact equivalent table, missing/zero distinction, daily target and keyboard selection; native/accessibility feasibility gates remain open |
| P4-01 domain contracts | Exact numeric definition/entry inputs, typed descriptor/query/range, validated widget/layout and bounded prepared display implemented |
| P4-01 durable schema | Migration 0037: definitions, entries, structured correction history and text-free receipts; strict backup verification, portable export and explicit cleanup integration |
| P4-02 lifecycle | Revisioned define/configure/archive, create/edit/delete/restore/Undo, atomic retry receipts; stale/concurrent/uncertain/cancelled-save behavior covered |
| P4-03 task aggregates | Batched uncapped retained history, corrections resolved before date filtering, event-time grouping, creation/completion/distinct/current-overdue metrics and explicit coverage |
| P4-04 query/workload engine | Exact custom reducers, date-only/instant ranges, current selected estimates, total/union scheduled time, P3 availability/planned workload, targets/comparisons and generation-checked contributors |
| P4-05–09 | Pending: durable dashboard layout, production charts, desktop editors/navigation, UI drill-down/refresh integration and full release acceptance |

The component deliberately has no sidebar page until durable dashboard layouts and
editors exist. Synthetic gallery tests demonstrate it without accounts, network,
credentials, local models or owner data. No application dependencies changed.

## Baseline findings carried into later packages

- `ActionService.history()` and `resolve_task_history()` already resolve complete
  per-task correction chains. Load histories in batches; never use the capped UI list.
- Task history completeness is per retained task, not a lifetime profile guarantee.
  Purge cannot be reversed by counting receipts or current completed tasks.
- P2 day-plan items expose live task state/estimates. The selected-estimate metric
  must define eligibility and show missing estimates; it cannot claim an old snapshot.
- P3 `workload()` counts open-task blocks and focus without blocks, uses interval
  unions, and suppresses old-day flags. Historical capacity adapters need explicit
  knowledge assessment rather than treating unflagged old days as complete.
- Migrations 0034–0036 are integrated. Allocate a new additive revision from the
  current head when schema work begins, and integrate strict recovery validation,
  portable owner exports and synthetic packaged restore with that schema.

## Verification

Verification is Windows source/offscreen unless explicitly stated otherwise.
The checkout has no local virtual environment; checks use the existing workspace
Python 3.13.15 environment with Pydantic 2.13.5 and PySide6 6.11.2, with this
checkout's `src` on `PYTHONPATH`. `uv.exe` cannot launch in the sandbox, so no
dependency installation or lockfile change was performed.

Initial focused run passed 62 tests. After the visual pass corrected cramped axis
labels and added an explicit bar-zero line, the broader domain/workspace/theme
run passed 955 tests, with one intentional redundant Completed-view render skip.
Final focused checks after additional precision/privacy tests passed 60 tests, with
95% combined statement/branch coverage across the five new application modules
(each module 95% or higher). The 2x chart/workspace/layout/theme run passed 320
tests with the same intentional render skip. The workspace's simulated-zone test
emits an existing host-offset warning; it passed at both scales.

Light/dark 1x and 2x renders were inspected for line gaps, signed bars, dense data,
long labels and number cards. Render output and safe logs stay in ignored
`scratch/p4-charts-*` and `scratch/p4-*.log`.

Final repository-wide Ruff formatting/lint, whitespace checks and strict
Windows/macOS-target mypy passed (545 source/test files; 705 files formatted).

## Milestones 1–4 verification and code review

The storage/recovery/data regression run passed 284 tests with one Windows symlink
skip. The backend lifecycle/query suite and code-review regressions pass, including
more than 200 activity records, out-of-range undo, event grouping, distinct versus
episode counts, concurrent retry/edits, cancellation, snapshot isolation, Other
membership, cross-range buffers, exact fractional durations and stale contributors.
The source package scenario verifies metric edit/Undo/retry, query, portable export,
backup/verify and separate-profile recovery using disposable synthetic records.

The full Windows source suite passed 5,459 tests with nine platform/capability skips
and 94.83% combined statement/branch coverage. Every service cleared the 90% gate.
After the final query-boundary/privacy review corrections, 72 focused lifecycle/query/domain/
review checks passed again; final coverage of the three new services is 98.59%
combined (each individually at least 96%). Final Windows/macOS-target strict typing
and repository formatting/lint passed (557 source/test/benchmark files checked by
mypy). Review scope and resolved findings are in
[the backend review](p4-backend-code-review.md). Reproduce the scale fixture with
`python scripts/benchmark_metrics.py` from the project environment; it uses a temporary
synthetic profile with 10,000 tasks, 100,000 activity records and 50,000 custom entries
and writes only safe timing/platform/source-hash metadata into `scratch/`.

The scale run completed on Windows 11 build 26200, Python 3.13.15, Intel family 6
model 186 stepping 2. Three repetitions produced these median individual-query
times: task creation 1.530 s, completion episodes 2.080 s, distinct completed tasks
2.174 s and custom-amount totals 1.858 s. Revalidated first-page contributor queries
took 1.43–2.22 s. Expected counts and exact sums matched the synthetic fixture.
Source signature: `8ca5fcbd765a0b3ea13b91c8757a1d9a0f11587ec0332aa1612697936a151860`.
These are engine timings, not a completed 12-widget dashboard benchmark. The
one-second first-dashboard target remains open: P4-05–09 must consolidate shared
reads/query work or add measured bounded reuse before claiming that target met.

The final Windows x64 single-file build passed offline full/restart scenarios at
1x and 2x, native Windows launch/restart, copied/renamed executable locking and
old-schema upgrade. Every full scenario also ran the synthetic metric lifecycle,
retry, exact aggregate, portable export, backup/verify and separate-profile restore
check. Fifteen affected embedded application modules and migration 0037 match the
final source. Executable SHA-256:
`660f8a351eee6126536785c28af1038b3ce4e80ccfb49efd991df12271a2508c`.
This is a local dirty-working-tree engineering build, not a published release.
Safe logs and final source-audit output are in ignored
`scratch/p4-package-*-final.log`, `scratch/p4-frozen-source-audit.json`,
`scratch/p4-final-backend-coverage.log` and `scratch/p4-metric-benchmark-final.json`.

No native macOS/Cocoa, assistive-technology, real-display or owner acceptance is
claimed. Five main P4 milestones remain (P4-05–09). No owner profile was migrated or
live account accessed for implementation/verification.
