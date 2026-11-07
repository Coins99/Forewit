# ADR 0031: Local suggestions on the merged Calendar foundation

Date: 2026-11-06. Status: implemented on the add-ons branch; owner acceptance open.

The merged P3 PRs (#105–#107) and flat controls (#108) are the base. Preserve their
task ownership, Busy/Free behavior, Day/Week/Month/List views, direct block editing,
5-minute–12-hour block limits, 50-live-block task limit and daily workload figures.

## Additions

- Working hours gain dated replacement windows (including an empty day) and separate
  before/after buffers, 0–120 minutes each. Dated windows use main's quarter-hour,
  three-window validation. Existing symmetric buffers initialize both sides. The saved
  owner time zone governs these wall-clock rules, as it governs weekly hours on main.
- Blocks gain explicit locking. Unlock before moving, resizing or unscheduling; a
  lock change increments the block revision and invalidates outstanding batch Undo.
  Locking never changes the task. Suggestions leave every existing block in place.
- Google read attempts remain available for retry throttling, but successful coverage
  separately records range, zone, freshness and generation. A failed/unavailable read
  retains cached busy records and does not publish empty time. Malformed or capped
  provider results cannot establish complete coverage. Restore forgets this cache state.
- Suggest time runs locally without a provider, AI or account. A bounded 1–42-date
  request contains up to 100 tasks and proposes at most 200 new blocks. Stable ordering
  is priority, deadline, optional focus position and task identity. Splitting is opt-in,
  up to four pieces, with an explicit minimum and small-final-piece choice. All pieces
  must fit before a task is proposed. Unknown demand and repair needs remain explicit.
- Credit is valid future planned time, including selected tasks' reservations outside
  the requested dates; it never represents actual work. Conflicted reservations need
  repair before the planner adds more. Remote Busy/Free and shared conflict policies
  govern availability. Daily Calendar/Today workload remains its existing read model;
  the suggestion review adds selected-range demand and limitations.
- Suggestions start at least five minutes after the captured clock. Review is read-only;
  apply requires explicit whole-task selection. Under a SQLite writer reservation,
  apply rereads the coherent inputs, compares the review fingerprint, independently
  checks placement constraints and current time, then writes blocks and the receipt
  atomically. Unknown/stale/incomplete external knowledge needs a current acknowledgement.
- Batch Undo is bound to its original apply receipt and every result revision. Manual
  commands also have durable receipts. Retrying the same identity/input confirms the
  result; changed input, changed results or purged results are refused. Receipts retain
  control identifiers, revisions, hashes and previous move times, never owner text.
  They survive backup and purge but are omitted from writing exports.

## Schema and development-candidate compatibility

Merged revisions 0032/0033 are immutable. New revisions 0034–0036 add successful
coverage, batch receipts, dated hours, buffers, locks and manual receipts. Old coverage
attempts are not promoted to successful reads during upgrade.

The unmerged `feat/p3-calendar` development candidate reused 0032/0033 for different
schemas. Startup detects its `work_blocks.task_public_id` shape and refuses a normal
upgrade. `python -m scripts.convert_p3_candidate SOURCE DESTINATION --owner-zone ZONE`
creates and validates a separate copy, preserving task/block identities, locks and
receipts, mapping normalized hours to main's hours model and clearing Google caches.
The explicit zone must match saved candidate hours and any saved owner zone. Incompatible
window counts/quarter-hour rules or block durations outside 5 minutes–12 hours fail
validation; reconcile them in the old candidate
before converting. The source is never changed and an existing destination is refused.
The converter handles a known development format, not arbitrary SQLite imports.

## Verification and acceptance

See [the add-ons register](../p3-scheduling-addons.md). Synthetic tests, source checks
and package checks establish engineering evidence only. Native macOS, assistive
technology and the owner's ordinary-week acceptance remain separate.
