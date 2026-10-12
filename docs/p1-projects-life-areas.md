# P1: projects and life areas

This is the organization slice after manual tasks and activity history. It is
implemented on `feat/projects-life-areas`, based on the task/history work in PR #51.
Unified Today follows this slice; calendar planning and dashboards can use the same
records later. Owner desktop acceptance remains pending.

On **Actions**, choose **Projects & areas**. The dialog has separate Projects and Life
areas lists. Choose New, enter a name and notes, then Save. A project can belong to
one optional life area. Select an existing record to rename it, edit notes or change
its area. Failed saves keep the entered fields. Saves and lifecycle changes check the
revision the owner loaded and commit once; stale edits never overwrite a newer record.

The task editor offers an optional **Project** for both manual and email-derived
tasks. Unassigned tasks remain valid. The Actions filter offers All tasks, Unassigned,
each project, and each life area. A life-area filter finds the tasks of its projects.
Filters apply in SQLite before the completed-list cap; counts query the full group.
Sidebar counts continue to describe all open/waiting tasks.

## Lifecycle and durability

- Archive project keeps its tasks and links. The archived project remains available
  in grouped views, and already linked tasks remain editable/completable/reopenable.
  New assignments to it are refused by the service and omitted from new-task choices.
  Reopen project makes it available for new assignments again.
- Remove area soft-deletes only the area and detaches its projects in the same
  transaction. Projects, their notes and tasks remain. Each detached project gets a
  new revision so an old project editor cannot accidentally put the removed area back.
- Renaming or moving a project does not change task revisions or incoming email sources.
- New task activity stores stable project and area IDs at the time of the change.
  Later reassignment or area removal does not rewrite earlier completion history.
  Old events remain ungrouped; migration does not invent prior organization.

Additive Alembic revision `20261014_0016` follows task history `20261013_0015` and adds
owner-only `projects` and `life_areas`,
the optional task/project foreign key and activity grouping snapshots. It preserves
existing actions, steps, sources, writing and activity. Projects and areas have no
mailbox/account foreign keys. The separate domain, repository and service modules
are `domain/projects.py`, `storage/projects.py` and `services/projects.py`.

Backups and recovery include both tables and the new links/snapshots. Writing exports
include `projects` and `life_areas` as an additive extension of format version 1,
including archived projects and removed areas. Deleted task/draft cleanup does not
remove project or area records. No Gmail or AI setup is needed for this workflow.

## Owner acceptance

1. With Gmail disconnected, create a life area named Learning and a project under it.
2. Create a manual task assigned to the project. Assign an existing email task to it too.
3. Filter Actions by the project and area; confirm both tasks use the usual editor,
   completion and reopening controls. Rename the project and check the labels update.
4. Complete a task, then move it to another project. Earlier completion history must
   retain the original project/area IDs in the portable export.
5. Archive the first project; its tasks remain visible and editable. New tasks cannot
   choose it. Reopen the project and confirm it can be selected again.
6. Remove the area; confirm its projects and tasks remain, with no area assigned.
7. Quit and restart, then exercise backup, restore and writing export in a test profile.
   Confirm organization, links and historical grouping survive.

Validation on Windows, 2026-10-11: 3,346 tests passed, three platform/gallery skips,
96.80% overall coverage, and the 90% per-service gate passed. Ruff formatting/lint
and strict mypy for Windows and macOS passed. Dark/light layouts, the manager and task
project selector were reviewed at 100% and 200% scale. macOS execution and owner
acceptance remain pending.

Review hardening: project and life-area writes use ORM optimistic versioning, so a
concurrent save or area removal rolls back instead of overwriting a newer revision.
The manager requires saving or explicitly discarding edits before switching records,
archiving, removing an area or closing. Escape and the window close button respect
pending saves. Opening the manager reloads its records; committed save results update
its cache even if the subsequent page refresh fails. Task reads fetch only their
referenced projects and the latest 20 activity entries; exports and recovery retain
all stored history.
