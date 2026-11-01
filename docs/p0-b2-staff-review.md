# B2 Part 3: staff engineer code review

Date: 2026-11-01. Scope: the uncommitted `feat/p0-local-consent` increment against
`d8e7b9b`, including its new migration and untracked tests. Review method: source
and call-path inspection, failing regression demonstrations, targeted fixes, and
post-fix verification. This is a self-review using a staff engineer's perspective.

## Findings and resolution

### 1. P2 — desktop revocation omitted the newly supported local provider — fixed

The Settings action says **Revoke AI consent (briefs and drafting)**. The backend
only revoked Groq grants. An owner could record local consent through the new CLI,
use this desktop action, and still have an active local grant. Current local-mode
brief generation is blocked, so this review found no actual local processing or
disclosure; nevertheless the stored permission state contradicted the visible action
and would matter when Part 4 connects inference.

Fix: `DesktopRuntime.revoke_consent()` revokes both Groq and local grants for Gmail
accounts within its existing transaction. Revocation includes old disclosure versions.
It preserves dormant Microsoft and historical unrelated-provider behavior and owner
records. The CLI's explicitly documented Groq-only and local-only commands retain
their separate scopes.

Evidence: the extended desktop regression initially expected two revoked grants and
received one. It now verifies both are inactive and a repeated revoke is idempotent.
See `tests/unit/test_desktop_runtime.py::test_revoke_current_ai_providers_for_gmail_accounts`.

### 2. P2 — mode changes could reuse old cloud automatic permission — fixed

The new mode preference was persisted independently of cloud automatic permission.
Switching cloud → local → cloud, including returning through Reset to defaults,
left a previously granted automatic allowance active. A subsequent automatic cloud
run could use that old allowance without the owner deliberately enabling it again
after the provider transition. Reset also disables desktop refresh settings, but
that alone does not remove the permission used by `brief --automatic` or a later
refresh configuration.

Fix: mode changes clear existing automatic allowances and timestamps in the same
transaction as the preferences. This includes first saving local mode into a profile
that previously used cloud defaults. Manual cloud consent remains recorded, local
consent remains independent, and same-mode edits retain the existing automatic choice.
The repository reads only identity, revision and mode for replacement, so malformed
JSON preferences can still be reset safely.

Evidence: both the normal change and reset tests initially retained a five-message
allowance. They now verify it is zero with no grant timestamp. A separate test flushes
both changes, forces commit failure, and verifies rollback restores the original mode
and allowance together. Another verifies an ordinary timezone edit preserves permission.
See `tests/unit/services/test_preferences_service.py`.

### 3. P2 — permission text could remain stale after mutation — fixed

Once a reset changes mode and clears the automatic allowance, the open Settings panel
must not continue displaying its previous allowance. The same refresh gap existed
after the generic revoke action.

Fix: reread the automatic permission after preference changes and desktop revocation,
using the existing read/error-handling path. No new controls, layout or styling are
introduced; unsaved form fields are not replaced by the revocation refresh.

Evidence: the UI regression starts with “Up to 5”, simulates the backend's committed
new state, performs reset or revocation, and verifies the stale allowance disappears.
See `tests/ui/test_preferences_view.py::test_permission_line_refreshes_after_preferences_reset_or_revocation`.

## Architecture and scope assessment

- The enum and separate limits fit existing domain/storage boundaries; neither mode
  selection nor model setup grants permission.
- Current CLI and desktop brief entrypoints refuse local mode before constructing
  providers. This is an intentional staged implementation, not a complete local feature.
- Restore revokes all local disclosures in the staged database and retains mode,
  owner writing and the original database. Existing cloud automatic permission is
  still cleared. No credentials are included in backup/export.
- The migration is additive in effect, preserves existing preferences/defaults and
  matches the ORM after the earlier numeric-default correction. No old migration was
  modified and no real owner database was used for migration development.
- The changes reuse existing consent rows, repository queries and transaction helpers.
  The review adds no job framework, policy engine, dependency or extra schema.

No further architectural expansion is warranted for this increment. Before Part 4
ships, review cancellation, fresh authorization before each body read/model call,
account switching, provider-separated cache and actual model-process containment.
Retain serialization of preference mutations when background inference is added;
allowing unrelated owner work does not require concurrent settings writes. These
are future integration gates, not capabilities established by this review.

## Verification

- Reproduced the first two findings with three failing tests before changing production
  code (normal transition, reset and desktop revoke).
- Post-fix focused checks: 110 passed, then 80 passed including the visible permission
  refresh checks.
- Strict mypy: native, win32 and darwin passed. Ruff lint and formatting passed.
- Final full regression: **4,836 passed, 9 platform/render skips**, 96.18% overall
  coverage. Every required service meets the 90% combined statement/branch gate.
- UI gallery: **111 passed, 1 render skip** at each of 1× and 2×; light, dark and
  narrow renders inspected against the references, with no new layout/styling.
- `git diff --check`: passed. Detailed synthetic test output is in ignored
  `out/staff-review-tests.log`.

## Verdict

No remaining blocking finding was identified for the scoped Part 3 increment after
these fixes. It is ready for the next integration step. Local brief generation remains
deliberately disabled until Part 4 connects and verifies that path.

Live-account, packaged-app and macOS execution acceptance remain pending. No live mail,
paid AI or owner credential/profile was accessed. No independent human review is claimed.
