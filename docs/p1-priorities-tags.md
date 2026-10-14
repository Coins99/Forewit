# P1: priorities, tags and filters

This P1 slice is B03 in the [feature inventory](future-feature-inventory.md): find and
order work without needing a deadline. It builds on manual tasks
([p1-manual-tasks.md](p1-manual-tasks.md)) and projects and life areas
([p1-projects-life-areas.md](p1-projects-life-areas.md)). Recurring tasks (B05) are the
remaining P1 slice. Owner desktop acceptance of this slice is still pending.

## Priority

The task editor has **Priority**: Not set, High, Medium or Low. The same editor serves
manual and email-derived tasks. A priority is the owner's own. Accepting a suggestion
leaves it unset, and AI runs, follow-up proposals and adding an email never change it.
Saving it is an ordinary edit: one revision with its activity record.

Open and Waiting tasks are listed by target date, then due time, then **priority** (High
first, unset last), then creation, with undated tasks last. Dates still come first. So a
priority orders tasks that have the same dates, including all tasks with no dates. Today's
**Needs attention** uses the same order within each reason. The list row and the detail
pane say "High priority", "Medium priority" or "Low priority".

## Tags

The editor's **Tags** field, on the same row as Priority so the editor still fits a small
screen, takes tags separated by commas, such as `home, errands`. While you type, it completes
the tag after the last comma from the tags already in use.

- A tag is cleaned before it's saved. Repeated spaces become one, control and format
  characters are dropped, and a leading `#` is removed. Blank entries are ignored.
- Tags are matched ignoring case and character width, so `Home`, `HOME` and the
  full-width `Ｈｏｍｅ` are one tag (`tag_key`, Unicode NFKC case folding). A task keeps
  each tag once.
- A tag keeps the spelling other live tasks already use. Typing `home` on a new task, when
  another task has `Home`, saves `Home`. A tag can be respelled only while no other live
  task has it. Renaming a tag across tasks comes later.
- A tag is at most 40 characters and can't contain a comma. A task has at most 10 tags. A
  refused tag keeps the editor open with every field as typed. The message never repeats
  the tag.

The row's meta line ends with "Tags: …", and the detail pane shows the priority and tags
on one plain-text line under the project.

## Filters

The Actions filter lists **Tag: …** after the life areas and projects. It offers one item
per tag on a live task, in the earliest spelling. Choosing one shows the tasks with that tag
in the Open, Waiting and Completed views, filtered in SQLite before the completed-list cap
like the project filters. A tag that no live task has any more drops out of the filter,
and the filter goes back to All tasks.

## Storage, durability and history

Additive Alembic revision `20261014_0020` follows task estimates `20261014_0019`:

- `actions.priority`: NULL for every existing task, with a CHECK for `high`, `medium` or
  `low`.
- `action_tags`: one row per task and tag, with the spelling (`name`) and the matching key
  (`name_key`). There is a unique key on the action and tag key, an index on the tag key, a
  CHECK of 1 to 40 characters, and a cascade with the action.

A tag has no record of its own: tasks share a tag when their keys match, so there is no tag
lifecycle to keep in step. Permanent deletion of a task removes its tags. A soft-deleted
task keeps them, ready for Undo. Backups and recovery carry the column and the table. The
writing export includes `action_tags` as an additive extension of format version 1, and
each action's `priority`. Activity records the edit, as for any other field. It doesn't copy
priority or tags into history, so past completions aren't regrouped by them.

## Owner acceptance

With Gmail disconnected:

1. Create three undated tasks: one High, one Low and one with no priority. Confirm Open
   lists them High, Low, then the unset one.
2. Give one of them a target date. Confirm it moves above the undated tasks whatever its
   priority.
3. Tag two tasks `home` and `Home`. Confirm both read `home` (the first spelling) and the
   filter offers one **Tag: home**.
4. Choose that filter. Confirm both tasks show, and Completed shows one after completing it.
5. Type a 41-character tag. Confirm the editor stays open with the reason and your input.
6. Remove the tag from both tasks. Confirm the filter returns to All tasks.
7. Quit and restart. Then exercise backup, restore and writing export in a test profile and
   confirm priority and tags survive and `action_tags` is in the export.
8. Repeat the priority and tag edits on an email-derived task.

Validation on Windows, 2026-10-14: the full suite passed 4,069 tests (four skips), with
96.39% combined coverage, and the 90% per-service gate passed. Ruff formatting and lint and
strict mypy for Windows and macOS passed. The editor and the Actions page were reviewed in
light and dark at 100% and 200% scale. The editor's minimum height grows by one row, from 574
to 608 pixels. macOS execution and owner desktop acceptance remain pending.
