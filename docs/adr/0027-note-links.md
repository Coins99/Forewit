# ADR 0027: A note's project and task links

Date: 2026-11-04

Status: Accepted for P2-04a implementation; owner acceptance remains open.

Amends ADR 0012, which lists what a draft may store.

## Context

Notes are drafts with `kind='note'` (ADR 0012). Before P2-04 a note could link to one
task, but only when it was created from that task, and it could not link to a project.
P2-04 gives notes their own page and lets the owner organize them later, the way a
captured task is organized later (ADR 0025). The roadmap (E01, E03) asks for notes
linked to tasks and projects, and for project context built from inverse queries
rather than copied notes. General note relationships stay deferred.

## Decision

- **One project and one task per note.** Migration 0029 adds a nullable
  `drafts.project_public_id`, a foreign key to `projects.public_id` with
  `ON DELETE SET NULL`, `ix_drafts_project_public_id`, and
  `ck_drafts_project_only_for_notes` (`kind = 'note' OR project_public_id IS NULL`), so
  the database also refuses a project on any other kind. The task link remains the
  existing `drafts.action_id` (SET NULL) and its `action_title` snapshot. There is no
  link table and no many-to-many relationship.
- **There's no project-name snapshot.** Projects are archived, never purged, so the
  name is read with the note and a rename shows on every note. The SET NULL only
  guards against a project row going away.
- **Only notes take links here.** `DraftService.set_project` and `set_task` refuse
  other kinds. Replies, emails and messages keep their creation-only task link and
  take no project, although the column is on every row, so a later increment could
  give drafts a project without a migration.
- **The owner sets the links, one change at a time.**
  - `set_project` and `set_task` need the live note's displayed revision.
  - A change makes one revision and bumps `updated_at_utc`.
  - It saves no version, because versions hold text only.
  - It leaves source snapshots and the text alone.
  - A call that changes nothing makes no revision.
  - The calls are separate, so changing the project never clears a link to a
    soft-deleted task, which could be restored.
  - The desktop names the note when the owner makes the change, so a change queued
    behind a switch to another note still goes to the note it was made for.
  - Errors use fixed messages and never contain owner text.
- **Archived projects keep their notes but take no new ones.**
  - A new project link needs a project that exists and isn't archived.
  - A note keeps an archived project it already has.
  - Once a note leaves an archived project, it can't go back.
  - `create(kind, project_public_id=…)` and `save_as_new` follow the same rule:
    `save_as_new` drops a project archived since.
  - Removing a life area never changes notes.
- **A task link is a live task's, with a title snapshot.**
  - Linking needs a live task and records its current title as `action_title`.
  - Unlinking clears both the link and the snapshot.
  - Linking never changes the task.
  - A note linked to a repeating task's next occurrence makes that occurrence the
    owner's work (`drafted()`), so undoing the completion keeps it.
- **No inference.** A note's project is never taken from its task's project, and AI
  runs, proposals, capture and adding an email never set either link.
- **Backlinks are queries.** A project's notes, and the notes of its tasks, are found
  by querying these links (P2-04b). Nothing is copied into the project or the task.
- **AI drafting sends nothing new.** A note's project, the project's notes and other
  linked notes are never part of what drafting may send. ADR 0013's parts are
  unchanged.

## Consequences

- Backup and recovery follow the schema in `Base.metadata`. Restore keeps the links,
  and older backups upgrade with none.
- The portable writing export includes `drafts.project_public_id` as an additive
  column of format version 1, beside the existing `action_id` and `action_title`.
- Purging a deleted note removes its links with the row. Purging a task sets
  `action_id` to NULL and keeps the title snapshot, as before. The cleanup preview's
  counts are unchanged.
- The Notes page lists notes on the existing writing page, and the editor shows a
  note's project and task (`docs/p2-notes.md`).
- Downgrading below 0029 drops the project links and keeps every note.
