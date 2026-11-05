# P2 workspace implementation

P2-04 through P2-09 extend the navigation, capture and focus foundation on
`feat/p2-workspace`. Forewit is the display name; the `mailbrief` package, owner
profile paths, vault names and portable format names are unchanged. This document
separates implementation from platform and owner acceptance.

## Owner workflow

- Today leads with My focus, its known estimates and unknown/waiting counts,
  appointments and other commitments. The full brief lives in Mail. Open Mail and
  Review yesterday are explicit; displaying saved owner records does not run AI.
- The sidebar is Today, Tasks, Projects, Calendar, Notes, Mail. Existing Ctrl/Cmd
  shortcuts retain their meanings: 1 Today, 2 Inbox, 3 Tasks, 4 Waiting, 5 Mail
  drafts, 6 brief history, 7 Calendar, 8 Notes. Ctrl/Cmd+K includes these routes; capture
  remains Ctrl/Cmd+Shift+N. Mail's view chooser includes saved metadata.
- Notes uses the existing writing records, versions, editor and export commands.
  Mail drafts excludes notes. A note can have one optional project and its existing
  optional task link; changing links checks the note revision and never creates a
  writing version, fetches an email or changes AI permission. Project context shows
  canonical tasks and notes. A task's writing menu opens its linked notes; All notes
  clears that filter. Search opens the same canonical note and reveals it.
- Capture's Review later choice saves an ordinary usable task/note to the optional
  review inbox. Unchecking it saves and keeps the record immediately. Tasks and Notes
  open type-filtered review views, with a combined view available. Keep as is requires
  no project, date or estimate and changes processing state transactionally. Unassigned
  records are not implicitly inbox records. Older captures remain legacy/non-inbox.
  Review retains up to 100 pending tasks and 100 pending notes; each selected view
  applies its 100-row display limit after filtering, so newer tasks cannot hide
  pending notes (or the reverse).
- Upcoming reads seven local dates, expandable to thirty. Task target/deadline reasons
  share one canonical task row; local and Google events retain separate identities.
  Meeting occupancy is the union of clipped timed intervals in UTC; all-day events,
  selected task estimates and Google coverage/freshness are separate. This is not an
  available-capacity or working-hours calculation.
- Plan today has a date picker and Go to today. A deliberately browsed date survives
  midnight/refresh. Past plans show current task state, not historical completion
  reports. Carry or move selections opens explicit, revision-bound previews. Copy
  keeps source membership; move changes both plans atomically. Existing destination
  selections are deduplicated, exact recurring occurrence IDs are retained, and no
  task dates, deadlines or completion facts are changed. There is no durable transfer
  undo log; ordinary select/remove/reorder remain available.
- Search includes project names/descriptions and saved brief summaries/actions and
  permitted metadata. Every term may match a different item of the same saved daily
  brief. Cached analyses outside saved briefs are not brief hits. Brief hits are
  rechecked against the connected account when opened. Query bounds and literal
  wildcard behavior are unchanged; no bodies, network, embeddings or FTS are added.

## Persistence and recovery

PR #101 supplies the Notes editor, project filter, searchable task picker and separate
revisioned project/task setters. This branch extends that foundation with Projects and
task context; the earlier combined link dialog and mutation have been removed.

Migration 0029 adds the nullable note-to-project foreign key. Migration 0030 adds a
bounded capture origin and optional processed UTC timestamp to tasks and writing.
Existing rows backfill to `legacy`, outside the inbox. Neither migration changes
writing text, versions, task links or deadlines. Project deletion detaches its foreign
key without deleting writing. Task soft deletion hides its live link while retaining
the writing and title snapshot.

Revision `20261105_0031` converges the two published histories: #101's
`20261104_0029` and #102's `20261103_0029` / `20261103_0030`. Their original files
remain unchanged. The online migration environment wraps only the two duplicate
note-column steps: it validates and adopts an existing matching foreign key, adds
#101's index and notes-only constraint when needed, and records both ancestors.
Downgrades keep the shared column until neither branch owns it. Fresh databases and
profiles from all three published branch revisions are tested through repeated
upgrade/downgrade cycles with note text, versions, sources and project links retained.

Quick-create receipts include an explicitly supplied capture processing payload in
their fingerprint. Omitted payloads retain the earlier command fingerprint; uncertain
retries return the current canonical record, including later processing. Different
input is refused. Processing does not create a writing version. Task processing records
an edit, never a completion. Optimistic revision checks also protect independent-session
task/writing changes.

The existing strict backup schema verification includes the converged schema. Owner JSON
exports retain the new fields in existing `mailbrief-writing` version 1 record objects;
this is an additive export, not an import/replay protocol. Cleanup fingerprints include
these fields automatically. Owner relationships and capture state survive account/cache
cleanup, restart and separate-profile recovery. Control receipts remain excluded from
portable owner writing exports. Restore retains its permission-clearing behavior.

The package upgrade scenario seeds the older shipped schema directly, then migrates it;
it does not use the current ORM to write columns absent from that schema. The original
pre-upgrade database is retained. Existing shipped migrations are unchanged.

## Verification register

CI repair after `428a58a` (2026-11-05): Windows passed all 5,134 tests and the service
coverage gate, then its frozen-app check exposed the dynamic Alembic environment's
missing `mailbrief.storage.migration_forks` import. The build now explicitly includes
that module for both platforms and Windows layouts. A fresh Windows directory build
passed the full offline package/recovery checks at 1x and 2x. Cocoa's three mail Tab-order
failures came from the selector's default Tab-only policy in text/list navigation mode;
the selector now takes StrongFocus. A regression exercises that mode and combo default
on every platform. All 33 focused build/keyboard/workspace/app checks, Ruff and strict
native/win32/darwin mypy passed. Native Cocoa confirmation runs in CI.

