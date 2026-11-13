# Forewit agent guide

## Branding and compatibility

Forewit is the public product name; `mailbrief` remains the Python package and
distribution. Keep Qt's organization/application names `MailBrief`, the current
data paths, `MailBrief.*` vault entries, `MAILBRIEF_*` configuration and secrets,
`com.mailbrief.desktop`, and machine-readable backup/export formats unchanged.
Use `mailbrief.branding` for the display name and canonical command names. The
original `mailbrief*` commands remain supported aliases. Historical ADRs and
archived material retain the original project name.

This is the single source of truth for AI agents working in this repository. Read it
first, then only the files your task needs. If another document disagrees with this
file, this file wins. `docs/archive/` holds superseded plans and notes for reference only.

## Product and status

- Personal desktop app for Windows and macOS that turns today's important Gmail into a
  short brief, and a general personal planner (tasks, projects, notes, calendar). Both
  platforms are supported, from source or as native packages.
- Gmail is the active provider. Microsoft (Outlook/Graph) code is dormant: keep it
  compiling and its tests passing, but do not extend it unless a task says so.
- Owner scope decision (2026-11-01): local AI is experimental, behind
  `MAILBRIEF_EXPERIMENTAL_LOCAL_AI=true` (default false). Flag-off local mode fails
  closed; never substitute cloud. Setup and inference require the flag; revoke,
  remove and saved-record access remain available. Enabling the flag grants no consent.
  P1–P4 require the P0 data/recovery/owner-work foundations only. Local model quality,
  AI queues, native scheduling and cloud enrichment remain separate deferred work;
  their acceptance and the email trial do not block planner delivery.
- Everything through P2 (email milestones M1–M9, planner P0–P2) is implemented and
  merged, except the deferred experimental local-AI work (the dedicated local prompt,
  its ADR and notices, progressive operation) and capacity planning against available
  hours (Upcoming shows known commitments, not capacity). Apart from M1–M3, nearly all of
  it awaits the owner's live or desktop
  acceptance, and native macOS package checks remain open for the P2 increments. Never
  claim a feature accepted from CI, agent-run or package checks alone. M9 is accepted
  only after the owner completes real-record recovery and the 7–14-day release scenario.
  `docs/project-status.md` and `docs/p0-p2-implementation.md` hold the current state and
  evidence; read them, not old commit messages, for what is done.
