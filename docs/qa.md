# QA sweep: rules and ledger

A budgeted check of the whole project, fixing as we go. An agent starting a QA or fix
session reads this file first and updates it last. It records what was checked and what
was found. Acceptance stays the owner's: nothing here marks a feature accepted.

## Why it is shaped this way

Measured on 2026-11-12 at `24f6216`: the source is 72,000 lines, half of it `ui/`, and the
tests are 89,000. Reading both once is about two million tokens, and a session holds about
70,000 before its first message. So:

- machines check first (`scripts/qa.py`, then CI);
- a session greps before it reads, and reads whole files only where a bug would cost data
  or privacy;
- the owner checks the UI in the real app, from one run-sheet.

## Rules for every session

1. One slice per fresh session. Its brief is its row under [Slices](#slices). It ends by
   updating this file.
2. Mechanical slices run on Sonnet at medium effort. Deep reads run on Fable at high
   effort, never max.
3. Run tests, lint and types only through `scripts/qa.py`: `changed` after a change, `full`
   before a push, `hotspots` for the least-covered modules. A bare `pytest <path>` prints
   the coverage table for every file and fails the 80% gate, so a direct call needs `-q --no-cov`.
4. Grep, then read a range. Give Glob a folder (`src`, `tests`, `docs`), never the
   repository root. Never read these whole: `src/mailbrief/ui/main_window.py`,
   `src/mailbrief/diagnostics/gmail.py`, `.claude/skills/mailbrief-ui/references/spec.md`,
   `docs/future-implementations.md`, `docs/m8-daily-operation.md`, or a test file over
   1,000 lines.
5. Send independent tool calls together in one message.
6. No subagents, and no `/code-review` above `medium`, unless the owner asks.
7. At most 10 screenshots a session, only of pages the slice touched.
8. Leave package builds, macOS and the full matrix to CI. Read results with
   `gh pr checks`, and a failure with the tail of `gh run view --log-failed`.
9. Work in the main checkout, one session at a time, so every session uses the same
   environment and no two write the same files.

## Budget

Defaults; the owner changes them here.

- At the start and end of a session, note the 5-hour, weekly and per-model percentages and
  the context size in `scratch/qa/usage.md`. That log is local and never committed.
- Start a slice only with 40% or more of the 5-hour window left.
- Stop and hand off (commit, and a "next" note in the slice's row) when the slice has used
  25 points of the 5-hour window or the session passes 250,000 tokens of context.
- QA takes at most half of a week's limit, so feature work still fits.
- Measured on Sonnet at medium effort: Phase 0 cost 3 points of the 5-hour window, slice R
  2 and slice B1 3, each ending near 130,000 tokens of context. The five Sonnet slices left
  (B2, B3, D, E and fix sessions) project to about 15 points in all, so they fit.
- Measured on Fable at high effort: C1 cost 35 points of the 5-hour window and 9 of the
  Fable week, ending at 228,000 tokens of context. That is over the 25-point stop. The owner
  decided on 2026-11-12 to keep E, C5 and C4 all the same. So a deep read starts in a fresh
  5-hour window, reads only its slice's files whole, and greps the helpers they lean on.
- C2, read that way, cost 33 points of the 5-hour window and 9 of the Fable week, ending
  at 210,000 tokens of context: over the 25-point stop again. It stood at 19 points when
  both fixes were committed; the other 14 came across the five-minute wait for `qa.py full`
  and the turns after it, each of which re-reads the whole session. So a deep read should
  start `qa.py full` in a fresh session, or end before it and leave the run to CI.

## Fixing as we go

- Every finding gets a line under [Findings](#findings).
- Fix it in the same session when a failing test reproduces it and the fix is local: no
  migration, no change to documented behaviour, under about 50 lines. Failing test first,
  then the fix, then `qa.py changed`.
- Otherwise record it and move on. It gets its own short session, briefed by its line.
- Where a doc and the code disagree and it isn't clear which is right, record both
  readings for the owner. Don't guess.
- One branch and draft pull request per slice (`qa/<slice>`), one commit per finding. The
  owner merges after Windows CI. A slice branches from `main`, or from the previous
  slice's head while that one is unmerged.
- This repository is public. A finding that could expose mail, credentials or other
  private data is fixed before it is described here; until then it stays in `scratch/qa/`.
- Never edit an existing migration, weaken an invariant to pass a test, or mark anything
  accepted.

## Setup (Phase 0)

| Step | State |
| --- | --- |
| Checkout on current `main`, branch `chore/qa-tooling`, this file | Done 2026-11-12 |
| `scripts/qa.py` with `tests/unit/test_qa_script.py` | Done 2026-11-12 |
| Baseline: `qa.py full` twice, `hotspots`, and the output size of a bare single-file `pytest` run against `qa.py changed` | Done 2026-11-12 |
| AGENTS.md: this file in the docs table, `qa.py` under Commands | Done 2026-11-12 |
| AGENTS.md: UI-only detail moved into `.claude/skills/mailbrief-ui/references/layout-and-rules.md` (47,505 → 41,407 bytes) | Done 2026-11-12, in PR #113 |

### `scripts/qa.py`

Standard library only. It runs each tool as `sys.executable -m …`, so it works from the
project's environment directly or through `uv run`, and it sets `QT_QPA_PLATFORM=offscreen`
when that is unset.

| Command | Runs |
| --- | --- |
| `changed [--base main]` | Ruff format and lint on the changed files, mypy, then the tests that mirror the changed modules (`src/mailbrief/**/<name>.py` → `tests/**/test_<name>*.py`), the changed test files and `tests/unit/test_source_rules.py`. A module with no mirror falls back to its package's test folder and says so |
| `area <test paths>` | Those tests, in parallel |
| `full` | Ruff, mypy for this platform, `win32` and `darwin`, every test with coverage, then the service gate of `scripts/check_coverage.py` |
| `hotspots [-n 15]` | The least-covered modules in `coverage.xml` |

It prints one `ok` or `FAIL` line per step with its duration, up to 10 failing test IDs
with one message line each (read from `--junitxml`), and the path of the full log under
`scratch/qa/`: about 30 lines at most. Tests run with `--no-cov` except in `full`, where
the coverage table goes to the log. It exits non-zero when a step fails. `hotspots` and
the service gate share one function for the combined statement and branch count.

### Baseline

Measured 2026-11-12 on `chore/qa-tooling` (main at `24f6216` plus the runner), Windows,
`.venv` without the `forewit*` console scripts.

- `qa.py full`: Ruff, mypy (this platform, `win32`, `darwin`) and the service gate pass.
  5,559 tests in about 300 s (309 s and 302 s on two runs), 4 failures, the same 4 both
  times; no flaky test. Report size: 14 lines when four tests fail, 7 when none do.
- The 4 failures are environmental, not findings: `tests/unit/test_branding.py`
  (`test_installed_commands_outside_checkout[forewit-forewit]`,
  `[forewit-gmail-diagnostic-...]`, `[forewit-ms-diagnostic-...]` and
  `test_gmail_aliases_show_the_same_synthetic_preferences`) raise `FileNotFoundError`
  because this `.venv` had only the `mailbrief*` console scripts. After `uv sync --locked
  --all-groups` created the `forewit*` commands, `qa.py area tests/unit/test_branding.py`
  passes (10 tests), so the full run is expected to be clean; it was not repeated. CI was
  never affected.
- Output of one file, `tests/unit/domain/test_intervals.py`: a bare `pytest` prints
  25,157 characters (the coverage table for every file); `qa.py area` prints 28.
- The 15 least-covered modules (combined statements and branches, `qa.py hotspots`):

| Coverage | Module |
| --- | --- |
| 63.9% | `ui/glass/system.py` |
| 66.4% | `ui/calendar_preview.py` |
| 76.1% | `ui/smoke.py` |
| 78.8% | `ui/project_page.py` |
| 81.0% | `diagnostics/metric_smoke.py` |
| 81.4% | `storage/dashboard_validation.py` |
| 82.2% | `providers/local/device.py` |
| 83.0% | `ui/scheduling_view.py` |
| 83.7% | `ui/block_editor.py` |
| 84.1% | `providers/microsoft/cache.py` |
| 84.6% | `ui/upcoming_view.py` |
| 85.1% | `app.py` |
| 86.2% | `storage/types.py` |
| 86.5% | `ui/main_window.py` (695 lines missed) |
| 86.9% | `ui/list_rows.py` |

All services modules meet 90%. Nothing here is below the 80% gate, so slice D, not
new tests, should target the UI modules at the top.

## Slices

In this order: privacy, then data, then durability, then the newest code.

| # | Slice | Method | Reads | Model | State |
| --- | --- | --- | --- | --- | --- |
| R | The owner's run-sheet | One page-by-page checklist, `docs/acceptance-runsheet.md`, from the "Owner acceptance" sections of `p1-*.md`, `p2-*.md`, `p3-calendar.md`, `p3-scheduling-addons.md` and `p4-insights.md`. Each step has an ID and a link back; the live Gmail and Groq checklists are linked, not rewritten. The owner walks it in the real app and sends back failed IDs with one line each | About 15 doc sections | Sonnet | Written 2026-11-12, merged in #113; awaiting the owner's walk. Next: B1 |
| B1 | Invariants: secrets, bodies, what goes to AI | For each AGENTS.md invariant, grep for the test that enforces it and confirm in 30 lines or fewer. A gap gets a test; rules a scan can check go in `tests/unit/test_source_rules.py` | Test names, short ranges | Sonnet | Done 2026-11-12, draft PR #114. Next: C1 (it takes the remaining AI-boundary checks marked "to C1" below) |
| C1 | Deep read: what leaves the machine | `services/analysis.py`, `drafting.py`, `calendar_planning.py`, `consent.py`, `providers/groq/provider.py`. From B1: confirm that summary, action and suggestion text is stripped of control and format characters before it is stored, and that mail content cannot authorize an action | About 2,800 lines | Fable | Done 2026-11-12, draft PR #118 on `qa/c1-ai-boundary`. Two findings fixed, one open. Next: C2, in a fresh 5-hour window |
| C2 | Deep read: owner-record writes | `services/actions.py`, `storage/actions.py`, `services/recurrence.py`, `services/writes.py` | About 2,600 | Fable | Done 2026-11-12, draft PR on `qa/c2-owner-writes` (stacked on C1). Two findings fixed, two open. Next: C3, in a fresh 5-hour window |
| C3 | Durability | `storage/backup.py`, `recovery.py`, `migrate.py`, `migration_forks.py`, `services/data.py`, `diagnostics/backup.py`, plus a real offline backup, verify and restore round trip and an upgrade from an early revision, on a synthetic database | About 1,050 | Fable | Not started |
| B2 | Invariants: owner records | As B1: revisions, tags, repeats, deadlines, waiting, checklists, drafts and notes, preferences, receipts, work blocks, Busy/Free, workload, scheduling and Insights | Test names | Sonnet | Done 2026-11-12, draft PR on `qa/b2-owner-records` (stacked on B1). Next: B3 |
| C4 | Deep read: newest code | Scheduling and workload from PRs #107 and #109: `services/scheduling.py`, `domain/scheduling*.py`, `domain/workload.py`, `domain/planning_intervals.py`, `services/availability.py` | About 1,900 | Fable | Not started |
| B3 | Invariants: mail and automation | As B1: ranking, brief dates, thread tracking, proposals, outside replies, automatic runs, read-only Gmail, Inbox browsing, Google Calendar | Test names | Sonnet | Done 2026-11-12, branch `qa/b3-mail-automation` (stacked on B2). Next: C2 or D |
| C5 | Deep read: the daily run | `services/brief.py`, `proposals.py`, `threads.py`, `sync.py` | About 1,800 | Fable | Not started |
| D | UI, without reading it | The `MAILBRIEF_UI_SHOTS` gallery at 1x in light and dark. For each view added since October (`insights_*`, `scheduling_view`, `date_picker`, the Day view), list its tests by name and add the missing empty, one, many and long-text cases. Fix what the run-sheet reports. From B1: check that every widget showing mail or AI text is plain text (`plain_label`, `wrap_label` or `drawText`), with a test or scan if one can hold it | Test names, 10 images at most | Sonnet with `/mailbrief-ui` | Not started |
| E | Performance, optional | Re-run the large-profile probe in `p2-workspace.md` and `scripts/benchmark_insights.py`; look only at regressions against the recorded numbers | None | Sonnet | Not started |

Anything else (the Gmail and Calendar adapters, `domain/`, the repositories, `text/`,
`infra/`) gets a deep read only if `hotspots` or a finding points at it.

Not in the sweep: a deep read of P4 Insights, which has three recorded reviews and PR #111;
splitting `ui/main_window.py`, the largest standing cost of any UI change, which is a
refactor to decide separately; the dormant Microsoft code; live Gmail or Groq checks;
native macOS checks.

## Slice B1: privacy and AI-boundary invariants

Method: test names grepped under `tests/`, the key assertion read in a few lines where the
name was not enough. "Gap closed" means a test was added; tests marked `scan` are in
`tests/unit/test_source_rules.py` and name file and line.

| Invariant | Enforcing test | State |
| --- | --- | --- |
| No log line carries a token, key, headers, body, draft, query, prompt or sender rules | `test_no_log_call_passes_a_token_key_body_query_or_prompt` (scan, AST over every logging call; `len()` of a private name and count-style names pass) | gap closed for named values; an exception's text and a private value under a plain name are not seen |
| Search logs only counts, never the query | `test_a_search_logs_nothing_of_the_query_or_the_titles_it_found` (`test_search.py`; also the refused-limit error) | gap closed |
| Gmail read-only; only the three read-only scopes | `test_the_source_names_only_the_read_only_google_scopes` (scan; also pins `SIGN_IN_SCOPES` and refuses Gmail's full-access scope); `test_a_grant_wider_than_read_only_is_refused_and_never_saved` | gap closed |
| No mailbox or calendar write from the adapters | `test_google_adapters_make_no_writing_request` (scan of the adapters and the shared request code, by method and by verb); `test_metadata_request_is_read_only` | gap closed |
| Timestamps never naive | `test_timestamps_are_never_naive` (scan) | partly: the common `datetime` spellings. A naive `datetime(...)` literal is not looked for, and `date.today()` in `ui/inbox_reader.py` and `ui/action_editor.py` uses the system day: to slice D |
| Provider JSON stays in adapters | `test_core_packages_import_no_network_library` (scan) | partly: no network library outside the adapters. C1 added `test_core_packages_import_no_adapter`: no core package imports an adapter |
| Forbidden dependencies absent | `test_no_forbidden_dependency_is_declared` (scan) | gap closed |
| Only `mail.google.com` links open | `test_open_in_gmail_only_for_gmail_https_links`, `test_open_in_gmail_opens_only_gmail_links`, `test_an_unlinked_draft_says_so_and_only_gmail_links_open`; `test_every_url_open_is_behind_a_link_check` (scan, per function; only the sign-in imports `webbrowser`) | covered, scan added |
| Calendar names its `fields`; no descriptions, locations, guests, conferences | `test_events_request_names_its_fields_and_follows_pages`, `test_calendar_list_request_names_its_fields`; `test_the_field_lists_are_exactly_what_forewit_reads` pins both lists | gap closed |
| Groq sends the seven minimized fields; no response storage | `test_groq_provider.py` (`REQUEST_KEYS`, `"store" not in body`, message keys); `test_analysis_request_enforces_minimized_body_limit`; `test_redirect_cannot_forward_key_or_mail` | covered |
| Drafting sends only ticked parts, with a sized preview and consent first | `test_no_parts_sends_only_the_always_sent_fields`, `test_the_preview_shows_each_chosen_part_and_its_exact_size`, `test_consent_is_recorded_before_the_call` | covered |
| Calendar planning sends only busy Google times, titles and times | `test_only_busy_google_times_are_sent_or_warned_about`, `test_what_is_previewed_and_sent_is_what_the_dialog_shows` | covered |
| Evidence at most 300 and under 80% of the body; none for a one-or-two-character body | `test_validate_candidate_stores_long_evidence_at_most_300_characters`, `..._strictly_under_80_percent`, `test_a_body_too_short_to_quote_stores_no_evidence` | covered |
| No body outside the evidence reaches disk, output or logs | `test_the_body_outside_the_evidence_never_reaches_disk_output_or_logs` | covered |
| AI text stripped of control and format characters | `test_brief_show_prints_no_terminal_control_characters_the_ai_wrote`, `test_a_deadline_phrase_keeps_no_control_character_that_could_move_a_terminal`, `test_normalize_removes_invisible_and_control_characters` | covered for output, phrases and email text; C1 confirmed the storage-side strip (`test_validate_candidate_cleans_text_the_model_wrote_itself`) and added evidence quotes |
| Local mode never falls back to cloud; flag off fails closed | `test_unavailable_local_never_constructs_cloud_or_starts_model`, `test_local_mode_never_enters_cloud_brief_path`, `test_local_twenty_message_brief_never_enters_cloud_and_reuses_cache`, `test_local_ai_requires_explicit_feature_flag`, `test_local_setup_requires_feature_flag` | covered |
| Local setup: HTTPS and listed hosts on every redirect, size and SHA-256, unsafe archive paths | `test_the_download_client_refuses_plain_http_and_unlisted_hosts`, `test_a_redirect_to_an_unlisted_host_stops_setup_before_reaching_it`, `test_wrong_bytes_are_deleted_and_reported_as_a_mismatch`, `test_a_body_longer_than_the_pin_is_a_mismatch`, zip and tar parent-path cases; `test_every_pinned_url_is_https_to_a_listed_host` | covered |
| Local consent separate from cloud; revoke needs no vault | `test_a_grant_is_account_scoped_and_independent_of_cloud`, `test_revoke_needs_no_vault_or_prompt` | covered |
| Sender exclusions enforced by services | `test_excluded_senders_are_never_selected_read_or_sent`, `test_an_excluded_sender_s_email_is_never_offered_or_downloaded` | covered |
| Unreadable preferences fail closed | `test_unreadable_preferences_stop_a_brief_before_gmail_or_groq`, `test_unreadable_preferences_exit_3` | covered |
| Credentials only in the OS vault; keys never printed | `test_the_default_store_uses_the_os_vault`, `test_ai_key_commands_never_print_the_key`, `test_reports_never_keep_the_servers_key_or_the_owners_folders` | covered |
| Mail content never authorizes an action; plain-text widgets | Not mapped by name. The `ui` code sets `PlainText` on its labels (grep); no test asserts that every label over mail text does | the model's side is pinned by C1 (`test_an_answer_that_obeys_the_email_still_writes_only_the_analysis_cache`); the plain-text widgets are not checked: to slice D |
| Draft text never in logs | Covered by the log scan above for drafting's own calls; `test_a_failed_check_after_cancel_is_only_logged` for the UI path | covered |

Only the scans, one search test and two calendar tests were new. Nothing failed, so there
is no B1 finding. A review of the slice tightened the scans; the Briefs page's Open in Gmail
now checks the link in the function that opens it. The log scan reads the message argument too, so an f-string that interpolates a private
name is caught. It checks calls through `logger`, `log`, `logging`, `_logger` and `LOGGER`;
a logger held under another name is not, and a name that hides what it holds is not.

## Slice B2: owner-record invariants

Same method as B1. Tests marked `scan` are in `tests/unit/test_source_rules.py`.

| Invariant | Enforcing test | State |
| --- | --- | --- |
| Every change needs the revision the caller saw; stale saves change nothing | `test_a_stale_revision_conflicts_and_changes_nothing`, `test_a_second_save_from_the_same_revision_conflicts_and_keeps_the_first`, `test_a_stale_screen_changes_no_step`, `test_stale_revisions_and_missing_blocks_are_refused`, `test_a_stale_revision_is_a_conflict` (preferences), `test_stale_save_and_delete_change_nothing` (events), `test_stale_missing_references_and_atomic_failures` (projects) | covered |
| Changes go through ORM objects, never bulk statements | `test_owner_records_change_only_through_orm_objects` (scan of `storage` and `services`; the AI-derived suggestion cache is deliberately outside it) | gap closed for the tables listed in `OWNER_TABLES` (checked to exist; `sqlite_insert(` upserts are seen). A bulk write spelled another way, or on a table not listed, is not |
| Projects and areas: archive, area removal, no new links to an archived project, history snapshots | `test_organization_lifecycle_history_and_recovery`, `test_area_removal_rolls_back_all_detachments`, `test_an_archived_project_takes_no_new_occurrence`, `test_a_note_keeps_its_archived_project_but_takes_no_new_one`; head schema: no key from activity to projects or areas | covered |
| Priority and tags are the owner's; tags share by key and respell on every live task | `test_accepted_suggestions_start_without_priority_or_tags`, `test_a_tag_keeps_the_spelling_other_tasks_use`, `test_respelling_a_tag_respells_it_on_every_live_task`; proposals: `test_applying_and_undoing_leave_the_owner_s_own_fields_alone` | gap closed for proposals. That tags change only through `set_tags` and `respell_tag` is not scanned |
| Repeats: next occurrence from the template, no missed dates, one per series and date, untouched-only withdrawal | `test_completing_makes_the_next_from_the_template`, `test_a_late_completion_skips_the_missed_dates`, `test_an_existing_date_makes_no_second_occurrence`, `test_undo_withdraws_the_untouched_next`, `test_reopening_withdraws_only_an_untouched_next`, `test_a_completing_proposal_moves_a_repeating_task_on` | covered |
| Deadlines: owner sets, email's kept for going back, 2000 to 2099, AI never writes | `test_replacing_the_email_s_deadline_keeps_it_and_the_owner_s_target`, `test_going_back_to_the_email_s_deadline`, `test_a_deadline_outside_2000_to_2099_is_refused`, `test_a_new_deadline_over_the_owner_s_says_so_and_undo_brings_theirs_back`; the proposal test above | covered |
| Waiting: person is the owner's, `waiting_since` set exactly while waiting | `test_the_wait_starts_when_a_task_becomes_someone_else_s_and_ends_with_it`, `test_accepting_a_waiting_suggestion_waits_from_then_and_names_no_one`, `test_a_repeat_keeps_the_person_and_waits_from_when_it_is_made`; proposal test above | covered |
| Checklists: one `edited` revision, no-op makes none, last tick never completes | `test_ticking_a_step_is_one_revision_recorded_as_an_edit`, `test_a_closed_task_s_plan_changes_once_it_is_reopened`, `test_an_added_step_comes_last_and_not_done`, `test_ticking_the_last_step_never_completes_the_task`, `test_a_tick_that_changes_nothing_makes_no_revision` | two gaps closed |
| Drafts and notes: owner-only, no account key, soft delete, 100 versions, notes' project and task links | `test_drafts_survive_message_account_and_action_deletion`, `test_versions_are_pruned_to_the_newest_hundred`, `test_notes_and_messages_refuse_recipients`, `test_set_project_is_one_revision_without_a_version`, `test_only_live_notes_take_links`, `ck_drafts_project_only_for_notes` in the migration tests; head schema: drafts key only to actions and projects | covered |
| Preferences: one revisioned row, fail closed, mode change clears auto-send | `test_a_matching_revision_applies_and_bumps`, `test_changing_analysis_mode_never_reuses_automatic_cloud_permission`, `test_unreadable_preferences_fail_closed_until_reset`, `test_logs_count_rules_but_never_show_them` | covered |
| Retry receipts: atomic with the record, no key to it, survive backup and purge, one command ID | `test_failed_commit_rolls_back_receipt_with_owner_records`, `test_committed_command_cannot_be_reused_with_different_input`, `test_replay_never_restores_or_recreates_removed_task`, `test_receipt_survives_restart_backup_restore_but_is_not_writing`, the note, metric and dashboard equivalents; head schema: no receipt table has a foreign key | covered; that a receipt holds no owner text is not asserted by name |
| Work blocks: never write the task, only live open tasks, overlaps and late deadline only warn | `test_schedule_move_and_unschedule_never_touch_the_task`, `test_only_open_tasks_take_new_or_moved_blocks`, `test_a_block_after_the_deadline_warns_and_moves_nothing`, `test_errors_never_carry_the_task_title`; head schema: blocks key only to actions | covered |
| Busy/Free: shape default, AI keeps it unless the shape changes, all-day busy is a day off | `test_busy_follows_the_shape_unless_chosen`, `test_a_planned_change_keeps_busy_or_free_unless_the_shape_changes`, `test_free_events_never_overlap_and_days_off_always_do`; B1 for what is sent | covered |
| Working hours: one row, unreadable counts as none | `test_unreadable_hours_count_as_none_and_can_be_replaced`, `test_hours_start_unset_and_save_with_their_revision` | covered |
| Workload: read model, no table, not sent to AI | `test_a_day_planned_past_its_time_is_overfilled_until_it_has_passed`, `test_google_makes_a_day_provisional_only_while_it_is_on`. Nothing asserts that it is never sent or stored | not checked: to C4 |
| Scheduling: one transaction with receipt, retry-safe apply, Undo only while unchanged, locks | `test_failed_batch_commit_rolls_back_every_block_and_receipt`, `test_tampering_elapsed_and_changed_undo_are_refused`, `test_locked_blocks_refuse_move_unschedule_and_batch_undo` | covered |
| Insights: logs, corrections, revisioned layouts, atomic failures | `test_completion_episodes_event_grouping_and_out_of_range_correction`, `test_starter_retry_revision_and_conflicting_editors`, `test_failed_save_leaves_layout_and_receipt_atomic` | covered; recorded reviews, not re-read |

Four tests and one scan were new; nothing failed, so there is no B2 finding. The names
were read for coverage, not the assertions behind each one beyond the ones listed as new.

## Slice B3: mail and automation invariants

Same method as B1 and B2.

| Invariant | Enforcing test | State |
| --- | --- | --- |
| Ranking is local, deterministic, explained; learns only from the owner's choices | `test_deterministic_sorting_and_tie_breaking`, `test_every_new_reason_reads_as_words`, `test_an_important_sender_ignores_what_was_learned`, `test_a_pick_that_was_not_suggested_is_remembered_and_lifts_its_sender`, `test_include_ids_count_as_a_pick_but_an_automatic_choice_does_not` | covered, except that nothing tests that AI output and analyses are ignored, or that only sender addresses are read |
| Exclusion, decline and limit beat important senders | `test_an_exclusion_wins_over_an_important_address_in_its_domain`, `test_a_decline_after_a_pick_takes_the_wanted_mark_back`, `test_the_limit_caps_automatic_and_reviewed_selection` | covered |
| Only whether `List-Id`, `List-Unsubscribe`, `Precedence` mark bulk mail is kept | `test_list_and_bulk_headers_mark_bulk_mail` (secret-token value, asserts it is not kept), `test_other_headers_are_not_bulk_mail`; `test_the_thread_request_asks_for_metadata_only` only pins the requested headers | covered |
| Brief date: today and seven days back, bounds checked before Gmail, only today's window moves the watermark | `test_today_and_the_previous_seven_days_can_be_briefed`, `test_an_out_of_range_date_fails_before_gmail_or_the_database`, `test_seven_days_back_is_allowed`, the `last_sync` assertion in the past-day brief test | covered |
| Browsing reads one metadata page, never reconciles, never advances the watermark or downloads bodies | `test_one_page_and_metadata_only` (50 and 100), `test_bounded_browsing_preserves_unobserved_cache_and_cancellation`, `test_browsing_a_page_never_advances_the_daily_brief_watermark`; the reader port has no body method | gap closed for the watermark |
| Thread tracking: metadata only, at most 25 threads and 20 messages, today's sync only, Trash and Spam forgotten | `test_the_thread_request_asks_for_metadata_only`, `test_threads_are_deduplicated_most_urgent_first_and_capped`, `test_only_later_messages_are_cached_newest_twenty_with_sent_flagged`, `test_a_past_day_never_checks_threads`, `test_a_cached_reply_gmail_discards_is_forgotten_and_comes_back_if_restored`, `test_a_failed_sync_never_checks_threads` | covered. That no folder or label is listed is true of the code (grep: `labelIds` only filters Inbox browsing) but no test or scan pins it |
| A reply never changes an action; only the owner moves the watermark | `test_a_thread_check_never_changes_the_action`, `test_marking_seen_moves_the_watermark_once` | covered |
| Proposals: at most three per email, none from the owner's own mail, dismissed never return, only the owner applies | B2's proposal tests; `test_at_most_three_actions_most_urgent_first`, `test_the_cap_is_exact_across_apply_dismiss_and_closing`, `test_the_owner_s_own_messages_propose_nothing`, `test_deriving_again_changes_nothing_and_dismissed_proposals_never_return` | covered |
| Outside replies: only today's run, at most three, never the owner's or an excluded sender's, no Gmail call | `test_archived_and_earlier_replies_are_found_newest_first_at_most_three`, `test_the_owner_s_own_mail_is_never_found`, `test_senders_the_owner_excluded_never_use_up_the_three`, `test_only_threads_this_run_read_are_drawn_on` | covered |
| Runs are cumulative: carried messages, drop rules, "ready" counts new ones | the `test_carried.py` set (drops for archived, blocked, declined; ready counts only new) | covered |
| Automatic runs: send nothing without permission, cap, one account, today only, no review, silent-only | `test_without_permission_an_automatic_run_only_checks_and_reports`, `test_it_never_sends_more_than_the_permission_or_messages_per_brief`, `test_granting_one_account_clears_the_other`, `test_an_automatic_run_never_briefs_a_past_day_and_never_contacts_gmail_first`, `test_an_automatic_run_takes_no_review_and_no_choices`, `test_automatic_cli.py` (`--automatic` implies `--silent-only`) | covered |
| Desktop runs coalesce, open no panels, wait during dialogs and other operations | `test_a_refresh_never_opens_a_review_a_consent_panel_or_a_dialog`, `test_a_refresh_waits_while_the_owner_is_in_a_dialog`, `test_a_refresh_waits_a_minute_while_another_operation_runs` | covered |
| Gmail read-only, only the three scopes | B1 | covered |
| Google Calendar: shown calendars only, unticked go with their events, cache out of exports and the cleanup fingerprint | `test_unticked_calendars_go_with_their_events`, `test_a_calendar_sync_between_preview_and_cleanup_keeps_the_preview`, `test_nonrevisioned_cache_changes_invalidate_preview`, `test_the_writing_export_names_no_mail_or_google_calendar_table` (exact allowlist) | gap closed for the export. Not pinned by name: that the cleanup fingerprint leaves out `google_*` tables, and that only shown calendars are cached |
| Calendar failures never prove free time | `test_failed_and_unavailable_calendars_keep_busy_cache_without_proving_free_time` | covered |

Two tests were new; nothing failed, so there is no B3 finding. The daily run's logic
(`brief.py`, `proposals.py`, `threads.py`, `sync.py`) still gets its deep read in C5.

## Slice C1: what leaves the machine

Read whole: `services/analysis.py`, `drafting.py`, `calendar_planning.py`, `consent.py`,
`providers/groq/provider.py`, and the helpers they lean on (`text/matching.py`,
`domain/analysis.py`, `domain/calendar_planning.py`, the request models of
`domain/drafting.py`, the cleaning functions of `text/prepare.py`, deadline resolution, and
the two consent repositories).

| Checked | Result |
| --- | --- |
| Analysis sends the seven fields, drafting only ticked parts, planning only what the preview lists | Confirmed by reading; each request is built from a model with exactly those fields. Drafting sends the sender's name through `display_name`, which gives none when the name is, or holds, an address |
| Consent is recorded before the call; a decline or cancel sends nothing | Confirmed for drafting and planning. Analysis consent is in `brief.py`: to C5 |
| From B1: summary, action and suggestion text stripped before storage | Covered already by `test_validate_candidate_cleans_text_the_model_wrote_itself`. Evidence quotes were not cleaned: C1-1, fixed |
| From B1: mail content cannot authorize an action | The request has no tools and a reply with tool calls is refused (`test_groq_provider.py`). New: `test_an_answer_that_obeys_the_email_still_writes_only_the_analysis_cache` compares every row of every table around a run, with a task and a consent in place |
| From B1: provider JSON stays in the adapters | New scan `test_core_packages_import_no_adapter`: only local setup imports an adapter module, its manifest and download guard |
| Errors and logs carry codes, counts and request IDs only | Confirmed by reading; Groq's text never reaches an exception |
| The model's calendar changes are checked one by one; notes, Busy/Free and Google events untouched | Confirmed by reading `validate_plan` and `_edit` |

Not read: `brief.py` (where exclusions and analysis consent are enforced), `infra/tasks.py`,
`infra/http_retry.py`, the local provider, and the UI gates.

## Slice C2: owner-record writes

Read whole: `services/actions.py`, `storage/actions.py`, `services/recurrence.py`,
`services/writes.py`. Grepped, with short ranges read: the session factory, the foreign keys
into `actions` in `storage/tables.py`, `remove_task_references`, the tag, person and waiting
helpers and `ActionEdit` in `domain/actions.py`, and the work-block service's entry points.

| Checked | Result |
| --- | --- |
| Every change needs the revision the caller saw and bumps it once | Confirmed by reading: each mutating method goes through `_live` or its own check, then one `touch`. The exceptions are the documented ones: restore, a repeated `accept` or `accept_into`, and a tick that changes nothing. `actions` and `action_series` are also versioned rows, so a stale flush fails. Its message blames the repeat: C2-3 |
| ORM objects only | Confirmed: the four files hold no `update()`, `delete()` or `insert()` statement; decisions, steps, tags and sources are deleted row by row |
| A failed write rolls back completely | Confirmed: every write goes through `commit_once`, which rolls back on any exception, cancellation included |
| Retry receipts | Confirmed: the receipt is added in the creating transaction, holds a hash and IDs only, and a replay returns the current record or refuses a removed one. A concurrent duplicate is recognised only by the receipt key's own constraint |
| Repeats: one next occurrence, from the template, in the closing transaction | Confirmed by reading `follow`: template fields only, one per series and date (deleted ones count), none while a live next one exists |
| Repeats: withdrawn only while untouched | A next occurrence with a work block counted as untouched: C2-1, fixed. Proposal undo withdraws through the same function |
| Deadlines, waiting, checklists | Confirmed by reading `_apply_deadline`, `_set_ownership`, `set_step_done` and `add_step`; nothing but an `ActionEdit` sets the person, and accepting a suggestion sets no priority, tags or person |
| Tags change only through `set_tags` and `respell_tag` | Confirmed (grep). Accepting again a suggestion whose task was deleted skipped the respelling that `restore()` does: C2-2, fixed |
| `history()` is uncapped and includes soft-deleted tasks | Confirmed: `history_rows` has no limit and no deleted filter, and reads the revision as a scalar |

Not read: `services/proposals.py` (it calls `follow` and `withdraw`; to C5), `domain/recurrence.py`
(the date arithmetic), `domain/task_history.py`, `services/deadlines.py`, the rest of
`domain/actions.py` and `storage/tables.py`, the work-block, day-plan, draft and project
services, and the desktop's callers. Test files were read only around the tests added.

## Findings

One line each: `ID | slice | severity | what is wrong | state`. Severity is high (data
loss, or private data exposed), medium (wrong behaviour) or low (cosmetic, or docs).

- R-1 | R | low | `p4-insights.md` cites "eight owner scenarios in the implementation guide", which is not in the repository; the Insights steps come from its behaviour text | open, owner question 1
- R-2 | R | low | `today.md` and `p2-focus.md` put Your day above the brief; `p2-workspace.md` says the full brief lives in Mail | open, owner question 3
- R-3 | R | low | P3 calendar acceptance sections name migrations 0032 and 0033 by branch; the add-ons add 0034 to 0036; `p3-scheduling-addons.md` has no numbered steps | open, owner questions 4 and 5
- R-4 | R | low | P1 estimates and the P2 capture, focus and navigation docs have no numbered acceptance steps; P1 docs say "Actions" for Tasks; steps may predate flat buttons (#108) | open, owner questions 2, 6, 8 and 9
- C1-1 | C1 | low | Evidence, follow-up and suggestion quotes are matched with spacing ignored, so a quote could be stored with carriage returns and separator characters the model wrote between words | fixed in `ada4425`; quotes cached before that are cleaned when read
- C1-2 | C1 | low | A Groq reply that could not be decoded (`httpx.DecodingError`) escaped as an unexpected exception. It is now an unreadable reply, raised at once: Groq has answered, so a retry would only send the mail again | fixed in `488edc6` and its follow-up
- C1-3 | C1 | low | The three Groq prompts tell the model to ignore text claiming to come from "MailBrief"; the product is now Forewit. Changing a prompt means a new prompt version, which empties the analysis cache | open, owner decision
- C2-1 | C2 | high | Reopening a repeating task, or undoing its completion or skip, removed the next occurrence outright while it had no activity, draft or focus place. One the owner had scheduled a work block for still counted, so the block (unscheduled ones too) was deleted with it | fixed in `8d43698`
- C2-2 | C2 | medium | Accepting again a suggestion whose task was deleted restored the task without the tag respelling `restore()` does, so one tag could read two ways on live tasks. It now restores through the same function, so its history records an undo of the deletion as `restore()`'s does | fixed in `6a50a50`
- C2-3 | C2 | low | `ActionService._write` reports every stale flush as "The repeat changed since it was loaded", though `actions` rows are versioned too: two overlapping writes to one task would blame its repeat. Not reproduced; the write is refused either way | open
- C2-4 | C2 | low | `undo_accept_into` checks the revision and the decision but not that the latest change was the addition. Called with the current revision of a task first accepted from that suggestion, it would make the suggestion pending and record an undo of the creation. The desktop only passes what `accept_into` returned. Read, not reproduced | open