Capture-filter review fix (2026-11-05): 42 focused capture/workspace/recovery/source-rule
checks passed; the capture service has 98.15% combined statement/branch coverage.
Regressions cover an older pending note behind 101 newer tasks, the reverse case,
switching all three views and opening the correct canonical record. Light/dark
inbox renders were inspected at 1x and 2x (four checks passed at each scale).

Integration after merged PR #101 (`main` at `50d0a22`, 2026-11-05):

- Full suite: 5,126 passed, 9 platform/environment skips. Three stale schema/sidebar/
  Notes-page expectations were corrected; all eight targeted reruns passed.
- Coverage: 95.34% combined statements/branches; every service passed its 90% gate.
- Ruff formatting/lint and strict native, win32 and darwin mypy passed.
- Migration/recovery closeout: five checks passed, including backup/restore and repeated
  upgrades/downgrades from both 0029 revisions and capture revision 0030. Original
  migration files are unchanged. Note text, versions, sources and project links survive.
- Light/dark galleries rendered and inspected at 1x and 2x; the final 2x run passed
  132 checks (one duplicate-render skip). The task-context/global-note navigation
  regression passed separately, retaining pending writing and revealing the opened note.

Closeout record: 2026-11-05. Synthetic records only; no real credentials, mailbox,
cloud AI or owner writing were used. The recorded local checks and packaged artifact
used an uncommitted working tree based on `21cf266` on `feat/p2-navigation`.
Publication moves the remaining P2 changes to `feat/p2-workspace` on current `main`;
CI verifies that PR head separately. This is not an owner-accepted release.

| Check | Status |
| --- | --- |
| Capture payload replay, processing revisions and writing version fidelity | Passed |
| Carry/move deduplication, stale preview and transaction rollback | Passed |
| DST 23/25-hour boundaries, interval union and event/task identity | Passed |
| Project and multi-item brief search, account isolation and detached analyses | Passed |
| Separate-profile backup/restore, exports, links and receipt replay | Passed |
| Ruff formatting/lint; strict native, win32 and darwin mypy | Passed |
| Publication integration with current main (`7f7f9cb`) | Rebased cleanly; preserved main's flush focus-card geometry in the new Today container; 228 focused UI/theme/layout checks passed |
| Final full functional suite, including the Today optimization | 5,076 passed, 9 platform/environment skips; a subsequently added linked-note route test also passed in the 5-test UI closeout run |
| Full coverage snapshot before the final Today read optimization | 5,075 passed, 9 skipped; 95.08% overall; every service met the 90% combined statement/branch gate |
| Final Today optimization and related workflows | 18 focused tests passed; Today service 100% statement/branch coverage; no task-edit timeline SQL in the Today read |
| Canonical linked-note route and settled dialog visibility | 5 focused UI tests passed |
| Light/dark pages and new dialogs at 1x/2x | Passed and visually inspected; includes capture inbox, note links and transfer controls. Broader gallery/theme checks: 279 passed per scale |
| Fractional-scale source gallery | Light/dark pages and dialogs passed and were inspected at 125% and 150%; 2 gallery scenarios per scale |
| Windows x64 one-file package | Passed both offscreen scales, native Windows launch, copied/renamed executable locks, separate-profile recovery/export/cleanup and preserved-profile upgrade |
| Native macOS package/Cocoa and owner hands-on fractional scaling | Open; source typing/render checks do not establish these |
| Large-profile service latency probe | Measured below; cold window-display and enabled-AI isolation measurements remain open |
| Owner ordinary-use capture/next-task/writing trial | Open |

The checked Windows executable is `out/dist/onefile/Forewit.exe`, SHA-256
`af508a69eb70e199412068c51729ca10004c7118c3d445657b557faba8c73b2c`.
Its adjacent manifest records Python 3.13.15, Windows 11 x64, the lockfile hash,
base commit and dirty-tree status. Package verification used disposable profiles
only; it did not launch against the owner's profile.

### Large-profile investigation

On FLYINGBRICKS (Windows 11 x64, Python 3.13.15), the synthetic fixture contained
10,000 tasks, 100,000 activity rows, 1,000 notes, 365 saved briefs, 20 cached messages
and 20 analyses. Each brief referenced those 20 synthetic messages; the probe does
not represent 7,300 distinct emails. Each run took 30 samples and created another
30 notes. Notes held roughly 1,900 characters each.

| Service operation | Before p95 | After p95 |
| --- | ---: | ---: |
| Today summary plus all saved note summaries | 15,609.96 ms | 3,014.96 ms |
| Local search for `budget`, including saved briefs | 167.74 ms | 107.70 ms |
| Atomic initial note commit with receipt and review state | 35.45 ms | 23.08 ms |

The investigation found that Today unnecessarily hydrated task-edit timelines.
It now reads current task state and thread activity without those timelines; opening
the canonical task still loads its history. A regression test inspects executed SQL
to ensure the Today read does not fetch `action_activity` for an unselected day.
Focus selections and task history semantics are unchanged.

These are service probes, not window-display or cold OS-cache timings. Other
validation processes were running, so the before/after observations are not a
controlled hardware benchmark. The first optimized owner read was 2,011.04 ms and
its median was 1,903.15 ms. The proposed one-second investigation threshold remains
exceeded at this size; further bounded owner-read work and a cold/warm window trial
remain follow-up performance checks. Search debounce and UI rendering are not
included in the search measurement. No available-capacity or AI-quality guarantee
is implied.

Passing platform typing does not establish native macOS usability. Owner acceptance
and the unrelated M4–M9 email/AI acceptance trials are not implied by this implementation.
