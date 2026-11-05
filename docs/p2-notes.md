# P2-04a: Notes and note links

Implemented on `feat/p2-notes`, from `main` at `7f7f9cb` (after P2-01 to P2-03). This
is the first of two P2-04 pull requests. The owner approved the plan on 2026-11-04:

1. a Notes row beside Drafts, sharing the writing page;
2. one project and one task per note, both editable;
3. a Projects page with the project's context (P2-04b);
4. two stacked pull requests.

P2-04b adds the Projects page and project search on top of this. Native macOS and owner
acceptance remain open, and full P2 is not complete.

## Workflow

- **Notes** is a sidebar row after Calendar (Ctrl+8, or Cmd+8 on macOS). Ctrl+1 to
  Ctrl+7 are unchanged. It shows the writing page in a Notes view, as Waiting shows the
  Actions page, with the same inline editor, autosave, versions, copy, export, delete
  and Undo.
- **Notes view:**
  - It lists only notes, newest first, with a search field and a filter: **All notes**,
    **No project**, or one project (archived ones are labelled).
  - Each row shows the note's project and task.
  - **New note** starts a note in the filtered project, when that project takes new
    notes.
  - Choosing an archived project in the filter, then New note, returns the filter to
    All notes, so the new note shows beside its editor.
- **Drafts** now lists replies, emails and messages, and its New menu offers Email and
  Message. Its sidebar count excludes notes, and the Notes row has no count.
- **A note's links** sit under its title in the editor, inline or in the dialog:
  - **Project:** No project, or a project that isn't archived. An archived project shows
    only while it is the note's own.
  - **Task:** the linked task's title, with **Link to task…** (**Change task…** once
    linked) and **Unlink**.
  - Link to task… opens a chooser that searches live tasks by title with the focus
    chooser's word rules: open tasks first, at most 50. Return or Link chooses one.
    Completed and waiting tasks are labelled.
  - A link change saves after any typing waiting to be saved, and the text stays as
    typed.
  - If a project filter would hide a note after its project changes, the filter
    returns to All notes, so the note being edited stays in the list.
- **Routes:** Capture's Open, Ctrl+K note results, the palette's **New note** and
  `PageRoute(page="notes")` open the Notes view. A draft opened inline always shows in
  its own view: a note in Notes, any other kind in Drafts.

No account, network or AI permission is needed.

## Persistence and failure behavior

- **Data.** Migration `20261104_0029` adds the nullable `drafts.project_public_id`,
  with an FK to `projects.public_id` (SET NULL) and an index. The task link is the
  existing `drafts.action_id` and its title snapshot. See
  [ADR 0027](adr/0027-note-links.md).
- **Changing a link.**
  - `DraftService.set_project` and `set_task` each need the displayed revision.
  - A change is one revision with no version, and sources stay.
  - A no-op makes no revision.
  - A stale revision shows the editor's usual conflict, which keeps the text and offers
    Save as new draft.
  - A refused link (an archived project, a deleted task) keeps the note's links as they
    were and says why, in a fixed message.
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
  newer words are typed.

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

Windows, 2026-11-04, on commit `b071d6e`:

- **Focused checks.**
  - 14 note-link service tests (`tests/unit/services/test_note_links.py`) and 2 migration
    tests (`tests/unit/storage/test_note_project_migration.py`).
  - Regression tests for a repeating task's next occurrence and for drafting never sending
    a note's project.
  - 14 writing-page panel tests and 11 Notes window tests (`tests/ui/test_notes.py`: rows,
    Ctrl+8, routes by kind, project changes after waiting typing, refused and stale links,
    the task chooser with the keyboard, New note in a project, and the filter keeping the
    edited note shown).
- **Full suite:** 5,092 passed and 9 expected platform skips. 95.69% combined coverage, and
  `scripts/check_coverage.py` confirms every service clears the 90% gate. Four
  `tests/unit/test_branding.py` command tests failed only because this machine's `.venv`
  predates the Forewit command names (`forewit.exe` not found). CI's `uv sync` installs
  them.
- **Lint and types:** Ruff lint and format passed. Strict mypy passed native, win32 and
  darwin (486 source and test files each).
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
`e1f08450b06656f3f6970e691d5b7ebf059ba8b5dc902dff246929d7d111ea17`. This is an
engineering build from the branch, not a release.

The checks used disposable synthetic records only: no owner profile, real mailbox or paid
AI. Logs are in the ignored `out/p2-notes-*`, and renders in `scratch/p2-notes-shots/`.
Native macOS and the owner's hands-on acceptance remain open.

## Owner acceptance

1. With Gmail disconnected, create projects Home and Work, and archive Work.
2. Choose **Notes** (or Ctrl+8). Confirm that Drafts no longer lists notes, and that
   its count excludes them.
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

## Next

P2-04b adds the Projects page (Ctrl+9): projects by area, each with its notes, the
notes of its tasks, its open, waiting and recent completed tasks, and the emails its
tasks came from. It also makes projects searchable. Dedicated
Tasks/Waiting/Completed and Mail pages, P2-06 capture review, and P2-07/P2-08
follow in their own increments.
