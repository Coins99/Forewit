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
   the coverage table for every file and fails the 80% gate; until the script exists, add
   `-q --no-cov`.
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
- After slices R and B1, write the measured cost of a slice here and project the rest. If
  the sweep won't fit, drop E, then C5, then C4.

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
| Optional, own pull request: move AGENTS.md's UI-only paragraphs into the `/mailbrief-ui` skill | Owner's call |

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
  because this `.venv` has only the `mailbrief*` console scripts. A reinstall of the
  project would fix them; CI is unaffected.
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
| R | The owner's run-sheet | One page-by-page checklist, `docs/acceptance-runsheet.md`, from the "Owner acceptance" sections of `p1-*.md`, `p2-*.md`, `p3-calendar.md`, `p3-scheduling-addons.md` and `p4-insights.md`. Each step has an ID and a link back; the live Gmail and Groq checklists are linked, not rewritten. The owner walks it in the real app and sends back failed IDs with one line each | About 15 doc sections | Sonnet | Not started |
| B1 | Invariants: secrets, bodies, what goes to AI | For each AGENTS.md invariant, grep for the test that enforces it and confirm in 30 lines or fewer. A gap gets a test; rules a scan can check go in `tests/unit/test_source_rules.py` | Test names, short ranges | Sonnet | Not started |
| C1 | Deep read: what leaves the machine | `services/analysis.py`, `drafting.py`, `calendar_planning.py`, `consent.py`, `providers/groq/provider.py` | About 2,800 lines | Fable | Not started |
| C2 | Deep read: owner-record writes | `services/actions.py`, `storage/actions.py`, `services/recurrence.py`, `services/writes.py` | About 2,600 | Fable | Not started |
| C3 | Durability | `storage/backup.py`, `recovery.py`, `migrate.py`, `migration_forks.py`, `services/data.py`, `diagnostics/backup.py`, plus a real offline backup, verify and restore round trip and an upgrade from an early revision, on a synthetic database | About 1,050 | Fable | Not started |
| B2 | Invariants: owner records | As B1: revisions, tags, repeats, deadlines, waiting, checklists, drafts and notes, preferences, receipts, work blocks, Busy/Free, workload, scheduling and Insights | Test names | Sonnet | Not started |
| C4 | Deep read: newest code | Scheduling and workload from PRs #107 and #109: `services/scheduling.py`, `domain/scheduling*.py`, `domain/workload.py`, `domain/planning_intervals.py`, `services/availability.py` | About 1,900 | Fable | Not started |
| B3 | Invariants: mail and automation | As B1: ranking, brief dates, thread tracking, proposals, outside replies, automatic runs, read-only Gmail, Inbox browsing, Google Calendar | Test names | Sonnet | Not started |
| C5 | Deep read: the daily run | `services/brief.py`, `proposals.py`, `threads.py`, `sync.py` | About 1,800 | Fable | Not started |
| D | UI, without reading it | The `MAILBRIEF_UI_SHOTS` gallery at 1x in light and dark. For each view added since October (`insights_*`, `scheduling_view`, `date_picker`, the Day view), list its tests by name and add the missing empty, one, many and long-text cases. Fix what the run-sheet reports | Test names, 10 images at most | Sonnet with `/mailbrief-ui` | Not started |
| E | Performance, optional | Re-run the large-profile probe in `p2-workspace.md` and `scripts/benchmark_insights.py`; look only at regressions against the recorded numbers | None | Sonnet | Not started |

Anything else (the Gmail and Calendar adapters, `domain/`, the repositories, `text/`,
`infra/`) gets a deep read only if `hotspots` or a finding points at it.

Not in the sweep: a deep read of P4 Insights, which has three recorded reviews and PR #111;
splitting `ui/main_window.py`, the largest standing cost of any UI change, which is a
refactor to decide separately; the dormant Microsoft code; live Gmail or Groq checks;
native macOS checks.

## Findings

One line each: `ID | slice | severity | what is wrong | state`. Severity is high (data
loss, or private data exposed), medium (wrong behaviour) or low (cosmetic, or docs).

None yet.
