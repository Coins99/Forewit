# P1: task foundation consolidation

Started 2026-11-01 after the owner narrowed P0 to planner prerequisites.
Manual/email tasks, projects/areas, priorities/tags, checklists, recurrence, owner
deadlines, estimates and waiting commitments already exist. This phase verifies
their integration and fills demonstrated correctness gaps.

## First increment: effective task history

`ActionService.history(public_id)` reads the full stored history directly, including
soft-deleted tasks. It never counts the UI's latest 200 activity records. Missing or
purged tasks raise not-found; per-task history is not a lifetime count across tasks
that the owner permanently removed.

The domain resolver walks corrections backwards. An active undo removes its target;
undoing that undo restores the original event. Ordinary reopen retains the original
completion episode, and a later completion is a separate episode. Undo reopen neither
duplicates completion nor changes its original time. Historical project/area snapshots
remain attached to the original transition, independent of current grouping.

Malformed, forward, missing or multiply corrected undo targets fail rather than produce
an apparently reliable total. Legacy records, gaps and missing latest revisions report
partial coverage. This reader is read-only and uses the existing `action_activity`
table; no migration or parallel event store is added.

Tests cover correction chains, ordinary reopen/recompletion, original timestamps and
group snapshots, malformed/partial history, more than 200 entries, soft deletion/purge,
and restart/export/backup/restore for manual and email-derived tasks.

## Verification

On Windows, 41 focused history/lifecycle/organization checks and all 1,204 service/domain
checks passed. The isolated report has 100% statement/branch coverage for the history
resolver and 100% statement/98.7% branch coverage for the actions service. Its repository
overall threshold is inapplicable to that subset; a separate full regression enforces
the overall and per-service gates. Native, win32 and darwin strict typing passed, along
with source formatting/lint. Final full-suite and package results are recorded in the
[planner foundation register](p0-planner-foundation.md).

## Second increment: retry-safe task capture

Manual task capture now carries a stable command UUID through the editor/runtime/service.
Its receipt commits atomically with the task and activity. Same-input retries return the
original task's current state; different input and deleted/purged results are refused.
Receipt rows contain no owner text, survive backup/restore and outlive permanent task
purge. Existing callers without a command ID remain supported. See
[ADR 0024](adr/0024-retry-safe-task-creation.md) for retention and recovery semantics.

The cross-feature recurrence audit now also covers edited successors, project archival,
tag respelling, ordinary reopen/recompletion and replay of the original create command.
Proposal completion and undo are checked against effective completion history.
The existing per-feature recurrence cases remain the primary edge-case contracts.

Second-increment verification (2026-11-01, Windows):

- Full run: 4,931 passed, nine platform/render skips, 95.98% overall coverage. Every
  service meets the 90% combined statement/branch gate. The run had one stale schema
  inventory expectation and two 30-second subprocess timeouts under concurrent load.
  After adding the new table to the inventory, all 40 targeted checks passed serially,
  including both timed-out subprocess scenarios and the final receipt review tests.
- Focused retry/migration/recurrence checks: 50 passed. UI tests verify a stable ID
  after failure, a new ID for new capture, and preserved input on a command conflict.
- Final source Ruff lint/format and strict typing (native, win32, darwin): passed.
- UI gallery plus theme checks: 275 passed and one intentional render skip at each
  of 1x and 2x. Light/dark desktop, narrow layout and task editor renders inspected;
  this increment introduces no layout or styling change.
- Windows single-file package: lifecycle, 100%/200%, native Windows startup,
  persistence, renamed-copy locks, backup/recovery/export and old-schema upgrade
  checks passed against disposable data. The final build returned exit code 0 and
  its exact executable passed the checks again (`out/p1-retry-package-final.log`).

Final verified executable SHA-256:
`4a47bb32f6f0419ae73aab52476b4d83e5eb75b4d95132fdc3ece189bac1fe65`.
This is a local working-tree engineering build, not a published release.

Detailed safe output is under ignored `out/p1-retry-*.log`. No owner profile,
live account, credential vault, paid AI or model download was used. Native macOS
execution and owner desktop acceptance are not established by this Windows run.

## Coverage contract for later dashboards

`TaskHistory.complete` describes the retained history of one task, never the lifetime
coverage of the profile. Later aggregates must be labelled as based on retained records;
even complete histories for every remaining task cannot establish that no other task was
purged. Migrated history remains explicitly partial. Physical purge intentionally removes
those task records and their history; no completion totals may be reconstructed or
invented from current task status, UI caps or the remaining command receipts.

P4 must keep the time/group snapshots and correction semantics of the full reader and
make any missing coverage visible. This P1 increment introduces no aggregate endpoint
claiming lifetime completeness and no extra generic event store.

## Remaining P1 work

- P4 must implement aggregate coverage according to the retained-record contract above;
  this is a later dashboard query contract, not a second P1 history store.
- Native and owner acceptance for task capture, edit/complete/reopen/undo, recurrence,
  offline use, restart, export and separate-profile recovery on declared platforms.

The task-foundation engineering increments are implemented and verified on Windows.
P1 acceptance remains open for the native/owner checks above; no dashboard metric or
cross-platform owner acceptance is claimed.
