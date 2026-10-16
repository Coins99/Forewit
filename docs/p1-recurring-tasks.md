# P1: repeating tasks

This P1 slice is B05 in the [feature inventory](future-feature-inventory.md): tasks that repeat
daily, weekly or monthly, with skip and reschedule. It builds on manual tasks
([p1-manual-tasks.md](p1-manual-tasks.md)), projects and life areas
([p1-projects-life-areas.md](p1-projects-life-areas.md)) and priorities and tags
([p1-priorities-tags.md](p1-priorities-tags.md)). Owner desktop acceptance is still pending.

## Repeats

The task editor has **Repeats**, under Target:

- **Doesn't repeat**, the default.
- **Every day**.
- **Every week on…**, with a check box for each weekday. Choosing it checks the task's own
  weekday. At least one weekday is needed.
- **Every month on day…**, with the day (1 to 31). A month without that day is skipped, so
  "the 31st" comes in January, March, May, July, August, October and December. February 29
  comes only in leap years.
- **On the last day of each month**: its own rule, never a stand-in for the 31st.

**Until** gives an optional end date: the last date the task can repeat on. It can't be
before the task's date.

A repeating task is a *series*: the rule, and a template that later occurrences start from.
Each occurrence is an ordinary task with its own ID, revision, steps, tags, activity and Undo,
and it shows in Open, Waiting, Today and the Calendar like any other task. Its *occurrence date*
is which occurrence it is. Moving its target date reschedules it without changing which
occurrence it is, or when the next one falls.

## One occurrence at a time

A series has one open occurrence at a time in the ordinary course. Completing it, or choosing
**Skip** in the detail pane, makes the next one in the same transaction, dated by the rule's
first date after **both** the occurrence's own date and today, in the owner's time zone:

- On time: a daily task completed on its day comes back tomorrow.
- Late: a daily task left for a week comes back once, tomorrow, not seven times. Dates missed in
  between are never created.
- Early: a Monday task completed on Saturday comes back on the rule's next date after that
  Monday.

The status line says when it comes back ("Completed: Water plants. Next Fri Oct 16."). Past the
end date, or after the owner stops the repeats, no next occurrence is made.

A skipped occurrence is closed without being done: status `skipped`, with no completion time. It
is in neither Open nor Completed, but keeps its activity. The next occurrence's detail says what
it follows ("Follows Wed Oct 14 · Skipped").

The next occurrence copies the template: title, notes, steps (none of them done), whose it is,
priority, tags (spelled as live tasks spell them), effort, estimate and project. It has no email
source, deadline or AI evidence. An archived project takes no new task, so it starts
unassigned.

## Undo and reopen

- **Undo complete** or **Undo skip** reopens the occurrence and removes the next one, while
  that one is untouched: open, live and still at revision 1.
- **Reopen** does the same. An untouched next occurrence has no owner input, so it is removed,
  not soft-deleted. Completing again makes it again.
- A next occurrence the owner has changed is theirs and stays. Then closing the earlier one
  again makes no second one.
- If the next date already has an occurrence, even a deleted one, none is made: the owner can
  restore that one. So repeating any of this makes one next occurrence at most.
- A follow-up proposal that completes an occurrence (a cancellation or delivery) makes the next
  one as completing it would, and undoing the proposal withdraws it the same way.

## Changing the series

Editing an occurrence changes that occurrence. **Change later repeats too**, ticked by default,
also copies the edit into the template, except the target date. Untick it to change only this
one. A new rule changes the series, but doesn't move the open occurrence.

Choosing **Doesn't repeat** on a repeating task stops the series. The task stays and keeps its
history, and choosing a rule again resumes it. A closed occurrence's repeat can't change:
reopen it first. Series changes carry their own revision. An editor that saw an older one is
refused, like a stale task.

## Storage, durability and history

Additive Alembic revision `20261015_0021` follows priorities and tags `20261014_0020`:

- `action_series`: the rule (`frequency`, `weekday_mask`, `month_day`, `end_date`), the
  template (title, notes, ownership, priority, effort, estimate, project, `steps_json`,
  `tags_json`), `ended_at_utc` and a revision. CHECKs keep every rule valid: weekdays exactly
  for a weekly rule (mask 1 to 127), a day of the month (1 to 31) exactly for a monthly-by-day
  one.
- `actions.series_id`, `occurrence_date` and `follows_action_id` (the occurrence whose close
  made it), NULL for every existing task. There is a unique key on the series and occurrence
  date. `actions.status` may now be `skipped`.

A series has no account and is never deleted with an occurrence: permanently deleting an
occurrence leaves its series and sets `follows_action_id` to NULL on the one after it. Backups
and recovery carry the table and columns. The writing export includes `action_series`, as an
additive extension of format version 1. Activity records `skipped` with the status before it,
and undo refers to it like a completion. Downgrading turns a skipped occurrence into a completed
one at its last change, since older versions have no skip, and drops the series.

## Owner acceptance

With Gmail disconnected:

1. Create a task with **Every day** and no target date. Confirm it's dated today.
2. Complete it. Confirm the status line names tomorrow, and a new open task is dated
   tomorrow, with the same title, steps unchecked and tags.
3. Choose **Undo complete**. Confirm the first task is open again and tomorrow's has gone.
4. Skip it. Confirm it's in neither Open nor Completed, and tomorrow's says "Follows … ·
   Skipped". Undo the skip.
5. Edit it: rename it, untick **Change later repeats too**, and move its target date two days
   on. Complete it. Confirm the next task has the old title and tomorrow's date.
6. Make a weekly task on Tuesday and Friday, and a monthly task on the 31st. Confirm each
   detail line ("Repeats every Tue and Fri", "Repeats on the 31st of each month (not in
   shorter months)").
7. Set **Until** to tomorrow on a daily task, and complete it twice. Confirm no third
   occurrence.
8. Choose **Doesn't repeat** on a repeating task. Confirm "Repeats stopped", no Skip, and no
   next task when it's completed.
9. Quit and restart. Then make a backup and a writing export in Data, restore in a test
   profile, and confirm the series, the skipped occurrence and `action_series` in the export.

Validation on macOS, 2026-10-15: the full suite with the Qt tests passed 4,230 tests (six
skips), with 96.21% combined coverage, and the 90% per-service gate passed. The Actions page,
editor, row and detail tests also passed on the Cocoa platform. Ruff formatting and lint, and
strict mypy for Windows and macOS, passed. The Actions page and the editor were reviewed in
light and dark at 100%. Windows execution and owner desktop acceptance remain pending.
