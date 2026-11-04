# Local pipeline and desktop: staff engineer review

2026-11-01; `feat/p0-local-consent`, against `d8e7b9b`. This is a self-review
from a staff engineer's perspective, using call-path inspection, regression tests
and the UI gallery. It does not represent an independent human review.

## Findings resolved during implementation

1. **P1: cancelling a permission query invalidated the main run transaction.**
   A watcher sharing the brief's SQLAlchemy session could be cancelled while its
   SQLite query was in flight. The 20-message regression exposed a subsequent
   `PendingRollbackError`. Permission checks now own independent short-lived
   sessions. They read current preferences and account-scoped consent; no watcher
   outlives the guarded operation.

2. **P1: HTTP cancellation alone did not establish process shutdown.**
   A local lease now stops and joins its server on cancellation/failure. Shutdown
   is shielded and joined even when close interrupts an idle shutdown already in
   progress. Queued leases cancel the preceding lease's timer after acquiring the
   lock, preventing a timer from stopping an active model. Regression tests cover
   both races and lazy restart/reuse.

3. **P1: setup cancellation could release its lock before a disk worker finished.**
   Python cannot cancel a running thread. Setup, installation and removal now join
   disk workers before releasing the setup lock. Tests block a worker, cancel its
   caller, verify a second operation cannot acquire the lock, then release/join it.

4. **P2: an unavailable model bypassed matching cache results.**
   Readiness is checked after planning against the existing cache. Matching results
   remain usable without a model; new work reports `LOCAL_AI_NOT_READY`. No path
   enters a cloud factory. Failed local runs retain carried canonical brief items.

5. **P2: the desktop could not withdraw permission while analysis owned the run.**
   Revoke stays enabled in Settings. It stops and joins cancellable work before
   revoking. Other durable writes still finish before revocation can start. A Stop
   control is available inside Settings during setup; the owner need not close the
   dialog to reach Cancel. Tests verify ordering and dialog availability.

6. **P2: local calls did not honor the saved request budget.**
   The local adapter now receives the configured budget and refuses the next call
   before leasing/starting a model when it has been exhausted. The benchmark can
   retain its explicit, separately bounded invocation behavior.

## Evidence and remaining gates

- End-to-end CLI: 20 synthetic Gmail messages, real local adapter/loopback response
  parsing, no configured Groq model, forbidden cloud factory, and a repeat using
  cached analyses without another model request: passed.
- Focused pipeline/runtime checks: 89 passed. Focused UI/safeguard checks: 63 passed.
  Setup/provider/edge checks after thread joining: 118 passed.
- Strict native, Windows and macOS type checks: passed for 457 files. Platform type
  checking is not platform execution acceptance.
- Gallery: 130 passed and one intentional render skip at both 1x and 2x. Additional
  Preferences renders inspected in light/dark against the existing dialog reference:
  existing typography, controls, alignment and scrolling are retained. No reference
  replacement or new styling system is proposed.

The diagnostic complete run had 4,856 passes and three compatibility failures:
the old ranking-bound assertions and the cloud consent heading. Those are fixed;
46 focused regression checks passed. Because source changed during that run, its
coverage is not final evidence. A stable independent run then passed 217 service
and CLI checks: brief 92%, setup 99% combined statement/branch coverage, both above
the required 90%. Cloud composition still independently enforces ten messages.

This increment does not finish the dedicated local prompt, its held-out quality
benchmark, progressive owner workflow, licenses/ADR, native execution acceptance,
or the owner recovery/trial journal. These remain explicit P0 work; a clean
synthetic suite does not close them.
