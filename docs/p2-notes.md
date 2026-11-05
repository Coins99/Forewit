# P2-04a: Notes and note links

Merged in PR #101. PR #102 then hosted it in the new workspace and delivered the rest
of P2 (see [After this checkpoint](#after-this-checkpoint)). The Workflow below
describes the Notes page as it is now. Native macOS and owner acceptance remain open.

Implemented on `feat/p2-notes`, from `main` at `7f7f9cb` (after P2-01 to P2-03). The
owner approved the plan on 2026-11-04:

1. a Notes row beside Drafts, sharing the writing page;
2. one project and one task per note, both editable;
3. a Projects page with the project's context (P2-04b);
4. two stacked pull requests.

## Workflow

- **Notes** is a sidebar page after Calendar (Ctrl+8, or Cmd+8 on macOS). It shares the
  writing panel and inline editor with Mail's drafts: autosave, versions, copy, export,
  delete and Undo.
- **Notes view:**
  - It lists only notes, newest first, with a search field and a filter: **All notes**,
    **No project**, or one project (archived ones are labelled).
  - Each row shows the note's project and task.
  - **New note** starts a note in the filtered project, when that project takes new
    notes.
  - Choosing an archived project in the filter, then New note, returns the filter to
    All notes, so the new note shows beside its editor.
  - A task's writing menu opens Notes filtered to that task's linked notes; **All notes**
    clears that filter.
- **Mail drafts** (Mail's view chooser, Ctrl+5) lists replies, emails and messages. Its
  New menu offers Email and Message, and its count excludes notes.
- **A note's links** sit under its title in the editor, inline or in the dialog:
  - **Project:** No project, or a project that isn't archived. An archived project shows
    only while it is the note's own. The choice saves as soon as it is made, so it changes
    only from the list's popup: the wheel scrolls past it, and the arrow keys or a letter
    open the popup rather than change the project.
  - **Task:** the linked task's title, with **Link to task…** (**Change task…** once
    linked) and **Unlink**.
  - Link to task… opens a chooser that searches live tasks by title with the focus
    chooser's word rules: open tasks first, at most 50. Return or Link chooses one.
    Completed and waiting tasks are labelled.
  - A link change saves after any typing waiting to be saved, and the text stays as
    typed. Each change names its note when it is made, so a switch to another note queued
    ahead of it still saves it to the note it was made for. Changes made in quick
    succession save in order, and the fields stay enabled (and keep the keyboard's focus)
    while they wait.
  - If a project filter would hide a note after its project changes, the filter
    returns to All notes, so the note being edited stays in the list.
- **Routes:** Capture's Open, Ctrl+K note results, the palette's **New note** and
  `PageRoute(page="notes")` open the Notes view. A draft opened inline always shows in
  its own view: a note in Notes, any other kind in Mail drafts.

No account, network or AI permission is needed.

## Persistence and failure behavior

- **Data.** Migration `20261104_0029` adds the nullable `drafts.project_public_id`,
  with an FK to `projects.public_id` (SET NULL), an index, and
  `ck_drafts_project_only_for_notes`, so only a note can have a project. The task link
  is the existing `drafts.action_id` and its title snapshot. See
  [ADR 0027](adr/0027-note-links.md).
- **Changing a link.**
  - `DraftService.set_project` and `set_task` each need the displayed revision.
  - A change is one revision with no version, and sources stay.
  - A no-op makes no revision.
  - A stale revision shows the editor's usual conflict, which keeps the text and offers
    Save as new draft.
  - A refused link (an archived project, a deleted task) keeps the note's links as they
    were and says why, in a fixed message. The projects are reloaded, so a project archived
    elsewhere isn't offered again.
- **Archived projects** keep their notes but take no new ones. `save_as_new` drops a
  project archived since. Removing an area never touches notes.
- **Tasks.** Linking records the task's current title. Unlinking clears the link and
  the title. Linking never changes the task. A note linked to a repeating task's next
  occurrence keeps that occurrence when the completion is undone.
- **Backup, recovery and export.**
  - Backup and recovery follow the schema, and restore keeps links.
  - Writing exports include `drafts.project_public_id` (format version 1, additive).
  - Purging a deleted note removes its links. Purging a task keeps the note and the
    title snapshot.
- **What never changes the links.** Cache cleanup, disconnecting Gmail, AI runs,
  proposals and capture never change them. AI drafting never sends a note's project.
- **Threading.** All reads and writes run off the Qt thread, and link changes go through
  the window's one queue of draft writes. Late task-search results are dropped once
  newer words are typed. Link to task… reads only each task's row (`TaskChoice`), not
  its steps, sources or tags.
- **Refreshes.** A refresh that keeps the selected note never passes through no
  selection, so it never hides the inline editor or takes the keyboard's focus. Switching
  between Notes and Drafts opens the view's newest item.

Implementation:

- `domain/drafts.py`: `WritingView`, and the project fields on `Draft` and
  `DraftSummary`.
- `storage/drafts.py`: project names read with drafts, and `list_summaries` filters.
- `services/drafts.py`: `set_project`, `set_task` and `NoteLinkError`.
- `ActionService.title_matches`, built on the shared `ActionRepository.title_matches`,
  which the focus chooser also uses.
- `ui/drafts_view.py`, `ui/draft_editor.py`, and the new `ui/task_picker.py`.
- The bundled Tabler `notes` icon.

## Verification

Windows, 2026-11-05, after the review fixes below:

- **Focused checks.**
  - 14 note-link service tests (`tests/unit/services/test_note_links.py`) and 3 migration
    tests (`tests/unit/storage/test_note_project_migration.py`), including the
    only-notes check.
  - Regression tests for a repeating task's next occurrence and for drafting never sending
    a note's project.
  - 4 shared-field tests (`tests/ui/test_fields.py`), 19 writing-page panel tests and
    15 Notes window tests (`tests/ui/test_notes.py`). The window tests cover:
    - rows, Ctrl+8 and routes by kind;
    - project changes saved after waiting typing, refused and stale links;
    - the task chooser with the keyboard;
    - New note in a project, and under an archived filter;
    - a change queued behind a note switch;
    - changes queued in order;
    - refreshes that keep the editor shown.
- **Full suite:** 5,106 passed and 9 expected platform skips. 95.69% combined coverage, and
  `scripts/check_coverage.py` confirms every service clears the 90% gate. Four
  `tests/unit/test_branding.py` command tests failed only because this machine's `.venv`
  predates the Forewit command names (`forewit.exe` not found). CI's `uv sync` installs
  them.
- **Lint and types:** Ruff lint and format passed. Strict mypy passed native, win32 and
  darwin (487 source and test files each).
- **Gallery:** the Notes page and the task chooser were rendered in light and dark at 1x
  and 2x and inspected against the Studio pages. The editor shows a note as LOCAL NOTE
  with Save note.
- **Package:** a fresh Windows x64 single-file build passed `check_package.py --layout
  onefile`:
  - offscreen full and verify at 1x and 2x;
  - a native Windows launch;
  - profile try-lock;
  - the upgrade scenario.

  The package smoke's upgrade check now writes its revision-0009 note in raw SQL, since
  today's ORM has a column that revision lacks. Without that change, the package check
  would have failed.

Executable SHA-256:
`ebc1a8736513dcb19c27278cc992d19b0ac5a5e48a0344981e945f2950d42967`. This is an
engineering build from the branch, not a release.

### Review fixes

An extra-high review of PR #101 found 14 issues. All are fixed with regression tests:

- A link change names its note when it's made, so a change queued behind a switch to
  another note still reaches the note it was made for.
- The Project list is a `PopupChoice` (`ui/fields.py`). The wheel and the arrow keys no
  longer change and save it.
- Link changes queue in order, and the fields stay enabled while they wait.
- A refresh that keeps the selection no longer hides the inline editor. Unchanged
  projects rebuild nothing.
- The task title uses `wrap_label`.
- A refused project reloads the projects.
- `ck_drafts_project_only_for_notes` was added to migration 0029 before the migration
  reached any real database.
- Switching views opens the newest item.
- `project_for_new_note` no longer changes the filter.
- Both editors share `fill_project_choice`.
- The window's two link saves are typed separately.
- The labels are named.
- The chooser reads `TaskChoice` rows.
- The AGENTS.md wording was corrected.

The checks used disposable synthetic records only: no owner profile, real mailbox or paid
AI. Logs are in the ignored `out/p2-notes-*`, and renders in `scratch/p2-notes-shots/`.
Native macOS and the owner's hands-on acceptance remain open.

## Owner acceptance

1. With Gmail disconnected, create projects Home and Work, and archive Work.
2. Choose **Notes** (or Ctrl+8). Confirm that Mail's drafts (Ctrl+5) no longer list
   notes, and that their count excludes them.
3. Choose New note, type a title and text, and pick Home under Project. Filter Notes
   by Home and No project, and confirm the note moves between them. Confirm Work isn't
   offered.
4. Choose Link to task…, search for a task, and link it. Then Unlink. Confirm the task
   itself is unchanged.
5. Open the note from Ctrl+K and from Capture's Open. Both show the Notes view.
6. Rename Home, and confirm the note's row shows the new name. Archive Home, and
   confirm the note keeps it but can't move back once moved out.
7. Quit and restart, then check backup, restore and the writing export in a test
   profile: the note keeps its project and task.

## After this checkpoint

PR #102 delivered the rest of P2 on top of this one:

- P2-04b: the Projects page with project context, and project search;
- the Today/Tasks/Projects/Calendar/Notes/Mail workspace, with Mail's drafts beside
  Notes;
- P2-06 capture review;
- Upcoming, and dated plans with copy and move;
- saved-brief search.

Capacity planning against available hours isn't implemented: Upcoming shows known
commitments, not available capacity.

PR #102 was built in parallel on its own branch, and its own migration
`20261103_0029` added the same note column as this one's `20261104_0029`. Revision
`20261105_0031` merges the two forks without editing either. See `p2-workspace.md`
for that behaviour, the migration convergence and the remaining open checks.
