# ADR 0032: Exact metric contracts and prepared-data charts

Date: 2026-11-08. Status: contracts, logs, query engine, durable dashboard and
Insights editors implemented. Native platform, accessibility and owner acceptance
remain open; see the P4 register for exact engineering evidence.

## Context

P4 adds F02, G01–G03, G05–G07 and G09 from `future-implementations.md`. The
task foundation already records effective activity with revision-based corrections;
P3 already defines interval unions and current workload. Metrics must consume
those sources without introducing competing history or scheduling semantics.

## Decisions implemented in this increment

- `domain/metric_entries.py` parses canonical decimal text directly to scaled
  integers, at scale 0–6, bounded to ±10^15 per entry. No silent rounding, binary
  float, exponent notation or locale grouping enters the domain boundary. Aggregate
  integer-to-Decimal conversion is independent of the ambient Decimal context.
- Definitions distinguish amount and point-value measurements and declare a negative
  value policy. Definition units/kind/scale will be immutable when persisted.
  Entries have owner local dates and a provenance zone; display-zone changes must
  never move date-only observations.
- The built-in registry declares calculation, supported dimensions and range mode.
  Query validation rejects unsupported reducers, versions, filters and grouping.
  Point-value definitions expose latest observation, never sum. Capacity has no
  project filter; unique occupied time has no additive project breakdown.
- Inclusive UI date ranges have at most 366 dates and DST-aware half-open instant
  bounds. Previous periods contain the same number of local dates.
- Layout is a versioned ordered tuple with at most 12 uniquely identified widgets.
  Number cards are scalar; categories use bars. Current-overdue cannot be a daily
  line or have an invented prior-period comparison. Daily targets are explicit and
  require daily charts. The later persistence boundary will use one dashboard revision.
- Prepared displays hold an explicit total separately from bucket values. Distinct
  totals are not reconstructed by summing daily counts. Missing samples remain
  unknown; measured zero remains zero. Decimal text is exact in tables and labels.
- `ui/metric_chart.py` is an existing-Qt feasibility component, outside navigation.
  It paints bounded line/bar data, breaks lines at missing samples, marks bar zero,
  shows optional daily targets, supports arrow/Home/End selection and Enter, and
  offers an equivalent keyboard table with stable source keys. Only coordinates
  and approximate axis labels use floats. Tokens/fonts/buttons follow the UI skill.

## Durable logs and query implementation (milestones 1–4)

Migration `20261108_0037` adds definitions, current entries, append-only structured
entry states and text-free command receipts. Every mutation reserves SQLite's writer
before checking the expected revision or receipt, and commits the owner record,
change and receipt together. A retry confirms only the unchanged result; conflicting
input, changed results and purged results are refused. Definition semantic fields
are immutable. Archive preserves logs and existing query access. Entry deletion is
soft; permanent deletion requires the existing previewed owner cleanup and retains
receipts. Normal history reads and backup verification share the same correction
protocol validator. Portable exports include logs/history and omit receipts.

`MetricService` opens a dedicated physical SQLite read transaction, materializes
local inputs and computes immutable results on a worker thread. Task queries batch
the full retained history rather than use UI lists or prefilter correction chains
to the displayed range. Event project/area IDs are snapshots, with current labels.
Legacy/gapped histories are partial, and retained totals never claim profile lifetime
coverage. Malformed correction graphs fail the metric instead of being skipped.

Custom logs reduce exact scaled integers/Decimals; point values use original
observation ordering and dates. Scheduled durations retain exact integer microseconds
and are summed before conversion to decimal minutes. Union-time details retain
their contributing blocks. Selected estimates use retained task-days and current
estimates/status. P3 workload remains open-task scheduled working time plus unscheduled
focus estimates; selected estimates are never added again to scheduled time.

Availability reuses P3 interval rules, pads event reads for cross-boundary buffers,
and assesses successful external knowledge independently for every requested day,
including old dates. No hours means unknown capacity; no external integration means
explicit local-only scope. Task blocks never reduce the available-time metric.

Comparisons read both periods from one snapshot, use matching local-date counts and
label in-progress/full-period comparisons. Undefined/nonpositive-prior percentages
remain unavailable. Targets preserve units and distinguish range from daily scope.
Each result fingerprints its canonical query, captured clock, source revisions,
contributions and coverage. Contributors reread using the captured clock and reject
a changed generation before paging. An explicit source-row limit fails the query;
it never truncates an aggregate. Up to 20 named groups plus inspectable Other are
rendered; category membership remains exact.

## Limits and follow-up

The dashboard is a singleton SQLite row guarded by one revision. Migration 0038
adds it and separate text-free retry receipts. Whole-layout writes validate every
widget against its resolved definition, reserve SQLite's writer, and commit the
layout/revision/receipt together. Conflicts preserve editor input; unsupported saved
schemas expose an explicit reset state. Removal/reset Undo requires the exact revision
it produced, so later layout edits cannot be overwritten. Backup validation checks
configurations and references; portable exports include layouts and omit receipts.

Insights has a canonical page/palette route without changing the eight shortcuts.
Queries, definition/entry saves and layout writes use independent sessions and do
not join the window's AI queue. One dashboard snapshot shares complete task-history
reads, pure history resolution, base contributions and input signatures; it never
persists a copied aggregate. Desktop source materialization and calculations run on
a worker-owned engine/event loop; no ORM/session objects cross loops. The GUI retains
query/fingerprint handles, while detail revalidation and paging stay on the worker.
A current-state card can appear while the retained-history widgets load. Obsolete dashboard reads discard late results and retain their
worker slot until cancellation has joined the worker. Range refresh keeps existing
cards until replacement; resize repacks prepared widgets without reading storage.

Charts use the prepared-data Qt painter/table component: exact accessible tables,
keyboard and pointer inspection, gaps, zero baselines and neutral target differences.
Editors expose units/precision/date/project/corrections and preserve explicit widget
zone/range overrides. All owner text stays plain. Dialogs use DialogGuard and the
existing quit guard; shutdown drains pending saves before closing the database.
Current retained plans do not prove historical workload or actual work performed.
No account/provider is accessed by metrics, no processing permission changes and
no dependency is added. The package check includes a separate synthetic metric
profile with lifecycle, retry, aggregate, export and restore verification.

The renderer remains subject to platform package, assistive-technology and
real-display checks. Offscreen painting and accessible names do not establish
screen-reader acceptance. See `../p4-insights.md` for current verification.
