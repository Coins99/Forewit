# P0: foundations required by P1–P4

Owner scope decision: 2026-11-01. Local email AI remains experimental and disabled
by default (`MAILBRIEF_EXPERIMENTAL_LOCAL_AI=true` opts in). Local-model quality,
native model acceptance, AI queue/resource policy, closed-app scheduling, cloud
enrichment and the email-specific use journal are separate deferred email work.

Status: scoped P0 engineering verification complete on Windows; P1 consolidation
started. Native macOS and real-owner acceptance remain open as specified below.

## Engineering completion gate

- Owner tasks and writing remain independent of accounts and cached email.
- Revisions protect edits; failed/cancelled writes roll back state and activity.
- Additive migrations preserve records; exports and verified backup/restore retain
  tasks, recurrence, organization, writing/versions and preferences.
- Restoring data does not restore credentials or authorize unattended processing.
- Without AI/Gmail, saved work, task capture/editing, local search and planning work.
- Disabled local mode stops before mailbox/body/model operations, does not fall back
  to cloud, and preserves access to saved records, revocation and removal.
- Required regression, coverage, lint/type checks and disposable native package
  lifecycle/recovery checks pass for the tested candidate.

The existing tests cover these contracts; changes should address demonstrated gaps
rather than introduce another storage, event or job framework.

## Validation record

2026-11-01, Windows, `feat/p0-local-consent` on `d8e7b9b` plus the working-tree
foundation and P1 history changes. This record will distinguish the P0 baseline
regression from checks of the subsequent P1 delta.

| Check | Result |
| --- | --- |
| Foundation baseline | 4,885 passed, nine platform/render skips; three stale expectation failures corrected, with all 56 affected tests passing on rerun |
| Final full regression and coverage | 4,907 passed, nine platform/render skips; 95.92% overall, every service above the 90% combined statement/branch gate; `out/planner-final-full.log` |
| Default-off flag and independent workflow regressions | Existing focused checks passed; included in full regression |
| Final source lint/format | Passed after a source-only line continuation preserving the experimental prompt text |
| Strict typing, native/win32/darwin | Passed, 463 source/test files |
| Windows single-file build and disposable package lifecycle/recovery | Passed; 100%/200% offscreen plus native Windows, persistence, renamed-copy locks and older-schema upgrade; `out/planner-p0-package.log` |
| P1 history and lifecycle/recovery delta | 41 focused checks passed again after final domain-model alignment; all 1,204 service/domain checks passed; final repository coverage gates passed |

Verified Windows executable SHA-256:
`cdaa58bb76a61878199fc60bfb8208687fa8c3703d9db8c39a01267e6d5c1457`.
This is a local dirty-tree engineering build, not a published release.

No owner profile, live mailbox, credentials, paid AI or model download is used.
macOS native execution and real-owner desktop/recovery acceptance remain unverified
in this Windows session. They are not claimed passed by type checks or synthetic
fixtures. Historical M4–M9 email acceptance remains open independently of this scope.

## Follow-on order

Update 2026-11-02: the final P0/P1 source candidate passed Windows and macOS CI,
including native Cocoa tests, both offline desktop packages and Windows frozen
lifecycle/recovery. See [P1 closeout](p1-foundation-audit.md#closeout-record-2026-11-02)
for the exact snapshot, test counts, publication audit and outstanding owner acceptance.
This supersedes the platform limitation of the earlier local Windows run above;
it does not claim owner or live-email acceptance.

P1 consolidates the existing task lifecycle and history, then addresses uncertain
create retries. P2 adds the independent daily workspace, P3 work blocks and planning,
and P4 typed metrics/dashboard queries. Each stage extends migration/export/recovery
checks with its own durable entities; it does not wait for deferred email AI.