- P3's work blocks, availability and workload are merged (#105–#107), as are flat
  buttons/date pickers (#108). Owner acceptance remains open. The scheduling add-ons
  branch extends main with dated hours, independent buffers, locks, successful Google
  knowledge and deterministic local suggestions with retry-safe apply/Undo. New migrations
  0034–0036 preserve merged 0032/0033. See `docs/p3-scheduling-addons.md` and ADR 0031;
  do not replace main's Day view, Busy/Free, gestures or daily workload. The divergent
  unmerged candidate requires separate-copy conversion, never a normal schema upgrade.
- P4 now includes the durable offline Insights page: exact metric logs/corrections,
  task/workload/custom queries, saved revisioned dashboard layouts (migration 0038),
  widget preview/editing, targets/comparisons, keyboard layout controls and stale-safe
  record inspection. Ctrl/Cmd+1?8 keep their existing meanings; Insights is in the
  sidebar and palette. Source/native verification and remaining acceptance gates are
  recorded in `docs/p4-insights.md` and ADR 0032. Native macOS, assistive-technology,
  real-display and owner acceptance remain open; do not claim P4 accepted.
  An independent review and macOS UI check fixed fifteen defects (`docs/p4-ui-review.md`).
  Keep what it settled: a refresh updates the cards in place (`MetricChartCard.set_display`),
  never rebuilds them for the same layout; widget queries go through
  `resolved_widget_query` (dashboard filters only where the metric takes them, forward
  plans from today); Insights combo boxes come from `combo()` and its tables carry the
  theme's `dataTable` property; a dialog's status line stays outside its scrolled fields.

- Where each feature is specified (behavior, acceptance steps and limits):

  | Area | Docs | ADRs |
  | --- | --- | --- |
  | Gmail sign-in, sync, bodies | `gmail-setup.md`, `google-signin-inbox.md`, `gmail-bodies.md`, `inbox-loading.md` | 0007, 0019 |
  | AI analysis and the brief (M4) | `ai-analysis.md` | 0009, 0010 |
  | Desktop workflow and packages (M5) | `m5-desktop.md`, `m5-acceptance.md`, `windows-executable.md` | 0006, 0008 |
  | Actions and suggestions (M6) | `m6-actions.md` | 0011 |
  | Drafts, notes, AI drafting (M7) | `m7-drafts.md` | 0012, 0013 |
  | Daily operation (M8: preferences, history, threads, proposals, automatic runs) | `m8-daily-operation.md` | 0014–0017 |
  | Data and recovery (M9) | `m9-durability.md`, `m9-live-validation.md` | |
  | Ranking signals | `ranking.md` | 0020 |
  | Calendar, Google Calendar, AI planning | `calendar.md` | 0018 |
  | P3 local calendar: work blocks, working hours, workload | `p3-calendar.md`, `calendar.md` | 0028–0030 |
  | Local email AI (P0-B, experimental) | `p0-b2-local-analysis-plan.md`, `p0-b2-local-analysis.md`, `local-ai-benchmark.md` | 0023 |
  | Planner foundation (P0, P1 tasks, projects, estimates, priorities/tags, repeats, deadlines, waiting, checklists) | `p0-planner-foundation.md`, `p1-foundation-audit.md`, `p1-*.md` | 0021, 0022, 0024 |
  | Today, search and the palette | `today.md`, `search.md` | |
  | P2 navigation, capture, focus, notes, workspace | `p2-navigation.md`, `p2-capture.md`, `p2-focus.md`, `p2-notes.md`, `p2-workspace.md` | 0025–0027 |
  | Desktop UI look | `ui/studio.md`, `ui/liquid-glass.md`, `.claude/skills/mailbrief-ui/` | |
  | QA sweep: rules, budget, slices, findings | `qa.md` | |
  | Owner acceptance run-sheet (one checklist for the real app) | `acceptance-runsheet.md` | |

- The diagnostic CLI is `forewit-gmail-diagnostic` (subcommands such as `sync`,
  `bodies`, `brief`, `actions`, `drafts`, `preferences show`, `ai-consent`,
  `local-ai`); offline backup, verify and restore is `python -m
  mailbrief.diagnostics.backup`. Gmail diagnostic database commands hold `desktop.lock`
  for their whole run: they exit 5 while the desktop is open, and the desktop reports
  that Forewit is busy while one runs.
- The desktop's sidebar is Today, Tasks, Projects, Calendar, Notes, Insights and Mail. Ctrl/Cmd+1–8
  keep their earlier meanings: 1 Today, 2 Inbox, 3 Tasks, 4 Waiting, 5 Mail drafts,
  6 brief history, 7 Calendar, 8 Notes. New task is Ctrl/Cmd+N, Capture
  Ctrl/Cmd+Shift+N and the palette Ctrl/Cmd+K. The default look is Studio (`docs/ui/studio.md`), in the system's light or
  dark mode unless Settings > Appearance keeps one.
- Restore retains a pre-restore snapshot, clears automatic AI permission, revokes local
  AI grants and never touches credentials.
- Retry receipts (`task_commands`, ADR 0024; `note_commands`, ADR 0025) commit
  atomically with the record they create. They contain no owner text, have no foreign
  key to it, survive backup, restore and purge, and are left out of writing exports. A
  retry with the same command ID returns the current record; different input, or a
  removed result, is refused.
- `ActionService.history()` reads a task's uncapped effective activity, including
  soft-deleted records; `domain/task_history.py` resolves correction chains and marks
  legacy or gapped history partial. Never treat a capped UI list as lifetime history.

## Layout (ports and adapters)

- `src/mailbrief/domain/`: frozen Pydantic models; no I/O. `domain/common.py` has the
  one `utc_now()` clock and `new_public_id()`; `DeadlineFields` (`domain/analysis.py`) is
  the deadline every suggestion, analysis, action and proposal carries.
- `src/mailbrief/ports/`: `EmailProvider` / `AIProvider` / `DraftingProvider` protocols
  (`drafting.py`), `ThreadReader` (`threads.py`) and provider-neutral errors.
- `src/mailbrief/providers/gmail/`: active adapter. `providers/microsoft/`: dormant adapter.
  `providers/gcal/`: Google Calendar, read-only (ADR 0018): its own sign-in, the calendar
  list and event reads, behind `ports/calendar.py`. `providers/google/`: what Gmail and
  Google Calendar share: the bounded GET with retries (`requests.py`), the token endpoint
  (`tokens.py`) and the vault-held refresh credential (`credentials.py`); each adapter
  passes its own fixed messages, and `error_reasons()` reads Google's error reasons.
  `GmailBodies` (`gmail/factory.py`) opens Gmail for one body download.
  `providers/groq/`: active AI adapter (Structured Outputs), API key store and factory.
  `providers/local/` (P0-B2): `transport.py` (`LoopbackGuard`, which refuses every request
  to a host other than localhost, 127.0.0.1 or ::1), `provider.py` (`LocalModelProvider`,
  a dedicated versioned local prompt with the shared analysis schema, sent to a
  `ModelServer` on loopback) and `server.py`
  (`LlamaServer`: a free port of 127.0.0.1, a fresh API key per launch, no web UI, a wrong-key
  and right-key check that the server on the port is ours, output kept in memory only, a
  Job Object on Windows and a leftover record elsewhere). `manifest.py` pins the runtime
  and model per platform (URL, size, SHA-256, license, source, and the hosts downloads may
  reach); `device.py` reads memory and `llama-server --list-devices` for the speed the
  owner can expect. `transport.py`'s `AllowedHostsGuard` keeps setup's downloads to HTTPS
  on those hosts, on every redirect.
- `src/mailbrief/services/`: calendar, sync, ranking, bodies, application, and for M4:
  analysis, deadlines, digest and brief; for M6: actions; for M7: drafts and drafting
  (AI drafting: parts, preview, consent, validation); `events` for the owner's calendar
  (ADR 0018), `google_calendar` for reading Google Calendar into its cache, and
  `projects` for owner projects and life areas, `calendar_planning` for AI-proposed
  calendar changes, and `work_blocks` for time set aside for a task (ADR 0028).
  `calendar.py` is day boundaries and time zones, not the calendar feature.
  `domain/intervals.py` is the half-open UTC interval arithmetic (union, clip, lengths)
  calendar counts go through, so overlaps never count twice.
  `writes.py`: `committed()` commits one change and rolls it back on any failure, and
  `commit_once()` adds the clock read once; the owner-record services, the auto-send
  permission and the Google Calendar cache write through them. `compose_brief()`
  (`brief.py`) builds the brief as the desktop and the CLI both run it.
- `src/mailbrief/domain/messages.py`: normalized messages, `own_addresses` (the primary
  address plus the aliases; every owner address set is built with it) and
  `is_own_message`, the one definition of the owner's own message, shared by ranking,
  thread activity, outside replies and proposals.
- `src/mailbrief/domain/drafts.py`: draft kinds, limits, placeholders and exports;
  `domain/drafting.py`: what AI drafting sends and gets back; `domain/preferences.py`: the
  owner's preferences, time zone rule and sender rules.
- `src/mailbrief/services/preferences.py`: read, save and reset preferences, apply the
  saved AI limits (`effective_settings`) and the owner's zone (`owner_zone`).
- `src/mailbrief/services/ranking.py`: the deterministic local score and its reasons, with
  the owner's side (`RankSignals`, `sender_signal`, ADR 0020); `MessageRepository.
  sender_history` counts what the owner wanted, left out and kept, by sender address.
- `src/mailbrief/services/today.py`: the owner's day (`DaySummary` in `domain/today.py`):
  open actions overdue, due or planned by today, new replies and the day's calendar. It
  reads only, and needs no Gmail or AI.
- `src/mailbrief/services/search.py`: the palette's search (`domain/search.py`): parameterized
  `LIKE` queries with the wildcards escaped, at most `HITS_PER_KIND` per kind, no full-text
  index (recovery refuses extra tables and triggers).
- `src/mailbrief/services/history.py`: saved briefs by day, missed days, the catch-up date
  rule (`check_brief_date`) and each brief's `coverage_line`.
- `src/mailbrief/services/threads.py`: checks the threads of open actions and caches their
  later messages' metadata, forgetting those Gmail has in Trash or Spam (ADR 0015), and
  finds the cached replies today's Inbox sync can't see (`outside_replies`, ADR 0016).
- `src/mailbrief/services/proposals.py`: derives follow-up proposals from a saved brief's
  analyses, and applies, undoes, dismisses and restores them (ADR 0016).
- `src/mailbrief/storage/`: async SQLAlchemy + aiosqlite, repositories, `migrate.py`,
  `actions.py` for suggestions, decisions and accepted actions, `proposals.py` for
  follow-up proposals, `drafts.py` for drafts, their versions and source snapshots, and
  `preferences.py` for the preferences row. Alembic revisions live in
  `migrations/versions/`; `migrated_database()` (`migrate.py`) opens a database brought up
  to date, and Alembic is imported only where a migration runs. `DeadlineColumns`
  (`tables.py`) holds the deadline columns, with `deadline_values()` and
  `deadline_columns()` to copy them to and from `DeadlineFields`.
- `src/mailbrief/text/`: provider-neutral text helpers for untrusted email (HTML to text,
  quote trimming, length limits, and `matching.py` for checking quotes against the email).
- `src/mailbrief/infra/`: HTTP retry classification, `vault.py`, the explicit OS
  credential vault, `files.py`, atomic writes for exports the owner chooses, and
  `tasks.py`, which runs a group of tasks and never leaves one behind (`running()`,
  `gather_or_cancel()`).
- `src/mailbrief/diagnostics/`: developer CLIs.
- `src/mailbrief/ui/`: the PySide6 desktop, with the sub-packages `ui/glass/` (Liquid Glass) and
  `ui/theme/` (tokens, style, fonts, icons). Its file map and UI rules are in the
  `/mailbrief-ui` skill, `references/layout-and-rules.md`.
- `src/mailbrief/app.py` owns the qasync loop, and `MailBriefApplication`, which lets the
  window ask before a quit (Cmd+Q, the Dock's Quit) would lose a dialog's unsaved input.
- `scripts/build_desktop.py` and `scripts/check_package.py`: native PyInstaller builds
  and credential-free package checks. Build artifacts stay in `out/`.
- `scripts/local_ai/`: the P0-B1 benchmark (`python -m scripts.local_ai.benchmark`). It runs
  the app's own `providers/local` provider and server, through `LoopbackGuard`; the model
  server is an external program, so no local-model library is added. Reports go to
  `out/local-ai-benchmark/`; use synthetic fixtures only, since reports hold analyses.
- Windows single-file builds use `build_desktop.py --onefile` and produce
  `out/dist/onefile/Forewit.exe`; check with `check_package.py --layout onefile
  --executable out/dist/onefile/Forewit.exe`. Keep the existing Qt profile identity.
  `scripts/package_manifest.py` audits embedded resources; `scripts/stage_release.py`
  rejects dirty, mismatched or modified release candidates. Version tags create a draft
  Windows release only. See `docs/windows-executable.md` for pending owner acceptance.

## Invariants (never break these)

- Never write OAuth tokens or API keys to SQLite, logs, exceptions, printed output or
  files. Never write full email bodies there either, beyond the short preview snippet Gmail
  supplies, which SQLite has kept since M2; for a very short email that snippet can be the
  whole text. The one exception is `forewit-gmail-diagnostic bodies --show-text`, which
  prints prepared text to the owner's terminal on explicit request. `brief --show` prints
  derived brief content (sender, subject, summary, action, deadline, link, suggestion
  titles, targets and steps, and the titles and IDs of the actions an email continues) on
  explicit request, never evidence or bodies.
- Derived content is bounded: a summary is at most 240 characters, an action at most
  1,000, and evidence at most 300 and strictly under 80% of the body. An email has at most
  five suggestions, each with a title of at most 120 characters, at most five steps of at
  most 120, and evidence of at most 160, and one follow-up signal with a quote of at most
  160. All stored evidence for one email totals at most 600 characters and stays under 80%
  of its body. A summary of a very short email may restate it; a body of one or two
  characters leaves room under 80% for one character at most, too little for a quote, so
  it stores no evidence, for itself or its suggestions, and is still analyzed.
- Text the AI writes itself (summary, action text, suggestion titles and steps) and
  deadline phrases are stripped of control and format characters before they are stored;
  an evidence quote is cleaned the same way once it has matched the email.
  Mail and AI text reaches the UI only through plain-text widgets or escaped HTML, and only
  `https://mail.google.com` message links are ever opened.
- Accepted actions belong to the owner: AI runs never write to them, and they survive
  message, cache and account deletion through their source snapshots. The owner's
  decisions on suggestions survive the same deletions: they are keyed by provider account,
  provider message ID and title fingerprint, never by a cached row. Every change bumps
  the action's revision, and every change except restoring a deleted action needs the
  revision the caller saw. Change decisions and actions through ORM objects, never bulk
  UPDATE or DELETE statements, which leave loaded rows stale.
- Projects and life areas are owner records with no account foreign key. Project/area
  edits and lifecycle changes need their loaded revision. Archive keeps tasks and links;
  area removal soft-deletes the area and detaches projects together, bumping project
  revisions. Archived projects accept edits to already linked tasks, but no new links.
  Task activity stores project/area IDs at the time of each change, with no foreign key
  from those snapshots, so organization changes cannot rewrite completion history.
- A task's priority and tags are the owner's: AI runs, proposals and adding an email never
  set them. A tag has no record of its own; tasks share it when their `name_key`
  (`tag_key`, NFKC case folding) matches, and a tag a task gains takes the spelling other
  live tasks already use; respelling one a task has respells it on every live task, a
  revision of each. Tags change only through `ActionRepository.set_tags` and
  `respell_tag` (ORM rows).
- Repeating tasks (B05): each occurrence is an ordinary task, and closing one never rewrites
  another's history. The next occurrence is made in the transaction that completes or skips
  one, from the series' template (never from the occurrence just closed), dated by the rule's
  first date after both its occurrence date and today in the owner's zone, so missed dates
  are never created. There is at most one per series and date, and at most one live next
  occurrence per occurrence. Reopen and undo remove a next occurrence only while it is
  untouched (open, live, revision 1, and nothing made for it: no draft, focus place, work
  block or proposal). The target date reschedules an occurrence; the
  occurrence date never changes. AI runs never touch a series; a proposal that completes an
  occurrence makes its next one, as completing it would.
- Deadlines (B06, ADR 0021): the `deadline_*` columns hold the current deadline whoever set
  it, and `deadline_origin` is set exactly when there is one. Only the owner's
  `DeadlineChange` (through `ActionService.create` or `save`) sets an owner deadline; AI runs
  never write a deadline, and only an applied follow-up proposal replaces one, as the email's,
  with Undo. An owner deadline is a date from 2000 to 2099 or an instant, with no phrase
  (`deadline_text` NULL; it stays an email's words). Replacing or clearing an email's deadline
  first copies it to `stated_deadline_*`. A deadline change re-suggests the target but never
  moves or sets the target date, and moving a target date or an event never moves a deadline.
- Waiting and commitments (B08, ADR 0022): a task's person is the owner's text, cleaned by
  `clean_person` (one line, at most 120 characters) and matched by `person_key`; only an
  owner's `ActionEdit` sets it, and accepting a suggestion, AI runs, proposals and adding an
  email never set or change it. `waiting_since_utc` is set exactly while ownership is
  `waiting_for`: becoming waiting sets it to now, staying waiting keeps it, becoming the
  owner's clears it, and a repeat's next occurrence waits from when it is made. AI drafting
  never sends the person.
- Checklists in place (B04): ticking, unticking or adding a step outside the editor changes
  only a live, open task, needs the revision the caller saw, and is one revision recorded as
  `edited` (never a new activity kind without a migration: older builds must read the
  history). A tick that changes nothing makes no revision. An added step is this occurrence's
  alone; a series' template changes only with its rule. Ticking the last step never
  completes a task.
- Drafts and notes belong to the owner (ADR 0012). They may store the owner's writing
  (title or subject, recipients as typed, body and its versions), a source-email snapshot
  (subject, sender address, link, received time) and an action link with a title snapshot.
  They never store a downloaded incoming body, and Forewit never inserts quoted incoming
  history; text the owner types or pastes is stored as entered. They have no account
  foreign key and survive message, cache, account and action deletion; delete is soft and
  restorable. A draft keeps at most 100 versions. Draft text never reaches logs,
  exceptions or files other than an export the owner chooses. Drafting never writes to the
  mailbox, has no "sent" state and never changes an action. Every change needs the
  revision the caller saw, except restoring a deleted draft; drafts and versions change
  only through ORM objects.
- A note's links (ADR 0027) are the owner's: one project and one task at most, changed
  only by `DraftService.set_project` and `set_task` on a live note, with the revision the
  caller saw, one revision each and no version; a no-op makes none. Other draft kinds
  take no project. A new project link needs a project that isn't archived; a note keeps
  an archived one it has. A task link needs a live task and snapshots its title; linking
  never changes the task. A note's project is never inferred from its task; AI runs,
  proposals and capture never set it, and AI drafting never sends it. Only a note has a
  project (`ck_drafts_project_only_for_notes`).
- Owner preferences are one revisioned SQLite row (ADR 0014); every save needs the revision
  the caller saw, and the row changes only through ORM objects. Sender exclusions are
  enforced by services, never only the UI: a message from an excluded sender is never
  selected, included, downloaded, analyzed or offered to AI drafting. Every accepted sender
  rule can match a sender (`normalize_exclusion`); anything else is refused. Sender rules
  are the owner's text: show them to the owner, but log only their count. Unreadable
  preferences fail closed (`PreferencesUnavailableError`) wherever data could be sent; display-only
  views fall back to the system time zone.
- Local email consent (`local-1`) belongs to one Gmail account and is independent of Groq
  brief/drafting consent and cloud automatic-analysis limits. Selecting local mode or
  downloading a model grants nothing. Restore revokes every local disclosure; local
  revoke works without vault access. No provider is composed for a local brief until
  Part 4 connects the local path; never fall back to the cloud.
  Changing analysis mode, including a reset from local to cloud, clears automatic-send
  permissions in the same transaction; ordinary preference edits preserve them. The
  desktop's Revoke AI consent action revokes both local and Groq grants for Gmail accounts.
- Ranking (ADR 0020) stays local, deterministic and explained: every rule shows its reason.
  It learns only from the owner's own choices (picks, declines, Inbox marks, actions and
  kept briefs), never from AI output, and reads only senders' addresses for it. Important
  senders never override an exclusion, a decline, the review or the limit. Of the
  `List-Id`, `List-Unsubscribe` and `Precedence` headers only whether they mark bulk mail
  is kept, never their values. A message carries at most one review mark (declined or
  wanted); the Inbox page's mark is local and never reaches Gmail.
- Precedence: for AI limits, an explicit `MAILBRIEF_*` variable, then the saved
  preference, then the default; only `AI_LIMIT_FIELDS` are read from preferences, and only
  when absent from `Settings.model_fields_set`. For CLI `--timezone`, `--tone` and
  `--length`, the flag, then the saved preference, then the default.
- A brief covers one local day in the owner's zone. Briefing a past day is explicit, one
  day per run, at most 7 days back, and its coverage line says what it covers; nothing
  briefs past days automatically. `BriefService` enforces the date bounds before Gmail is
  contacted or anything is written, and only today's window advances the account's last
  complete sync.
- Thread tracking (ADR 0015) reads metadata only (`threads.get`, `format=metadata`), for the
  live, open actions of the connected account: at most 25 threads and the newest 20
  messages per thread after the action's latest source, during today's sync or brief only.
  Drafts, trash and spam are ignored, and no folder or label (the Sent folder included) is
  ever listed. A cached message a thread check sees in Trash or Spam is deleted from that
  account's cache, and a body labelled TRASH or SPAM is never read. A reply never changes
  an action: activity is derived, and only the owner's `mark_thread_seen` moves the
  watermark. A failed or stopped check never fails the sync.
- Follow-up proposals are the only AI-derived path to change an action, and only the owner
  applies them; applying never touches title, notes, steps or ownership, and never moves a
  target date the owner changed. Deriving proposals never changes an action, applying and
  undoing make one revision each, and a dismissed proposal is never proposed again.
- An email proposes to at most 3 actions ever: the actions that already have a proposal
  from it, in any state, use up its slots; a message the owner sent, or that comes from one
  of the account's addresses, proposes nothing.
- Replies outside today's Inbox (ADR 0016) join only today's run, only when a thread
  service is composed: cached messages in tracked threads, newer than the thread's
  baseline, never the owner's own, never from an excluded sender, not yet analyzed at the
  current schema, at most 3 a run, found from the cache with no Gmail call. They go
  through the same review, consent and messages-per-brief limit. They come only from the
  threads the same run's check read successfully. They are labelled in the review, in
  their own last brief section and in the coverage line.
- Only the owner adds an email to an existing action (`accept_into`), and only to a live,
  open one; the brief offers it only for actions with a source in the email's thread in the
  same account, it makes one revision, and its undo works only while the action is
  unchanged and never removes an email-derived action's last source; undoing the first email
  addition to a manual task restores its original zero-source state.
- Runs through a day are cumulative: the day's saved brief is carried forward ahead of the
  automatic selection, within messages per brief. A carried message drops out only when it is
  uncached, was an Inbox message since archived, is blocked or is declined; one the brief
  listed as an outside reply keeps that status. "Ready" counts only new messages.
- Automatic runs (ADR 0017) send nothing unless the active consent carries an explicit
  permission (`auto_send_limit`, 1–10, cleared by revoking); with none they only sync, check
  threads, rank and count. The permission belongs to the connected account and at most one
  account holds one: granting clears every other account's, and 0 clears them all.
  `brief --automatic` implies `--silent-only`. They brief today only, run only while the
  desktop is open, coalesce, never open panels, never download bodies without permission,
  and defer messages over the cap. A message the owner left out (an unchecked pick or
  `--exclude`) is never auto-selected.
- Credentials live only in the OS credential store (Gmail: Windows Credential Manager or
  the macOS Keychain, chosen explicitly, with no plaintext or automatic fallback).
- Configured packages may bundle the developer's registered Desktop OAuth client ID and
  native-app client secret (never user tokens or API keys). Custom client files remain an
  explicit override. See `docs/google-signin-inbox.md`. Interactive Inbox browsing reads
  one metadata page (50 initially, 100 on request), without a date cutoff; its all-mail
  search is explicit and separate from the local command palette. Browsing never
  reconciles absence across all dates, advances the daily-brief watermark or downloads
  bodies. The separate full-scan service reconciles only after complete enumeration.
  Browsing cursors live in memory, scoped to account and query; cached metadata persists.
- The Groq API key lives only in that OS vault, under `MailBrief.Groq`.
- Provider JSON stays inside its adapter; services and storage use domain models only.
- Timestamps are timezone-aware UTC. Never use naive datetimes or `datetime.utcnow()`.
- Gmail access is read-only (`gmail.readonly`). No mailbox writes or sends.
- Email content is untrusted input. It can never authorize actions, settings changes,
  mail writes or sending other messages to the AI.
- Local AI setup (`services/local_setup.py`, P0-B2) is the only local-AI step that uses the
  internet: only when the owner asks, sending no mail or account data, and only to the
  manifest's hosts over HTTPS. A downloaded file is used only once its size and SHA-256
  match the manifest; an archive path that could leave its folder stops unpacking. Changing
  a pin means editing `providers/local/manifest.py` and rerunning the benchmark.
- Search (`services/search.py`) reads only local SQLite: never a provider, never a body
  (none is stored), and mail only for the connected account. The query is the owner's
  text: log only counts, and keep it out of exceptions.
- Schema changes need a new additive Alembic revision; never edit an existing revision.
- No blocking network or database work on the Qt main thread (M5).
- Calendar (ADR 0018): AI never changes the calendar by itself, only through proposals the
  owner applies. Deadlines and target dates are never stored as events or moved by them.
  Google writes go only to the calendar Forewit creates: never to other calendars, never
  guests or invitations. One Google sign-in serves Gmail and Calendar (ADR 0019): it asks
  for `gmail.readonly`, `calendar.events.readonly` and `calendar.calendarlist.readonly`,
  requires only the first, and adds nothing else until phase 4. Every Calendar request names its
  `fields`: descriptions, locations, guests and conference details are never fetched or
  stored. Only calendars shown in Google Calendar are cached; one the owner unticks goes
  with its events. The Google cache is excluded from exports and from the cleanup
  fingerprint.
- Busy/Free and working hours (ADR 0029): an event's `busy` is the owner's choice, or its
  shape's default (`shape_busy`: timed busy, all-day free); a Google event's is Google's
  `transparency`, the only field P3-2 added to the request. Only busy events take time or
  overlap; an all-day busy event is a day off. AI never chooses Busy or Free: a planned
  update keeps it unless the shape changes, and only busy Google times are sent. Working
  hours are one revisioned `working_hours` row, never the preferences row; unreadable hours
  count as none and never stop anything. Free time and the tasks to schedule are worked out
  locally and never sent. Making a block from the tasks to schedule never changes the task.
- Workload and conflicts (ADR 0030) are a read model over the range the window read
  (`domain/workload.py`): no table, no write, never sent to AI. A block's conflicts and a
  save's warnings come from one function, `conflicts_for`. Past days are never flagged, and
  a provisional day (Google Calendar on with the day missing or over a day old, or a focus
  task without an estimate) is never shown as fitting.
- Work blocks (ADR 0028) are the owner's planned time for one task, never measured work. A
  block never changes its task: `WorkBlockService` never writes `actions` (no revision,
  activity, deadline or target date), and moving a deadline or target date never moves a
  block. Every write needs the revision the caller saw (except restoring an unscheduled
  block), makes one block revision and goes through ORM objects. Only a live, open task
  takes a new or moved block. Overlaps and ending after the deadline are local warnings
  that never stop a save. AI never sees or changes blocks.
- Once any real database has run a migration, including the owner's from a source run
  of an unmerged branch, never edit that migration; fix forward with a new revision.

## AI analysis rules (M4)

- Send only shortlisted messages, and only these seven minimized fields: `message_key` (a
  random per-run key), `subject`, `sender`, `received_local`, `time_zone`,
  `body_truncated` and `body` (truncated plain text).
- Never send tokens, account or tenant IDs, provider message IDs, source links or
  attachments.
- Require explicit first-use consent, and disable provider-side response storage where
  supported; enable Groq Zero Data Retention in Console Data Controls before live mail use.
- Accept only responses that validate against the versioned Pydantic schema. Cache by
  input hash, provider, model, prompt version and schema version.
- Store evidence at most 300 characters long and never as a whole body (under 80% of it);
  a body of one or two characters, with room for one character at most, stores none.
- Each result may carry up to five action suggestions (schema version 6). A suggestion
  whose evidence does not quote the email is dropped; a deadline phrase that does not quote
  it drops only that suggestion's deadline. Target dates come from Python, never the model.
- Each result also carries a follow-up signal (schema version 7, ADR 0016), judged from the
  email alone: nothing about the owner's actions is ever sent. Its quote is checked before
  the suggestions and counts toward the evidence total; a signal that doesn't check out
  becomes none and never fails the message.

## AI drafting rules (M7, ADR 0013)

- Send only the parts the owner ticks for that generation: the email being replied to
  (subject, sender name only, received time in the owner's zone, and its body, downloaded
  then, prepared like the brief's and never stored); the linked action (title, ownership,
  target date, deadline phrase, or the date and local time of a deadline the owner set,
  steps in order up to 2,000 characters in all, at most 2,000 characters of notes); the draft's current title and body (at most 8,000 characters).
  Always: kind, tone, length, the owner's instructions (at most 1,000 characters) and
  today's date.
- Never send the sender's address, To or Cc, other emails, attachments, other drafts or
  actions, Gmail or Forewit IDs, or credentials. The chosen parts are sent as written, so
  addresses, links or numbers inside them are sent too, and the consent text says so.
- Every generation shows a preview of every part with its size and waits for approval.
  First use needs explicit consent, recorded per provider for the owner (not per account)
  before anything is sent; revoking AI consent revokes it too. Declining sends nothing.
- The output schema has no recipients. Python cleans the text, removes quoted history,
  bounds it (body 1 to 8,000 characters, subject 200 and only for emails and notes, at most
  five missing-context items of 200) and rejects a body that copies 200 or more characters
  of the source email. Missing facts stay as `[[placeholders]]`.
- The owner's text is saved as a version before the call; a result becomes a "generated"
  version with its generation record. A failed, declined or cancelled generation leaves the
  text unchanged.
- Groq's HTTP 400 `json_validate_failed` is an incomplete answer (retried once), not a
  rejected request, for briefs and drafting alike.

## AI calendar rules (ADR 0018, phase 3)

- The model only proposes changes to the owner's own events; `validate_plan` in
  `services/calendar_planning.py` checks each one, and nothing changes until the owner
  applies. Never let the model name Google events or actions, or change notes, deadlines
  or target dates.
- Send the instruction, the local date and zone, and the owner's events in the four-week
  window (titles and times only). Google busy times, their titles and open actions go only
  when the owner ticks them, and the preview shows each part's size.
- Parse dates and times in Python in the owner's zone; never trust the model's arithmetic.
  Overlap warnings are computed locally and never sent; work blocks join them only as
  local warnings ("Task · title") and are never sent (ADR 0028).
- Apply all kept changes in one transaction (`EventService.apply_changes`), and undo them
  together only while every event is as the apply left it (`undo_changes`).

## Dependencies

- Python 3.13 only, managed with `uv`; direct dependencies in `pyproject.toml`, exact
  versions in `uv.lock`. Update both together.
- Development environment: keep the checkout and its `.venv` out of folders synced by
  iCloud Drive (Desktop & Documents), OneDrive or Dropbox. Those tools set the macOS hidden
  flag on `.venv` files, and then Python 3.13 skips hidden `.pth` files and Qt skips hidden
  plugins.
- Do not add: Google API client libraries, the Microsoft Graph SDK, `python-dotenv`,
  generic retry libraries, HTML parsing libraries (use the standard library if
  HTML-to-text is needed), or local-model libraries.

## Code and tests

- Strict mypy and Ruff (100-character lines). Annotate every function.
- Tests mirror `src/` under `tests/`. Use `tests/factories.py` (including `Clock` and
  `Ids`), the `database` and `session` fixtures in `tests/conftest.py`, `respx` for HTTP
  and injected sleeps/clocks. Tests never touch external networks (localhost is fine), real
  credentials, real mailboxes or paid AI.
- Coverage: at least 80% overall and 90% for every module in src/mailbrief/services/.
  `scripts/check_coverage.py` finds the modules itself and enforces the combined
  statement/branch gate.
- Tests run SQLite without disk flushes, CI runs tests in parallel (`pytest -n auto`), and CI
  runs macOS in Toronto and Windows in Tokyo time.
- `tests/unit/test_source_rules.py` enforces three source rules, naming the file and line of
  each slip: no strftime directive that follows the system language (`%a`, `%A`, `%b`, `%B`,
  `%p`, `%c`, `%x`) anywhere in `src/mailbrief`, since weekday and month names come from
  `ui/deadline_text.py`; no hex colour literal in `src/mailbrief/ui` outside
  `ui/theme/tokens.py`; and no `QListWidget` made or subclassed in `src/mailbrief/ui`
  outside `ui/lists.py`. It also scans for privacy and boundary slips: a log call that
  passes a private name (a token, key, headers, body, query, prompt, sender rules; `len()`
  of one and `*_count`-style names are fine); a Google scope other than the three read-only
  ones; a writing request in the Gmail, Calendar or shared Google request code; a naive
  `datetime` clock; a network library, or a provider adapter, imported by `domain`,
  `storage`, `text`, `services` or `ports` (local setup alone reads the local adapter's
  manifest and download guard); workload, free time or scheduling imported, outside the
  desktop, by a module the scan doesn't list as local planning code; a forbidden dependency in `pyproject.toml`; and a function that opens a URL
  without checking it is Gmail's or Calendar's in that same function.

## UI work

- Use the `/mailbrief-ui` skill (`.claude/skills/mailbrief-ui/`) for any change under
  `src/mailbrief/ui`.
- The file map and the rest of the UI rules (screenshot gallery, pixel snapping, paused
  collection, `SafeList`, `take_widget`) are in the skill's `references/layout-and-rules.md`;
  read it before any UI change.

## Dormant Microsoft notes

- Microsoft `provider_account_id` is always `{oid}.{tid}` (the MSAL `home_account_id`),
  never the bare Graph object ID.
- Graph is called directly with httpx. The original Microsoft-first specification is
  `docs/archive/microsoft-mvp-plan.md`.

## Commands

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run mypy --platform win32 src tests
uv run mypy --platform darwin src tests
uv run pytest
```

CI type-checks on both Windows and macOS, so run mypy for both platforms before pushing.

Agents run checks through `python scripts/qa.py changed` after a change and `python
scripts/qa.py full` before a push (`area <test paths>` for chosen tests). `qa.py hotspots`
lists the least-covered modules. A bare `pytest <path>` prints the whole coverage table and
fails the 80% gate, so direct calls need `-q --no-cov`. Rules and ledger: `docs/qa.md`.

CI runs on pull requests and on pushes to main; a push to a branch without a pull request runs
nothing.

## Git workflow

- Branch from an up-to-date `main`; one pull request per plan; pull requests target `main`.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Stage explicit paths. Never commit `.venv/`, `out/`, coverage files, databases,
  OAuth client JSON files or anything from the credential store.
