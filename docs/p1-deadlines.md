# P1: deadlines you set

This P1 slice is B06 in the [feature inventory](future-feature-inventory.md): separate
deadlines, target dates and scheduled times, so a commitment's date survives when the
planned work moves. Before it, a task's deadline came only from an email: the editor showed
it as text, and a manual task never had one. Now you set, change or clear a deadline
yourself, and an email's deadline you replace is kept so you can go back to it. The design is
[ADR 0021](adr/0021-owner-deadlines.md). It builds on manual tasks
([p1-manual-tasks.md](p1-manual-tasks.md)) and repeating tasks
([p1-recurring-tasks.md](p1-recurring-tasks.md)). Owner desktop acceptance is still pending.

## Three dates

- The **deadline** is the commitment: when the task is due, from an email or from you. It
  decides "Overdue", Your day's "due today", the Calendar's deadline markers and the order of
  tasks with the same target date.
- The **target date** is yours: the day you plan to finish. It moves only when you move it.
- The **suggested target** is worked out in Python from the deadline: one working day before
  it, or the deadline's own day when no working day before it is left.

Scheduled times, blocks of time set aside for a task, are C03's work blocks and aren't part
of this slice. Like Calendar events (ADR 0018), they will never move a deadline.

## Setting a deadline

The task editor's **Deadline** row has **Due by**, a date and a time. The date runs from
2000 to 2099. The time is **Any time** for a deadline on a day, or a time from the list in
15-minute steps, or one typed as `HH:mm` ("9:05" works too). It steps as the event editor's
times do: Up and Down a quarter hour later and earlier, Page Up and Page Down an hour, the
wheel only while the field has focus, and from a time between marks to the next mark (Up
from 16:59 is 17:00; Down from 00:00 is Any time). A date still being typed is taken when you
save. Dates and times are in your saved time zone, the system's when preferences can't be
read, in the desktop and the service alike; a system zone without an IANA name is stored as
UTC, keeping a date's day and a time's instant. A time a clock change skips reads as a clock
shows it: 02:30 on the morning clocks go forward is 03:30. A time that isn't one keeps the
editor open with the reason. The inline **New task** shows the row under **More options**.

Unticking **Due by** clears the deadline. Saving without touching the row leaves the deadline
as it was, so an email's deadline stays the email's however often you save the task, and
setting the day it already falls on changes nothing.

## The email's deadline

A task you accept from a brief has the email's deadline, and the editor shows the email's
words under the fields: "The email says “by Friday”." An exact time the email stated in
another zone also names that time: "The email says “Friday 4 PM Central” (16:00
America/Chicago)." The fields show it in your zone.

Replacing or clearing the email's deadline keeps it. The editor then says "You set this
deadline. The email said “by Friday” (Fri Oct 2)." or "You cleared the deadline. …", with
**Use the email's**, which puts the email's deadline back in the fields; saving makes it the
deadline again. Changing your own deadline again keeps the email's as it was. Only the latest
email deadline you replaced is kept.

## Target dates

Changing the deadline re-suggests a target, counted from today. In the editor, a target date
that was the suggestion moves to the new suggestion as you change the deadline, so you see it
before saving. A target date you chose stays, and a task without one keeps none: the
**Suggested** line offers it, but only you tick a target (so a new repeating task still
starts on its rule's first date). Clearing the deadline suggests nothing and moves no
target. The service itself never moves a target date when a deadline changes, and moving a
target date never moves a deadline.

## Follow-up emails

A later email's new deadline is still a proposal you apply (ADR 0016). Over a deadline you
set, it says so: "Replace your deadline with Mon Oct 5" (in the CLI, "new deadline 2026-10-05,
replacing yours"). Applying it makes the email's deadline the task's, and **Undo** brings
yours back. While your deadline stands (you set or cleared it), an email that names the
email deadline you replaced proposes nothing: you decided about that one. Once an email's
deadline is the task's again, the one you replaced earlier counts as news again.

## Repeating tasks

A repeating task's next occurrence starts without a deadline, as before. You can set one on
an occurrence; **Change later repeats too** never copies it, since a fixed date means nothing
for later repeats. A deadline that moves with each repeat ("two days after each date") is a
possible later addition.

## Where it shows

- The detail pane: "Due Fri Oct 16, 17:00 · Set by you" (" · Overdue" when it has passed).
- Rows, Your day, the Calendar's markers, ordering, overdue and Plan with AI's open actions
  read a deadline you set like any other.
- `mailbrief-gmail-diagnostic actions list`: "deadline 2026-10-02 (set by you)". Editing
  stays in the desktop.
- AI drafting's linked-action part sends a deadline you set as its date, or its date and
  time in your zone ("2026-10-02 17:00"), where it sends an email's deadline as its words.

## Storage, durability and history

Additive Alembic revision `20261020_0023` follows `20261016_0022`:

- `actions.deadline_origin`: `email` or `owner`, NULL exactly when there is no deadline
  (CHECKs `deadline_origin_known` and `deadline_origin_set`). Every existing deadline came
  from an email, so the upgrade marks each `email`.
- `actions.stated_deadline_text`, `_precision`, `_date`, `_at_utc` and `_timezone`: the
  email's deadline you last replaced or cleared, stored as the deadline columns store one,
  with precision `none` when there is none.

The deadline columns hold the current deadline whoever set it, so nothing that reads them
changed. A deadline you set is a date or an instant with no phrase (`deadline_text` NULL):
`deadline_text` stays an email's words. Downgrading gives each of your deadlines its date (or
its UTC time) as its text, since older versions need one, drops the new columns and keeps
every task and deadline; who set it is lost on the way down, so upgrading again marks it the
email's. A change is one revision with an ordinary edit in the activity history. Backups, recovery and the writing export carry
the new columns, the export as an additive extension of format version 1.

## Owner acceptance

With Gmail disconnected:

1. Create a task with **Due by** next Friday at 17:00. Confirm **Suggested** names Thursday,
   no target is ticked for you, and the detail says "Due Fri …, 17:00 · Set by you".
2. Confirm the deadline shows on the Calendar and, on the day, in Your day.
3. Tick a target on Wednesday, then move the deadline to the Monday after. Confirm the target
   stays Wednesday. In the time field, press Up on 16:59 and confirm 17:00.
4. Untick **Due by** and save. Confirm the task has no deadline and keeps its target.
5. Quit and restart; make a backup and a writing export in Data, restore in a test profile,
   and confirm the deadline and `deadline_origin` in the export.

With Gmail connected:

6. Accept a suggestion with a dated deadline. Open the editor: confirm "The email says …"
   and no **Use the email's**.
7. Move its deadline and save. Confirm "You set this deadline. The email said …", then
   **Use the email's**, save, and confirm the email's deadline is back.
8. When a reply proposes a new deadline for a task whose deadline you set, confirm "Replace
   your deadline with …"; apply it, then Undo, and confirm your deadline is back.

Validation on macOS, 2026-10-20: the full suite with the Qt tests passed, with the 90%
per-service gate. Ruff formatting and lint and strict mypy for Windows and macOS passed. The
editor was reviewed in light and dark at 100%. Windows execution and owner desktop acceptance
remain pending.
