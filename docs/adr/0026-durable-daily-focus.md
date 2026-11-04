# ADR 0026: Daily selection is independent owner data

Date: 2026-11-03

Status: Accepted for P2-03 implementation; owner acceptance remains open.

## Decision

Keep one `day_plans` row per local date in this profile, with a public identity,
creation IANA zone, revision and UTC timestamps. An empty read creates nothing.
Changing zones can change the current date; it never renames an existing plan.
`day_plan_items` hold stable public identities, canonical occurrence IDs and dense
positions. Migration 0028 follows the note-create receipts migration 0027.

Selection does not change task dates, priority, estimates or lifecycle history.
Select, remove and reorder require the displayed revision, including revision zero
for a missing plan. Actual edits increment it once; no-ops do not. Unique date,
plan/task and plan/position constraints back the service contract. Revision checks
are flushed before membership writes. Reordering uses temporary positions outside
the current range, then final positions in the same transaction.

Selections project current task state. Completed work stays visible; deleted work
is unavailable until restored. Repeating selections retain their exact occurrence.
A selected successor counts as owner work and is retained when undoing an earlier
completion, just as an edited or drafted successor is retained.

Permanent deletion removes references, normalizes positions and increments each
affected plan once in the cleanup transaction, even across batches. It keeps the
plan identity and date. Backups retain both tables; portable writing exports include
them as owner data. Cache cleanup does not remove plans.

## Consequences

My focus can work offline without Gmail or AI. Execution workload sums estimates
only for live open tasks owned by the owner, counts unknown estimates separately,
and labels waiting selections as follow-ups. Completed/deleted work contributes
no remaining workload. Selection is not an actual-work or completion fact.

The service caps a plan at 100 items and the chooser returns at most 50 open task
matches. These bounds do not limit ordinary task creation. Daily selection does
not automatically carry forward, reschedule tasks or create calendar blocks.
