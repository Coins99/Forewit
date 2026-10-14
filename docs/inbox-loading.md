# Bounded Inbox loading

The Inbox previously displayed local 100-message pages after scanning every provider
page. Startup, sign-in, Refresh Inbox and daily workflows could therefore wait on a
large historical Inbox before displaying fresh metadata.

Interactive browsing now reads a single provider page: up to 50 IDs initially and
up to 100 per **Load 100 more** action. The latest cached 50 messages appear before
the fresh request. There is no date cutoff. Daily briefs keep their complete local-day
sync, without a preceding all-date Inbox scan.

## Implementation

- `domain/inbox.py` and `ports/inbox.py` describe a normalized metadata page and its
  bounded reader. The Gmail adapter validates page size, IDs and cursor before reading
  metadata, keeping the existing five-request concurrency and cancellable retries.
- `services/inbox.py` commits metadata and directly observed removals. It never
  reconciles messages simply because they were absent from a limited page. The whole
  page fetch has a 180-second deadline in addition to individual request limits.
- `ui/runtime.py` advances the browsing cursor only after persistence succeeds.
  State is isolated by account and query. Failed metadata retries the same page;
  repeated cursors are rejected. Generation guards discard results after sign-out,
  and reconnect starts a new browsing session. Authentication loss clears Google
  access and prevents downstream automatic or connection workflows from continuing.
- The initial cached page is a preview: the first successful provider page replaces it
  in the display, without deleting unobserved cached records. Newer stale cache entries
  cannot crowd out older current-Inbox messages after the remote cursor advances.
- The Inbox appends and deduplicates loaded entries, preserving selection and scroll.
  Refresh starts from the latest page while retaining previously loaded Inbox entries.
- All-mail search submits an explicit Gmail query without the INBOX label restriction,
  with its own pagination state. Non-Inbox results are labelled. Trash/Spam metadata
  is discarded, and neither browsing nor search downloads bodies or attachments or
  calls AI. The existing command palette continues to search local saved records only.
  Only Inbox and the active search retain browsing history in memory.

Browsing state is deliberately in memory. SQLite already persists normalized metadata;
restart displays its cached first page and refreshes from latest. Persisting a remote
cursor across restarts would require extra transient schema/backup lifecycle work without
improving startup. No dependency, OAuth scope or schema change is required.

The full-scan service and persistent full-scan checkpoints remain separate and retain
their complete-enumeration reconciliation rules.

## Limits

A page may display fewer messages than its ID limit because mail can disappear, leave
Inbox, overlap an earlier page or fail metadata validation. It never fetches a second
provider page just to fill gaps. Failed first-page metadata retries the 50-ID request.
Successful pages are ordered by received time and a deterministic local ID tie-breaker;
this sorts loaded records without claiming a globally sorted snapshot during Gmail changes.

Unvisited cached entries may have stale membership. Directly observed labels update
membership, and the UI describes older saved entries as potentially stale. A continuous
Gmail history-based change feed would be a separate extension. Expired cursors can be
restarted with Refresh. Live Gmail latency and ordering still need owner acceptance.

## Verification

New mocked-provider and desktop tests verify request limits, separate all-mail search,
deduplication, cached display before delayed network responses, cursor retries, failed
commits, cancellation, wrong accounts and sign-out during a request. Existing full-scan,
daily-workflow, ranking-mark and command-palette tests remain applicable.

Offscreen screenshot galleries are rendered and inspected in both modes at 1x and 2x.
A local synthetic display measurement (Windows, offscreen Qt, concurrent test load)
recorded approximately 10 ms for 50 rows, 17 ms for 1,000 and 70 ms for 5,000.
These numbers measure rendering only, not real Gmail or end-to-end startup latency.

Local validation on 2026-10-13:

- Ruff lint/format and mypy for Windows and macOS pass.
- Full regression run: 4,012 passed, four platform/intentional skips, and one recovery
  subprocess failure. The shared Python environment's editable install pointed at the
  older `MailBrief` checkout, so that subprocess rejected the new schema. Setting
  `PYTHONPATH` to this worktree's `src` corrected the test environment without changing
  application code. The recovery module then passed 70 tests with one platform skip.
- All other subprocess-test modules and the final loading/search, Inbox, command-palette,
  Gmail-page and sync checks passed together: 140 tests.
- Combined statement/branch coverage is 95.55%; every service meets the required 90% gate.
- Main-window/workspace/Inbox galleries passed 103 tests at each scale (one intentional
  skip each). Search galleries and browsing tests also passed at both scales.

Run `uv sync --locked --all-groups` in this checkout for its own environment. When
borrowing another checkout's Python environment, set `PYTHONPATH` to the absolute
`src` directory here so subprocess checks use the same source as pytest.

Review validation on 2026-10-14:

- Full suite with the correct source path: 4,035 passed, four platform/intentional skips.
- After the final total-metadata-failure guard, the affected desktop, runtime, provider
  and sync tests passed: 270 tests. Cached display and the retry cursor remain intact
  when every metadata request fails.
- Combined coverage after the final rerun: 96.47%; all service modules meet 90%.
- Ruff lint/format and Windows/macOS mypy checks pass.
- Screenshot checks passed 126 tests with one intentional skip at each scale; Inbox,
  all-mail search and workspace renders were inspected in light/dark at 1x and 2x.

Findings and fixes are recorded in [the review](inbox-loading-review.md).
