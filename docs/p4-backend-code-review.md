# P4 backend code review: milestones 1–4

Date: 2026-11-08. Scope: migration 0037, metric persistence/lifecycle, recovery/export/
cleanup, task and workload readers, reducers, snapshots, comparisons/targets and
contributing-record pagination. This is an author code review, not an independent
review or owner acceptance. The full P4 UI remains outside these four milestones.

## Findings and resolutions

| ID | Severity | Finding | Resolution and regression |
| --- | --- | --- | --- |
| R1 | Medium | Normal entry history reads checked current snapshots/revision continuity but did not enforce all the same operation/state relationships as backup validation. A malformed delete/edit state could become an Undo source. | Added shared `validate_entry_history` for both normal storage reads and untrusted recovery. It checks create/operation order, state transitions, correction targets, Undo-of-Undo and the final state. `test_inconsistent_change_operation_is_rejected_before_undo` rejects a fabricated delete without changing the entry. |
| R2 | Medium | Available-time inspection carried block IDs alongside working/busy intervals, even though task blocks do not subtract from availability. Combined unlabeled intervals also obscured the calculation. | Capacity now exposes separate working and busy/buffer intervals and no block contributors. Planned workload lists only contributing open-task reservations. `test_capacity_reads_buffer_crossing_the_range_start` adds a block and verifies capacity remains the hours-minus-busy result with honest interval details. |
| R3 | Low | Project lookup and per-day bucketing repeatedly scanned all input rows, adding avoidable cost at the design fixture size. | Built a snapshot-local project-area lookup and a single date-bucket index. Benchmark fixture and timing metadata are reproducible through `scripts/benchmark_metrics.py`; no cache or copied chart-value persistence was introduced. |
| R4 | Low | A range adjacent to the calendar's absolute endpoints could overflow the extra day needed for busy-event buffers. | Range validation reserves enough calendar space for padded interval reads. Ordinary DST/local-date behavior and existing date-limit cases remain covered. |
| R5 | Low | Programmatic query copies could bypass date-model validation, and a past forward-plan request was reported as invalid saved data. | Revalidate the whole query at the service boundary before reads and reject unsupported past planning with a specific static message. `test_programmatic_query_copy_cannot_bypass_date_limits` checks both query and comparison endpoints; final focused verification covers the corrected API. |
| R6 | Medium | Low-level SQLAlchemy write errors can include statement parameters, which may contain owner metric names or numeric values. | Normalize database write failures to `MetricStorageError` with suppressed exception context after rollback. `test_database_failures_never_expose_owner_values` verifies safe error text, suppressed driver context and no partial owner record. The final executable was rebuilt after this fix. |

## Review checks

- Confirmed all owner writes reserve/check revision and receipt before mutation,
  commit state/history/receipt atomically, and roll back on cancellation/failure.
  Receipts contain identifiers/revisions/hashes, survive purge and are not exported.
- Confirmed definition semantic fields cannot be edited in place, log values never
  pass through float, and saved evidence consists only of permitted owner numeric
  state and metadata. No mail bodies, credentials, account writes or AI calls are added.
- Confirmed aggregates resolve full correction chains before interval selection;
  soft-deleted history remains and purged history is not invented. Group identity
  is historical for transitions and current for current-state/plan metrics.
- Confirmed physical snapshot isolation for comparisons with an interleaved writer.
  Detail pages use the captured clock and exact query/fingerprint; changed sources
  invalidate the generation before returning rows.
- Confirmed source caps fail explicitly, Other retains exact membership, and distinct
  totals/union durations/point values use their own reducer rather than bucket sums.
- Confirmed P3 workload is reused; available time does not subtract task blocks;
  unset hours/failed/stale external knowledge remain unknown or provisional, including
  historical displays. Scheduled time is never called actual work.
- Confirmed the migration is additive on main's 0036 head and no older migration was
  edited. Schema/recovery inventory expectations advance to 0037. Package smoke adds
  lifecycle, aggregate/export and separate-profile recovery in a synthetic profile.

No unresolved backend correctness finding remains after these corrections. The
scale benchmark measures individual engine queries at roughly 1.5–2.2 seconds;
the one-second first-dashboard target remains an open performance gate for the
dashboard milestones, requiring shared reads/query reuse or bounded caching.
This increment does not claim that performance target met. Exact verification is
recorded in `p4-insights.md`. Native macOS, owner acceptance and the remaining P4
UI milestones are open gates.
