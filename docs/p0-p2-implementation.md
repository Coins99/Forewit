# P0–P2 implementation progress

## Owner scope decision: planner prerequisites (2026-11-01)

Local AI is experimental and disabled by default. Explicit opt-in uses
`MAILBRIEF_EXPERIMENTAL_LOCAL_AI=true`; saved local mode never falls back to cloud.
This flag enables availability only, not consent, downloads or automatic processing.
Revocation, file removal and reading saved records remain available with the flag off.

The P0 gate for P1–P4 is now limited to data integrity, additive migrations,
revision/conflict protection, transactional history, backup/export/restore and
usable owner-record workflows with AI unavailable. Verify relevant Windows/macOS
behavior on each supported platform as those planner increments ship.

Local prompt quality, native model acceptance, progressive AI polish, durable AI
queues/resource policy, closed-app scheduling and selective cloud enrichment are
deferred experimental email work. They do not block P1–P4. The email-specific
7–14-day trial also remains separate from planner completion. Existing email
acceptance claims remain pending; this decision does not mark those checks passed.

Execution checklist and evidence: [planner foundation](p0-planner-foundation.md).
P1 continuation: [task foundation audit](p1-foundation-audit.md).
The increment records below describe earlier implementation work and evidence.

2026-11-02 closeout: P1 implementation and automated Windows/macOS platform gates
passed in [PR #98](https://github.com/Coins99/Forewit/pull/98), including atomic
create retries, effective history, integrated recurrence/recovery and native packages.
The foundation audit records full test counts and the agent-operated Windows checks.
Owner usability acceptance remains open; this does not accept the deferred email trial.

## Scoped P0 closeout and P1 start (2026-11-01)

Windows engineering gate passed: 4,907 tests, nine platform/render skips, 95.92%
overall coverage and the 90% combined statement/branch gate for every service.
Ruff lint/format, strict typing for native/win32/darwin and disposable Windows
single-file package checks passed. Native macOS and real-owner acceptance remain
open; this does not accept the deferred experimental email milestones.

P1's first consolidation increment adds uncapped effective task history, correction
resolution, original completion/group snapshots and partial-coverage semantics.
The final model uses the shared frozen domain base. All 41 focused history/lifecycle/
recovery checks passed after that alignment. The subsequent retry protection and
cross-feature/native automated verification are complete in the closeout above.

Started 2026-11-01 from `main` at `d8e7b9b`, on `feat/p0-local-consent`.
This is the active checklist and review log. The approved
[B2 plan](p0-b2-local-analysis-plan.md) governs local analysis.

Use the reviewed smaller sequence: finish the approved pipeline,
make saved/progressive results usable, then improve capture and Today. Native scheduling,
durable job queues, advanced cloud enrichment and generalized note relationships remain
deferred. No new application dependency is added.

## Increment 1: B2 Part 3 — mode, consent and local limit

The following B2 increment records are historical. Current planner P1 work is in
[the foundation audit](p1-foundation-audit.md): effective history, atomic task-create
receipts, stable editor retry IDs and integrated recurrence checks. Migration 0026
preserves legacy clients and owner records. Windows verification and remaining
platform/owner acceptance are reported separately there.

Implemented:

- Additive migration `20261101_0025`: cloud remains the default; the local limit is
  separately bounded to 1–20 (default 20). Cloud remains bounded to 1–10.
- `ai-consent local grant` names the connected, previously synced Gmail account,
  shows the local disclosure and requires a typed `yes`. It changes no mode/schedule,
  reads no mail, downloads no model and starts no processing.
- `ai-consent local revoke` withdraws every local grant, including old versions,
  without needing credentials or a prompt. Groq consent is independent.
- `ai-consent status` and `preferences show` report the new state.
- Restore revokes local permission; it preserves mode, limits and owner writing.
- Existing Settings saves preserve local mode/limit even before local controls exist.
- Until Part 4, both manual and automatic local-mode briefs fail before either
  provider is constructed. This staged implementation cannot silently use Groq.

Local brief generation and its desktop controls are still pending. The permission
commands are a foundation for that work, not a claim that local summaries are usable yet.

## Correctness review

1. Reviewed provider entrypoints: both CLI and desktop read current preferences and
   refuse local mode before entering the existing cloud composition path.
2. Reviewed consent scope: current disclosure and Gmail account are required;
   repeated current grants preserve their time; cloud grants authorize no local reads.
3. Reviewed failed commits: grant/revoke use the existing single-commit/rollback helper.
4. Reviewed recovery: old/current local disclosures are revoked only in the restored
   database, with original data and Groq consent preserved; cloud auto permission is
   still cleared by the existing restore behavior.
5. Reviewed Settings compatibility: editing other preferences cannot reset local mode
   or its limit to defaults. No new visible controls/layout are introduced.
6. Tightened disclosure wording so its no-cloud guarantee explicitly applies to local
   mode. Permission alone does not switch the existing cloud-mode workflow.
7. Updated tests that intentionally assert the current backup/migration revision;
   historical migration fixtures still target their original revisions.

8. The full regression run caught a migration/ORM mismatch: a numeric SQLite default
   had been quoted as a string. Corrected the new, synthetic-only migration to use
   `sa.text("20")`; head-schema equivalence and the final full run now pass.

### Validation (2026-11-01, Windows / Python 3.13.15)

- Final full suite: **4,830 passed, 9 platform/render skips**, 96.18% overall coverage.
  `scripts/check_coverage.py` confirms every required service meets the 90% combined
  statement/branch gate.
- Ruff lint/format and strict mypy for native, win32 and darwin: passed.
- UI gallery: **111 passed, 1 render skip** at each of 1× and 2×. Inspected light,
  dark and narrow renders against current references; this increment adds no layout
  or styling changes.
- Migration coverage includes old-profile preservation, empty-profile behavior,
  database constraints, downgrade/re-upgrade and schema/ORM equivalence. Recovery
  tests verify all local disclosures are revoked in the restored copy while the
  original, owner writing, mode and limit survive.
- An initial full run also encountered Git subprocess ownership checks and two
  transient local-server test failures. The six failed checks all passed on rerun;
  the final complete run passed. Tests used a process-scoped `safe.directory` for
  this checkout; no global Git configuration was changed.

Test artifacts remain under ignored `out/`, including `final-tests.log`. No live
account, paid AI, real model download or owner profile was used. Packaged/live and
macOS execution acceptance remain pending; type checks do not establish those results.

## Increment 2: local pipeline and desktop controls (in verification)

The [staff engineer review](p0-b2-staff-review.md) found and fixed permission gaps
in desktop revocation and mode transitions, plus the related Settings refresh.
Mode changes now clear old automatic allowances atomically; same-mode edits preserve
them. See that report for the post-review validation, which supersedes the initial
increment's test count above.

The CLI and desktop now compose a dedicated local adapter. Local mode never enters
the Groq factory, including missing-model paths. A retained desktop host starts the
pinned model lazily, serializes leases and shuts down after five idle minutes.
CLI-owned hosts close on exit. Cancel/revoke joins the model process, including a
process already stopping because it became idle.

The local pipeline selects at most the separately configured 20 messages, processes
one model request at a time, and checks the current account's local permission and
preferences revision before each body read/call and before publishing. Permission
watchers use independent, short-lived database sessions: cancelling a watcher must
not invalidate the run's main transaction. Current cache identities remain unchanged.
Matching cached results work without a ready model. A failed local run cannot remove
carried messages from the last good canonical brief.

Settings > Preferences includes the analysis location, local limit, setup and removal.
Setup first shows pinned download provenance/size and a device expectation. Settings
offers Stop during work; Revoke remains available, cancels and joins a running job,
then withdraws permission. Setup disk workers also join before releasing the setup
lock, so cancellation cannot race a subsequent removal or setup. Local progress and
brief provenance explicitly name this computer. Drafting/calendar remain separate
Groq purposes; local mode's readiness does not require a Groq key.

Focused evidence so far: 89 pipeline/runtime checks; 63 UI/safeguard checks;
118 setup/provider/local edge checks; a real composition CLI test with 20 synthetic
Gmail messages, loopback model responses, no Groq model configured, forbidden cloud
factory and cache reuse. Native strict mypy passed for 457 source/test files.
The first gallery runs passed 130 checks (one intentional render skip) at each scale;
additional local-controls renders and final regression are still being verified.
This is not live model or native acceptance.

Remaining core work: dedicated local prompt and separated development fixture with
benchmark evidence; ADR/notices and owner checklist; saved-first progressive display
and usable owner work during analysis. Native scheduling and additional cloud behavior
remain named deferred submilestones under the two-agent critique. Full P0 acceptance
still requires owner recovery/trial evidence and Windows/macOS native checks.
