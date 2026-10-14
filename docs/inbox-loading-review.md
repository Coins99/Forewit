# Review of bounded Inbox loading

Review scope: commit `73e1c52` against `main`, including provider validation, pagination,
cache retention, cancellation and authentication, memory use, UI recovery and compatibility
with daily briefs and the local command palette. Reviewed and fixed 2026-10-14.

## Findings and fixes

| Priority | Finding | Fix and regression coverage |
| --- | --- | --- |
| P1 | Newer stale cached rows could occupy all 50 initial display slots while the cursor advanced past older fresh Inbox results. Those fresh messages were then hidden from browsing. | Replace the startup preview with the first provider page's validated results; retain unobserved records in SQLite. Tests cover newer stale cache, partial metadata followed by retry, and an empty fresh Inbox. |
| P2 | A page whose metadata requests all failed could replace the cached preview with an empty display. | Treat total metadata failure as a failed page without publishing traversal state; the existing cached display and cursor remain available. Tests cover first-page and continuation retries. |
| P1 | Authentication loss during a bounded refresh cleared the Inbox view but could leave Google access/browsing state and allow connection, startup or automatic workflows to continue. Reconnecting could reuse an old browsing session. | Close provider/session resources, clear signed-out runtime state, reset cursors on successful connect, and stop subsequent workflows when account identity disappears. Tests cover revoked requests, reconnect, startup, connection and automatic refresh. |
| P2 | Trash/Spam labels were acted on before checking that the returned metadata ID matched the requested ID. Malformed metadata could delete the requested cached row. | Validate identity before label-based discards. Tests cover wrong/missing IDs with both Trash and Spam. |
| P2 | Bounded message counts did not bound the total page-fetch duration, and completed searches retained potentially large ID/cursor sets indefinitely. | Apply the existing 180-second work budget to each page fetch, and retain only Inbox plus the active search's traversal history. Tests cover timeout with cache preservation and background refresh retaining active search state. |
| P3 | A failed first search left Back to Inbox hidden; disconnect could leave Refresh search or Retry page labels visible. | Show Back to Inbox for typed search input as well as active results, and reset control labels on clear. UI regression tests exercise failure recovery and reset. |

## Preserved behavior and limitations

- Initial/continuation ID limits remain 50/100, without a date cutoff or hidden gap-filling
  requests. Metadata failures retry the current page instead of silently skipping it.
- Limited pages never reconcile unobserved all-date Inbox membership or advance the
  daily-brief watermark. Daily briefing remains a complete local-day sync.
- Bodies, attachments and AI calls remain outside browsing/search. No new scopes,
  dependencies or migrations are required. Mail and search input render as plain text.
- Old unvisited cache entries can be stale, and Gmail can change during enumeration.
  Live-mail latency and ordering still require owner acceptance.

Full regression validation passed 4,035 tests with four platform/intentional skips.
The final total-metadata-failure guard was followed by 270 passing affected tests.
Combined coverage is 96.47%, and every service meets the 90% gate. Ruff, platform type
checks and the required UI renders pass. Details are recorded with the implementation
and in the pull request.
