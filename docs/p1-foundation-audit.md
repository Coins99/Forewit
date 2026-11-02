# P1: task foundation consolidation

Started 2026-11-01 after the owner narrowed P0 to planner prerequisites.
Manual/email tasks, projects/areas, priorities/tags, checklists, recurrence, owner
deadlines, estimates and waiting commitments already exist. This phase verifies
their integration and fills demonstrated correctness gaps.

P1 implementation and automated platform verification are complete. The final code
snapshot passed Windows and macOS CI in [PR #98](https://github.com/Coins99/Forewit/pull/98).
Owner usability acceptance remains a separate, uncompleted check; see the closeout
record below. No live email or M9 release acceptance is implied.

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

## Closeout record (2026-11-02)

The final source snapshot is `e6ba5aca035bc51d18ca3f8065849f411b5f45fd`.
[CI run 41363314078](https://github.com/Coins99/Forewit/actions/runs/41363314078)
passed both platform jobs, including formatting, lint, strict typing, full tests,
coverage gates, native builds and offline package checks:

| Check | Final result |
| --- | --- |
| Windows full suite | 4,938 passed, eight skips; 96% rounded combined coverage |
| macOS non-UI suite | 3,310 passed, 1,636 skips; UI tests run separately below |
| macOS native Cocoa suite | 1,625 passed, eight skips; appended combined coverage 96% rounded |
| Required service coverage | Every required service meets 90% combined statement/branch coverage on both platforms |
| Native packages | Both desktop packages pass disposable offline startup/repeat-launch checks; Windows copied single executable also passes frozen lifecycle/recovery checks |
| Publication audit | Completed before push; personal commit contact metadata replaced with GitHub noreply identity; no remaining sensitive-data blocker found |

The latest UI fix selects the Completed view when retrying a create command whose
task has since completed, and identifies an already-skipped result without claiming
a new task was created. The focused UI/receipt run passed 54 tests before full CI.

### P1 scope and evidence

| Scope | Implemented and verified behavior |
| --- | --- |
| B01 capture/lifecycle | Source-free and accepted-email tasks share editing, completion, reopen and undo; atomic activity, optimistic revisions and retry-safe creation |
| B02 organization | Projects/areas, archive/reassignment, historical grouping snapshots and recovery/export |
| B03 priorities/tags | Ordering and filtering, normalized tags, respelling and combined recurrence checks |
| B04 checklists | Ordered steps, revisioned in-place edits; checking all steps does not complete the task |
| B05 recurrence | Daily/weekly/monthly rules, skip/reschedule, undo and edited-successor conflicts, one open occurrence, restart/recovery/export |
| B06 dates | Owner deadline separate from target date and email evidence; date/time-zone semantics preserved |
| B07 estimates | Owner minutes independent of AI effort, validation and recovery/export |
| B08 waiting | Waiting/person/commitment fields, elapsed waiting and due follow-up, revisioned lifecycle |
| Later-stage history contract | Uncapped effective completions with original time/group snapshots and explicit partial coverage; ordinary reopen differs from undo |

The cross-platform suites include the lifecycle/restart/export/restore tests in
`test_manual_tasks.py` (manual and email-derived), `test_projects.py`,
`test_recurring_tasks.py` and `test_task_commands.py`, plus the corresponding UI tests.
This satisfies the automated P1 gate without Gmail, a paid AI call or local-model setup.

Agent-operated native Windows checks also exercised keyboard capture, Unicode and
multiline notes, priority/tags, person, a 90-minute estimate, an owner deadline,
in-place checklist completion, task completion, reopening and Undo reopen. A read of
the disposable profile confirmed the original completion timestamp was restored,
the checked step remained checked and exactly one creation receipt existed.
The profile blocked external network and credential access and contained only
synthetic data. It was excluded from publication.

The desktop automation helper subsequently became unavailable (native pipe missing),
including after retry and kernel reset. Further hands-on recurrence/organization,
restart and recovery checks were not completed in that session; their automated
Windows/macOS and package checks passed as recorded above. Agent checks are not owner
sign-off or accessibility/display acceptance on the owner's supported machines.

### Outstanding acceptance and later work

- Owner usability checks in the per-feature P1 documents remain unrecorded. The
  implementation is ready for that review; the PR remains a draft and main is unchanged.
- P4 must implement aggregate coverage according to the retained-record contract above;
  this is a later dashboard query contract, not missing P1 storage.
- Deferred local-model quality/native acceptance and M4–M9 live email/release checks,
  including the real-record recovery trial, retain their existing status. They do not
  block planner implementation under the owner's narrowed scope.

No P1 implementation item remains open in this audit. Human acceptance remains open;
no dashboard metric, live-mailbox acceptance or cross-platform owner sign-off is claimed.
