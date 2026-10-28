# P1: manual tasks and activity history

This is the first small P1 slice. Projects and life areas follow it; unified Today
follows those. Their stores, calendar planning and dashboard aggregates are not part
of this change. Owner desktop acceptance remains pending.

On Actions, choose **New task**, or press **Ctrl+N** (**Cmd+N** on macOS). Enter a title,
notes, checklist steps and an optional target date. The existing editor also offers
ownership (Mine / Waiting for someone) and effort. Cancel creates nothing. A failed
save keeps the entered fields available to retry.

Manual tasks use the same owner action, revision checks, lists, editing, completion,
reopening, drafts and deletion workflow as email-derived actions. They need no Gmail
connection or source snapshot. Adding an email later retains its provenance; undoing
that first addition restores the manual task to zero sources.

An open task's steps can also be ticked and added from the detail pane, each saved at once
as one revision ([checklists in place](p1-checklists.md), B04).

## Durable activity

Additive migration `20261013_0015` follows the Google calendar cache migration
`20261012_0014` and creates `action_activity`, keyed uniquely by action and revision. Each owner change and its activity record commit together, or both roll
back. Creation, edits, completion, reopening, undo, deletion/restoration, source
additions, suggestion acceptances, seen watermarks and applied proposals are recorded. Repeating an idempotent
operation records no extra event. Activity contains timestamps and transition metadata,
not a copy of task notes or incoming email content. Accepting another suggestion from
an already-linked email records suggestion acceptance; only an actual new source records
email addition, and undo uses the corresponding wording.

Reopening still clears the action's current `completed_at_utc`, while the earlier
completion event keeps its time. Undo completion/reopen creates an `undo` event referring
to the reversed revision. Undo reopen restores the original completion time rather than
creating another completion. A stale undo is refused. Future charts must treat undo
references explicitly and query uncapped history, rather than count completed UI rows.

The detail pane shows the latest 20 lines of activity in the owner's time zone, where edits
in a row share one line (B04). Task reads fetch at most the latest 200 entries with an
indexed query. All entries remain stored and are included
in the writing export's `action_activity` records (an
additive extension of format version 1) and consistent database backups. Recovery validates
and restores the new table alongside actions. Acceptance undo now hides the unchanged
action and removes its decision, preserving creation/undo history until explicit deleted
writing cleanup; accepting again creates a new action. Permanent deleted-writing cleanup
cascades to its activity, just as it does to its steps and sources.

For existing databases, migration records known creation and the currently preserved
completion as **Migrated record**. It does not invent old edits, reopenings, undos or
completion timestamps that earlier versions erased. Restoring a legacy deleted task records
restoration, without claiming to undo a migration-era completion. Only a known deletion
event at the previous revision can be referenced as an undo of deletion.

## Estimates

A task can carry the owner's own estimate (B07 in the [feature
inventory](future-feature-inventory.md)): **Estimate** in the editor, in whole minutes from 1
to 6,000 (100 hours). Type minutes (`90`), hours (`1.5`, `1.5h`), both (`1h 30`) or `1:30`;
the arrow keys step to the next quarter hour; clear it for **Not set**. Half-typed text is
refused with the reason. The editor and the detail pane, under the target, read it in the
event editor's words: "Estimate 1 hr 30 mins". It sits beside the AI's coarse effort (minutes, hours or
days) and is never derived from it: accepting a suggestion leaves the estimate unset, and
AI runs, proposals and adding an email never change it. Saving it is an ordinary edit, one
revision with its activity record. Additive migration `20261014_0019` adds
`actions.estimate_minutes` (NULL for existing tasks) with a CHECK on its range; backups,
restore and the writing export carry it.

## Acceptance

Automated synthetic tests cover manual and email-derived tasks through create/accept,
edit, complete, reopen, undo, restart, backup, restore and portable export. They also
cover failed-commit atomicity, stale undo, invalid steps, zero-source restoration,
keyboard creation, cancellation and failed-save input preservation.

Owner check, with Gmail disconnected:

1. Open Actions, create a task with notes, two steps and a target date.
2. Edit its title and check a step. Complete it, then reopen it.
3. Confirm Activity still contains the completion and reopening times.
4. Undo reopen; confirm the original completion time returns. Reopen once more.
5. Quit and restart; confirm the task, edits, steps and Activity persist.
6. Create a backup and writing export in Data. Restore the backup in a test profile;
   confirm the same task and Activity return and the export contains `action_activity`.
7. Repeat completion/reopening on an email-derived action and confirm the same controls.

Validation on Windows, 2026-10-11: 3,234 tests passed, three platform/gallery skips;
96.87% overall coverage, with the 90% service coverage gate passing. Ruff formatting
and lint checks and strict mypy for Windows and macOS passed. Dark/light screenshots
were reviewed at 100% and 200% scale. macOS execution and owner desktop acceptance
remain pending.


PR #51 integration validation: 3,328 tests passed across the full run and a recovery
subprocess rerun with the worktree source path; three skips, 96.88% overall coverage,
and the 90% per-service gate passed. Ruff and strict mypy for Windows and macOS passed,
and the dark/light screenshot gallery was reviewed at 100% and 200% scale.
