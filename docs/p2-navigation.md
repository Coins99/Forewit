# P2: canonical navigation foundation

Started 2026-11-02 on `feat/p2-navigation`, based on the P1 integration branch at
`bb60f94`. P1 remains a dependency: this branch is not based on the older email-only
checkout. Remote references were refreshed before branching; no P1 merge or owner
acceptance is implied.

## Implemented behavior

The first P2 increment introduces immutable, validated destinations in
`domain/routes.py`, compatibility mappings in `ui/navigation.py`, and one
`MainWindow._navigate` dispatcher. Page commands, search, Today task/event links and
saved-brief opening share those destinations. Existing method/attribute names and
sidebar commands remain available.

The current screens are adapters during rollout: Notes uses the existing writing
page/editor, Projects uses the existing organization manager, and Mail uses the
Inbox, Today brief, history, saved metadata and drafts screens. Dedicated pages,
the six-page sidebar and project-record routes arrive with the corresponding P2
features. No schema or shortcut migration is introduced here.

- Task opening clears a text filter that hides its row. If the task is absent from
  the loaded view, the runtime reads it by canonical ID through `ActionService.get`,
  resets organization filters and reveals its current open/waiting/completed view.
  A completed task outside the usual list limit can still be opened. Missing tasks
  leave a usable page and an explanatory status message.
- A navigation generation and account snapshot protect pending opens. Moving away
  and back still invalidates an old request. Late mail/note/task opens do not select
  a record after the owner chooses another destination. Calendar selection callbacks
  are checked independently from their data reads.
- Mail routes bind the account; opening rechecks that account after each awaited
  metadata read. An account change clears old palette results and invalidates pending
  searches. Existing source-link validation still admits only Gmail links.
- Saved briefs keep their requested account/date and remain readable while the
  account is disconnected. Account-context changes invalidate an in-progress open.
  History previews use a separate sequence so an older preview cannot replace a
  newer selection. Note switching retains the existing save/conflict guard.

Routes store identity rather than copied owner text. Mail identifiers and account
context are excluded from their diagnostic representations. These changes perform
no provider writes, body downloads or AI requests.

## Verification register

Final Windows source verification completed 2026-11-03. No production source was
changed during the final full run. A separate runtime integration assertion also
passed against the same production source, exercising canonical task retrieval
from the real local database.

- Focused domain, navigation, palette and Today checks: 75 passed on Windows.
- Final action/navigation regression after preserving bounded-list totals: 164
  passed. The completed-task test now starts with 200 loaded rows and 500 total
  records, then reveals the requested task while retaining the more-results label.
- Strict win32 and darwin typing: passed, 469 source/test files each.
- Repository Ruff lint/format: passed.
- Gallery and theme checks: 275 passed and one intentional render skip at each of
  1x and 2x. Desktop light/dark renders match the Studio references on inspection;
  narrow light/dark 2x renders were also inspected. The timezone-switching test
  emits the existing system-offset warning in both gallery runs.
- Final full regression: 4,972 passed, nine platform/intentional skips and 26
  warnings; 95.91% combined coverage. The coverage checker confirms every required
  service meets the 90% combined statement/branch gate. The final full run includes
  the completed-list total fix; the earlier full run is preliminary evidence only.
  Safe logs are under ignored `out/p2-navigation-*` and synthetic renders under
  ignored `scratch/p2-navigation-shots/`.
- Native macOS execution and owner usability acceptance are not established by
  Windows automation. Native Windows/macOS packages were not rebuilt for this
  source-level increment; package/release acceptance remains open.

The implementation tests use synthetic records and delayed async responses. They
cover route validation, legacy aliases, account changes during mail opening, late
note loads, missing tasks, tasks outside completed limits and navigation away/back.

## Remaining P2 work

Global Task/Note capture and retry-safe initial note content are implemented in the
[P2-02 checkpoint](p2-capture.md). Durable My focus/day plans come next.
Notes/Projects pages and links, Mail/sidebar migration,
optional capture review, upcoming/workload/overview, dated plan management and
project/brief search remain separate increments. This foundation does not establish
full P2, P1 owner acceptance or deferred email/local-model acceptance.
