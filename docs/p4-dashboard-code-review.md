# P4 dashboard author code review

Date: 2026-11-08. Scope: the complete P4 branch against main, with a detailed pass
over the durable dashboard, editors, asynchronous reads/saves, query inheritance,
detail generations and shutdown. The earlier backend findings remain recorded in
[the backend review](p4-backend-code-review.md). This is an author review, not an
independent review or owner acceptance.

## Findings addressed

| ID | Severity | Finding | Resolution and regression |
| --- | --- | --- | --- |
| D1 | Medium | Completing a background dialog read cleared the shared saving flag and enabled controls while an owner save was still pending. | Only the write that owns the save lock releases it. `test_finishing_a_read_cannot_unlock_an_inflight_save` holds a read and write independently and verifies exclusivity. |
| D2 | High | Changing metric could leave an old observation selected until the new list arrived. An edit could parse the value with the new metric's precision while targeting the old observation. | Invalidate the old selection/value/history before reading; guard definition identity again before mutation; reject metric changes with unsaved input. The switching-metrics test holds the replacement list in flight and verifies there is no editable old context. New definitions become the selected logging metric. |
| D3 | Medium | List reads checked definition identity only, so an older read of the same metric could replace a newer page. History reads had the same re-selection problem. | Separate definition, entry and history generations discard late results and are invalidated before writes. The delayed same-metric test commits another observation between reads and checks the newer list survives. History also checks the reviewed revision. |
| D4 | Medium | Widget preview omitted inherited dashboard filters and prior-period comparison, so a saved widget could display different values. Reconciled comparisons could also capture a new clock. | One typed `resolved_widget_query` resolves preview and display, inherited filters are visible, comparison captions are shared, and comparisons accept the captured instant. Tests reconcile filtered preview with the saved chart and verify both comparison periods retain the same captured clock. |
| D5 | High | Layout controls retained a positional index while a committed replacement layout was still loading. A click could remove or move a different widget from the new layout. | Bind controls to public identity and the rendered dashboard revision; refuse edits until a newer committed layout has rendered. The old-render regression reverses the committed layout while keeping old controls and verifies it remains unchanged. |
| D6 | Medium | A successful log/definition write followed by a failed list read reported a failed change and did not notify the dashboard. | Emit the committed-change notification immediately after acknowledgement, select newly created definitions, and distinguish a saved change from failed display reload. The reload-failure test verifies exactly one durable observation, a saved message, cleared acknowledged input and one refresh notification. |
| D7 | Medium | Closed dialogs accumulated in the page for the whole application lifetime, retaining widgets, owner input and command state. | Drain a finished dialog's jobs, remove it from the page and delete it through Qt. Shutdown drains newly scheduled cleanup jobs as well. The closed-dialog regression verifies release. |
| D8 | Medium | Cancelling a page during a short profile read could interrupt SQLite connection creation and leave its worker reporting to a closed event loop. | Shield and join profile reads/session cleanup, including repeated cancellation, before returning cancellation. A held-read regression checks the caller cannot finish before cleanup; the review tests also run with unhandled thread warnings treated as errors. |
| D9 | Medium | After a keyboard layout move, focus lookup could find an old hidden control awaiting Qt deletion instead of the newly rendered widget. | Resolve the control only within current cards. The keyboard regression activates Move with Space and verifies focus on the replacement control. |
| D10 | High | Insights retained the Google Calendar scope from its initial open. Later granting access could omit cached busy events and overstate capacity; disconnect could leave coverage stale. | Initialize from the window's canonical calendar mode and refresh the Insights scope whenever the calendar refresh path runs, including cache updates and disconnect. A window integration test grants access and disconnects while Insights remains open. |

## Verification and limits

The review regressions are in `tests/ui/test_insights_review.py`; the existing
offline journey, renderer, lifecycle, snapshot/reconciliation and recovery suites
remain in place. Final repository, package and source-provenance results are
recorded in [the P4 register](p4-insights.md).

No unresolved data-loss, duplicate-write, silent-overwrite or misleading-total
finding remains from this review. Large-profile latency is a documented limitation.
Native macOS/Cocoa, assistive-technology, real-display and owner acceptance remain
open and are not implied by automated Windows checks.
