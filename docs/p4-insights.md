# P4: Insights implementation register

Started 2026-11-08 on `feat/p4-insights`, from refreshed main
`7ecb649` (PR #109 merges P3 scheduling add-ons).
Status: the P4 dashboard and desktop implementation is now present locally, on top
of milestones 1-4. Acceptance remains open. This register separates the earlier
backend evidence from the dashboard increment's source/package measurements below.

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
| P4-05 | Migration 0038; revisioned singleton, validated atomic whole-layout writes, retry receipts, reset/repair, portable export and strict backup/recovery |
| P4-06 | Qt number/line/bar cards, signed zero baseline, gaps, daily/range targets, exact keyboard table, pointer/keyboard detail activation |
| P4-07 | Offline Insights/sidebar/palette, widget preview/editor, filters/group/range/zone/size, metric definition/archive and exact entry/correction manager; independent writes and quit guards |
| P4-08 | Revalidated keyset detail pages, canonical task/calendar opens, explicit stale refresh, mutation/day/zone refresh and bounded obsolete dashboard workers |
| P4-09 | Source, recovery, package, visual and performance verification recorded below; native macOS, assistive technology, real displays and owner acceptance remain open |

Insights is now a sidebar page and palette destination. Synthetic tests exercise it
without accounts, network, credentials, local models or owner data. No application
dependencies changed. The earlier component-only evidence below is historical.

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

That backend increment claimed no native macOS/Cocoa, assistive-technology,
real-display or owner acceptance. Its remaining milestones are addressed by the
dashboard increment below. No owner profile or live account was used.

## Dashboard increment: behavior and acceptance handoff

One saved layout holds at most twelve widgets. Add widget selects the metric, chart,

project/area filters, daily/category grouping, inherited or explicit date range/zone,

size, target and prior comparison. Preview has no write. Current-state metrics have

no historical comparison; incompatible choices explain their limits and service

validation still enforces them. Global dashboard dates start at the last seven local

dates and follow day rollover until explicitly changed. Widget overrides are saved.

Edit layout exposes Earlier/Later, Size and Remove. Removal and Reset offer Undo; a later layout change withdraws it, because the service

rejects an undo of an older revision. Whole-layout revisions reject stale editors

without losing typed input. Unknown layout schemas show a repair/reset message.

Manage metrics creates amount or point-value definitions with immutable unit, kind,

precision and sign policy. Archive/reactivate keeps existing charts/history. Entries

support exact decimal input, date/project, corrections, delete, restore and Undo.

Repeating an acknowledged identical log creates a new observation; retrying an

uncertain write reuses its command. Unsaved entry input cannot be replaced by another

row selection. Discard input is explicit. Dialogs are scrollable in short windows and show what a button answered under the

scrolled fields;

quit checks unsaved inputs and shutdown drains in-flight saves.

View data exposes exact bucket values. Contributing records revalidate the captured

query/fingerprint before every page. Source changes reset pagination with

?Data changed; refreshed.? Task transitions open canonical tasks; block/time rows

open the calendar. Custom observation corrections remain in Manage metrics. Interval

rows disclose contributing spans, working windows, busy/buffer subtraction and related

block identities rather than suggesting overlapping row values can simply be added.

Task/group/calendar writes use the existing committed-view refresh path. Insights also refreshes when reopened, on its minute tick and on owner day/zone changes.

A refresh that changes neither the layout nor the mode puts the fresh values into the

cards on screen: focus, an open data table, the selected value and the scroll position stay.

A dashboard project or life-area filter reaches only the metrics that can take it; the

others keep their whole value and say so in their caption. Planned workload starts today:

on the dashboard's dates or a widget's own, the part of the range before today is left out.

The date fields always hold an ordered range of at most 366 dates. Observations are listed

newest first.

Range changes retain the old cards while loading; obsolete reads cannot replace a

new generation. Layout resize repacks existing cards without rerunning queries.

No Gmail/AI provider is constructed by Insights, and owner saves have their own

sessions/tasks even while email AI is running.

### Requirement and scenario register

| IDs / scenario | Implementation / automated evidence | Acceptance state |

| --- | --- | --- |

| F02: exact amount/point logs | Existing metric lifecycle tests plus `test_insights.py` create/repeat/edit/delete/restore/Undo journey | Owner review open |

| G01: saved layout, order/size, retry, 12-widget cap | `test_dashboards.py` concurrency/retry/cancel/repair/recovery; UI reorder/remove/Undo/restart | Owner keyboard review open |

| G02/G03: cards, line/bar, exact table and gaps | Existing renderer galleries plus Insights light/dark galleries | Native assistive technology/real-display checks open |

| G05: query/filter/group/range/zone/preview | Typed boundary validation, preview cancellation, explicit-zone preservation and late-range tests | Owner explanation/usability review open |

| G06: target/prior comparison | Existing engine semantics; editor writes typed settings; range differences and daily target line | Owner review open |

| G07: detail agreement and Open | Captured fingerprint pagination, stale reset, exact contribution/interval table, canonical task/calendar routes | Owner source-open review open |

| G09: task/workload analytics | Existing history/DST/coverage/reconciliation tests; shared dashboard query matches individual query | Native/owner review open |

| Durability | Additive 0037?0038, strict reference validation, export, backup and separate-profile restore; package scenario extended with layout/target/retry | Real-record recovery open |

| Independence | Local facade constructs no provider; writes use separate sessions; tests use blocked external networking | Enabled-AI owner observation open |

### Dashboard verification

The locked environment was successfully created with `uv sync --locked --all-groups`.

The Windows `uv.exe` launcher required execution outside the sandbox; application

checks use the new checkout-local Python 3.13 environment. Dependency/lock files were

unchanged. All fixtures and exports used for verification are synthetic and disposable.

The full Windows source run passed 5,476 tests with nine platform/capability skips.
After the final performance changes, 48 affected lifecycle/query/UI regressions
passed in the checkout-local locked environment. Combined statement/branch coverage
is 93.25%; every service passes its 90% gate. Repository Ruff formatting/lint and
strict Windows/macOS-target typing pass (564 application/test/benchmark files).

The 1x and 2x Insights/chart/workspace/layout/theme runs each passed 329 checks,
with one intentional redundant-render skip. Both modes were visually inspected
against the Studio references. Fractional-scale real-display and native assistive-
technology acceptance remain open. The simulated-zone check emits the existing
host-offset warning; tests pass. Safe screenshots and logs are in ignored
`scratch/p4-final-gallery-*`, `scratch/p4-gallery-*` and `scratch/p4-final-coverage.log`.

The final Windows x64 single-file executable passed full/restart checks at 1x/2x,
native Windows launch/restart, copied/renamed executable locks and old-schema upgrade.
Every full scenario exercises metric lifecycle/Undo/retry, dashboard layout/target/
retry, exact queries, portable export, backup/verify and separate-profile recovery.
The Windows folder distribution also passed full/restart package checks at both
1x and 2x. Folder executable SHA-256:
`46dc5b559b697f248f0dee53a6a2a634886dbe4aec57ae13c98b0f55fdcd5434`.
Its safe log is `scratch/p4-insights-onedir-package.log`.
Nineteen embedded affected modules and migration 0038 exactly match final source.
Executable SHA-256:
`da637b3bf61f1df0268eebb6776fa11f6bb0a55f943043e853bfc16ed56b9b88`.
This is an unpublished engineering build from base commit
`fc088afc2a779084fe913580d9315405c97ed888` with the recorded working-tree source;
the build manifest marks it dirty. Source signature is below. Safe package and
embedded-source evidence: `scratch/p4-insights-package-final.log` and
`scratch/p4-final-source-audit.json`.

Native macOS/Cocoa, screen-reader, real-display/fractional-scale owner checks and
the eight owner scenarios in the implementation guide remain pending. No owner
profile was accessed or migrated. Automated/offscreen checks never mark acceptance.

### Scale fixture and limits

`python -m scripts.benchmark_insights` uses a temporary synthetic profile with 10,000
tasks, 100,000 activity records, 50,000 observations and twelve mixed widgets. It times
the actual Qt page and revalidated first detail page, measures process peak memory,
and uses a 10 ms Qt timer as an event-loop latency proxy. The timer starts after fixture
setup. These are Windows source/offscreen engineering measurements, not real-display
input or owner acceptance.

Reference: Windows-11-10.0.26200-SP0, Python 3.13.15, Intel64 Family 6 Model 186 Stepping 2, GenuineIntel.
First usable current-overdue card: 0.155 s.
Complete twelve-widget cold dashboard: 9.446 s;
warm median: 9.087 s; worst of three: 9.541 s.
Revalidated first detail page: 2.794 s.
Peak process memory: 655,745,024 bytes.
Qt timer p95/worst: 0.056/0.652 s.
Three dashboard samples do not establish a stable dashboard p95.
Source SHA-256: `9759b3e3c135c0906732d64e2f9244055fb324ccb186aaa875db1730cc3b6249`.

The current-state card is published while retained-history widgets load. Existing
cards remain visible during later refreshes. Identical snapshot reads, history
resolution, base contributions and source signatures are reused only within that
snapshot. Desktop materialization/reduction and large contribution-set release run
on a worker-owned engine/event loop; the GUI retains lightweight query/fingerprint
handles and prepared values. Detail revalidation and paging also run on that worker.
Repeated cancellation retains the worker slot until the old computation joins.

The initial stress run took roughly 15-17 s and peaked near 1.25 GB; the final run
roughly halves time and memory. The first usable current-state card is under one
second for this fixture, but all twelve charts still take around nine seconds and
the worst timer gap remains noticeable. Further large-profile tuning and native
input-latency validation remain open. Do not describe this as a one-second fully
loaded dashboard. Retained historical counts and current estimates remain distinct
from actual work or complete lifetime history.


## PR preparation review

The full branch received an author code review against main. All ten findings in
[the dashboard review](p4-dashboard-code-review.md) are addressed: exclusive saves,
metric/entry context, late reads, inherited preview filters/comparisons, rendered
layout identity/revision, acknowledged-write reloads, dialog lifetime, read cleanup,
keyboard focus and live Google Calendar scope. Twelve dedicated UI review
regressions cover these boundaries. No owner profile or account was accessed.

Final Windows full regression: 5,489 passed, nine platform/capability skips. Final
current-source follow-up: 43 focused UI/query/review checks and nine dashboard
service checks passed. Combined statement/branch coverage is 92.95%; every service
passes 90%. Ruff formatting/lint, whitespace checks and strict Windows/macOS-target
mypy pass (565 source/test/benchmark files). The latest full run preceded the final
keyboard-focus/calendar-scope integration corrections; the final focused run covers
both corrections and their new regressions, with unhandled thread warnings as errors.

The 1x and 2x chart/Insights/workspace/layout/theme galleries each passed 339 checks
with one intentional redundant-render skip. Saved renders remain under ignored
`scratch/p4-review-gallery-*`.

Both fresh Windows layouts passed full/restart package checks at 1x and 2x. The
single executable also passed native Windows launch/restart, copied/renamed lock
checks and old-schema upgrade. All nineteen affected embedded modules in both
layouts exactly match final reviewed source. The engineering artifacts are
unpublished builds from the working tree at base commit `fc088af`; CI will rebuild
the pushed commit. Package/source evidence is in `scratch/p4-review-source-audit.json`,
`scratch/p4-review-final-onefile-package.log` and
`scratch/p4-review-onedir-package.log`.

Single-file SHA-256: `3935fdc43d93b79e487d7d7f595413f9696ff908096a8dddc81f78b392923e16`.
Folder executable SHA-256: `6bcfc74de27ce5524cb85f97bed1099e205e8905ba10f0f064027eb4c89085a0`.

The final synthetic 12-widget benchmark on the same reference machine measured:
first usable current-state card 0.198 s;
complete cold dashboard 10.103 s; warm median
9.752 s; worst of three 10.128 s;
revalidated first detail page 2.962 s;
peak process memory 656,338,944 bytes; Qt timer p95/worst
0.047/0.915 s.
Source SHA-256: `d79ce7f44843233a6b2e7c1c47798ca4c0e4d2c92fab8a4ad1653df3ef20066d`.

This supersedes the earlier increment's benchmark for the reviewed source. It
remains a timer proxy and a three-run engineering measurement; fully loaded stress
dashboards take about ten seconds. Further large-profile tuning, native macOS/Cocoa,
real displays, assistive technology and owner acceptance remain open.

## Independent review and macOS UI check

An independent review of the merged P4 code and a UI check on macOS (2026-11-09) found
fifteen defects. All are fixed, each with a regression. The findings, their resolutions
and what was noted but left unchanged are in [p4-ui-review.md](p4-ui-review.md). No
migration, dependency or stored format changed.

Verification on 2026-11-10: macOS 27.2.1 (arm64), Python 3.13.15 and PySide6 6.11.2 in
the locked environment, synthetic records only.

- Ruff formatting and lint pass (732 files). Strict mypy passes for the native, Windows
  and macOS targets (565 source and test files).
- Full suite, offscreen with the Qt tests enabled: 5,532 passed, 7 platform skips and one
  failure that is not P4's. `test_day_window_matches_the_window_of_any_moment_that_day`
  for America/Havana expects a 25-hour day on 2026-11-01, and this machine's time-zone
  data gives 24; it fails the same way on `main`. Combined statement/branch coverage is
  94.64%, and every service passes its 90% gate.
- 35 new tests: 17 in `tests/ui/test_insights_qa.py`, 14 in the chart tests, 2 domain
  tests and 2 service tests.
- Native Cocoa. On 2026-11-09, before the fixes, the 44 Insights and chart tests passed
  there and the defects were reproduced there. On 2026-11-10 the desktop gave test
  windows no keyboard focus, so tests that wait on `hasFocus()` timed out: across the
  twelve affected UI test files, the same 28 failures and 17 errors on unchanged `main`
  (`f10618c`) and on the fix branch, name for name. The 17 new UI tests pass on Cocoa in
  that state, because they read each window's focus widget. CI's macOS job is the
  reference for the Cocoa step.
- The package's synthetic metric check (`check_metrics`) passes from source. No package
  was built here; CI builds and checks it.
- Light and dark renders of the dashboard, both editors and Contributing records were
  inspected before and after the fixes, offscreen at 1x and on Cocoa at 2x.

Not checked here: VoiceOver, a real display at a fractional scale, Windows and the owner's
own records. Large-profile timing was not measured again: the in-place refresh removes the
rebuild of the cards, not the cost of the queries.
