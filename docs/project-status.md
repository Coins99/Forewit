# Forewit

Forewit is a desktop app for Windows and macOS that turns today's important
email into a short brief. Gmail is the first supported mailbox. Outlook code is retained for
later use after Microsoft Entra access is resolved.

## Current state

- P1 first slice implemented, awaiting owner desktop acceptance: [manual tasks and activity
  history](p1-manual-tasks.md). Manual and email-derived actions share the editor and
  lifecycle; completion/reopening/undo history survives recovery and export. Projects and
  life areas now have an implementation ([organization workflow](p1-projects-life-areas.md)),
  awaiting owner desktop acceptance. Unified Today's first slice is implemented, awaiting
  owner desktop acceptance: **Your day** on the Today page shows what needs attention,
  today's agenda and new replies, from local data only ([Today](today.md)).
  Tasks can carry the owner's own estimate in minutes, separate from the AI's effort
  ([estimates](p1-manual-tasks.md#estimates)), awaiting owner desktop acceptance.
  Priorities and tags (B03) are implemented, awaiting owner desktop acceptance: a priority
  orders tasks with the same dates, and the Actions filter offers each tag
  ([priorities and tags](p1-priorities-tags.md)). Repeating tasks (B05) are implemented,
  awaiting owner desktop acceptance: daily, weekly or monthly, with skip and reschedule, one
  open occurrence at a time ([repeating tasks](p1-recurring-tasks.md)). Deadlines the owner
  sets (B06) are implemented, awaiting owner desktop acceptance: set, change or clear a
  task's deadline, keeping an email's to go back to ([deadlines](p1-deadlines.md)). Waiting
  and commitments (B08) are implemented, awaiting owner desktop acceptance: name who a task
  waits on or is for, see how long you've waited, and get a follow-up prompt only when one is
  due ([waiting and commitments](p1-waiting.md)). Checklists in place (B04) are implemented,
  awaiting owner desktop acceptance: tick and add an open task's steps in its detail pane,
  one revision each ([checklists in place](p1-checklists.md)).

- P0-B1 benchmark for local-first email AI (D09) done, as a developer tool only: a 4B model
  on this computer, run through Forewit's own checks, against a rule-based baseline, with
  speed, memory, energy and no-network evidence ([local AI benchmark](local-ai-benchmark.md)).
  Nothing in the desktop app changes; P0-B2 builds the pipeline.

- Implemented: provider-neutral models, SQLite storage, daily sync, ranking, and
  a tested Microsoft Graph adapter.
- Implemented for M1: Gmail OAuth, OS credential storage (Windows Credential Manager or
  macOS Keychain), and an authentication
  diagnostic. The owner verified sign-in, restoration, disconnect and reconnect.
- Implemented for M2: Gmail Inbox metadata, pagination, safe reconciliation,
  local ranking and a metadata sync/review diagnostic.
- Implemented for M3, live acceptance recorded in [M3 validation](m3-validation.md):
  shortlisted body reading and preparation ([Gmail bodies](gmail-bodies.md)).
- Implemented for M4, awaiting live acceptance: consented Groq analysis and the saved
  daily brief ([AI analysis](ai-analysis.md)).
- Implemented, awaiting acceptance: M5 desktop workflow, with full-Inbox shortlist
  review, consent, cancellation, saved-brief restoration, offline metadata browsing,
  graphical settings, and native packaging scripts.
  See [desktop usage, builds and remaining acceptance](m5-desktop.md).
  macOS package execution and packaged live-account checks remain pending.
- Implemented, awaiting live acceptance: M6 action suggestions on the brief, accepted
  actions with editable plans and target dates, open/waiting/completed lists and undo.
  See [M6 actions](m6-actions.md).
- M7 implemented, awaiting live acceptance: local drafts and notes, and AI drafting with
  Groq from context you choose.
  See [M7 drafts, notes and messages](m7-drafts.md).
- M8 implemented, awaiting live acceptance (PR #17): your time zone, messages per brief,
  excluded senders, drafting defaults and AI limits; brief history and catch-up for missed
  days; tracking the threads of open actions, with proposed updates to your actions and
  replies outside today's Inbox offered in review; and refreshing while Forewit is open,
  with automatic analysis only by your explicit permission.
  See [M8 daily operation](m8-daily-operation.md).
- M9 implemented, awaiting live acceptance: **Data and recovery** provides snapshot
  backup, verification, safe restore on exit, portable writing exports, manual retention
  previews, safe diagnostics and bundled setup/recovery help. Diagnostic commands that use
  the database can't run while the desktop is open: they exit with code 5.
  See [M9 durability](m9-durability.md) and the
  [owner's live-validation checklist](m9-live-validation.md).
- Ranking signals implemented, awaiting live acceptance ([ADR 0020](adr/0020-ranking-signals.md)):
  the review's suggestions use Gmail's tabs, stars and bulk-mail headers, your important
  senders, and what your own choices say about each sender; **Should have been in my
  brief** on the Inbox page feeds it. See [Ranking](ranking.md).
- Search and commands implemented, awaiting owner desktop acceptance: **Go to or search**
  (Ctrl+K) goes to pages, starts a task, event or note, and searches local records only
  ([Go to or search](search.md)).
- Inbox reading pane implemented, awaiting owner desktop acceptance (PR #76): the **Inbox**
  page starts as a list across the page, by day, as Gmail's does, and opening an email
  shows it in a reading pane beside the list, as Outlook's does (on a narrow window the
  reading pane takes the page). It shows the full Inbox's saved metadata, with no bodies or
  AI calls ([Google sign-in and the full Inbox](google-signin-inbox.md)).
- Calendar phase 1 implemented, awaiting live acceptance: a **Calendar** page with your
  own events (timed or all-day, with Undo) and your open actions' deadlines and target
  dates. Google Calendar, read-only (phase 2), implemented, awaiting live acceptance:
  with one Google sign-in shared with Gmail (ADR 0019), Google events show on the Calendar
  page and refresh with every Sync and review. **Plan with AI** (phase 3) implemented,
  awaiting live acceptance: Groq proposes changes to your own events, and nothing changes
  until you apply them. Week and Month grids beside the List implemented, awaiting owner
  desktop acceptance (PR #77): the page shows a **Week** of hours, a **Month** of six weeks
  or the week's **List** of rows, chosen by tabs and remembered on this computer
  ([Week, Month and List](calendar.md#week-month-and-list)).
  See [Calendar](calendar.md) and [ADR 0018](adr/0018-calendar.md).

## The desktop window

![Forewit in its dark theme: the header, the sidebar, the brief and the selected email](ui/workspace-dark.png)

The screenshot is from before Studio (PR #74) and the Account card (PR #78);
[Studio](ui/studio.md) shows the current look.

Forewit is one window with three panes (PR #21):

- **Header**: when Gmail was last checked, then **Sync and review**, and **Cancel** while
  something runs.
- **Sidebar**: the pages **Today**, **Inbox**, **Actions**, **Waiting**, **Drafts**,
  **Briefs** and **Calendar**, with counts for actions, waiting actions and drafts. Below
  them, **Saved mail** (the offline browser of cached metadata), **Data** (backup, exports
  and recovery) and **Settings**, and **Google sign-in** only while no account is
  connected. The Gmail and AI status and **Disconnect** are in Settings' **Account** card
  (PR #78).
- **Today**: the brief's emails by section, and the selected email with its summary,
  deadline, suggested actions, proposed updates, **Draft a reply** and **Open in Gmail**.
- **Status strip**: the latest status along the bottom, with **Undo** after a change you
  can reverse.

**Sync and review** runs in two steps on its own page. Step 1, **Choose what Forewit
reads**: only the messages you leave checked have their bodies downloaded. Step 2,
**Approve sending to Groq**: approve or decline what will be sent; Decline starts focused.

Ctrl+K (Cmd+K on macOS) opens **Go to or search**: commands, and a local search of your
actions, drafts, notes, events and saved mail ([Go to or search](search.md)).
Ctrl+1 to Ctrl+7 (Cmd+1 to Cmd+7 on macOS) open Today, Inbox, Actions, Waiting, Drafts,
Briefs and Calendar, in the sidebar's order. Tab runs from Sync and review through the sidebar and the page to Undo.
The look is Studio (PR #74): white and Studio Mist grey with one blue, in the system's
light or dark mode. Settings > Appearance offers six other colour schemes (Slate,
Graphite, Ocean, Sage, Plum and Rose) and can keep Light or Dark ([Studio](ui/studio.md)).

## Start here

- [Single Windows EXE: Desktop launch, builds and updates](windows-executable.md)

- [Scope](mvp-scope.md)
- [Implementation plan](mvp-plan.md)
- [Detailed email milestones and acceptance checks](email-implementation-plan.md)
- [Later desktop ecosystem and website guidelines](ecosystem-roadmap.md)
- [Future planner feature inventory](future-feature-inventory.md) and
  [researched future implementation plan](future-implementations.md)
- [Gmail setup and authentication diagnostic](gmail-setup.md)
- [Gmail metadata synchronization and shortlist review](gmail-metadata.md)
- [Ranking: which emails the review suggests](ranking.md)
- [Gmail bodies (M3) and live acceptance](gmail-bodies.md)
- [AI analysis, consent and the daily brief (M4)](ai-analysis.md)
- [Actions, target dates and plans (M6)](m6-actions.md)
- [Drafts, notes and messages (M7)](m7-drafts.md)
- [Preferences and daily operation (M8)](m8-daily-operation.md)
- [Backup, exports and retention (M9)](m9-durability.md)
- [Live validation and personal-use journal (M9)](m9-live-validation.md)
- [File change map](change-map.md)
- [Microsoft work retained for later](microsoft-setup.md)

## Development

```powershell
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

Python is pinned to 3.13. Runtime code lives under `src/mailbrief`; tests mirror
that structure under `tests`.

Keep the checkout and its `.venv` out of folders synced by iCloud Drive (Desktop &
Documents), OneDrive or Dropbox: they set the macOS hidden flag on `.venv` files, and then
Python 3.13 skips hidden `.pth` files and Qt skips hidden plugins.
