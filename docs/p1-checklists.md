# P1: checklists in place

This P1 slice is B04 in the [feature inventory](future-feature-inventory.md), subtasks and
checklists, in the form the inventory starts with: a task's existing ordered steps. Before
it, the detail pane showed the plan as text ("✓" or "○" before each step), and ticking a step
or adding one meant opening **Edit…**. Now an open task's plan is a checklist in the detail
pane: tick a step, or add one, and it is saved at once. Child tasks with dates of their own
remain later work. It builds on manual tasks ([p1-manual-tasks.md](p1-manual-tasks.md)) and
repeating tasks ([p1-recurring-tasks.md](p1-recurring-tasks.md)). Owner desktop acceptance is
still pending.

## The plan in the detail pane

On Actions and on Waiting, an open task's plan reads "Plan · 2 of 5 done", then each step
beside a check box:

- Tick a box, or click the step's words, to mark it done; untick it to mark it not done. A
  done step is muted. The heading and the row's "2/5 steps" follow.
- Under the steps, **Add a step**: type a step and press Return, and it goes last, not done.
  The field keeps what you typed until the step is saved, through a refresh too, and empties
  when you choose another task. A task without steps shows **Plan** and the field. At 30
  steps the field goes, as the editor's **Add step** does.
- Tab goes from the footer's buttons through the boxes to the field, then on to **Mark
  seen**; Space ticks the box with the focus, which keeps the focus.

Rename, reorder and remove steps in the editor, as before; its check boxes still work too. A
completed or skipped task's plan stays read-only, "✓" or "○" before each step: reopen it to
change its plan.

## Each change is one revision

- A tick, an untick or an added step is saved at once: one revision, with an ordinary edit
  ("Edited") in the activity history, as saving the editor makes. Edits in a row show in
  Activity as one line ("Edited 3 times"), so ticks don't crowd out the rest. A step that
  becomes done keeps the time it was first done until it is unticked; ticking a step already
  done changes nothing.
- The status line says how far the plan is ("2 of 5 steps done: Plan the party.", "Every step
  done: Plan the party.") or "Step added: Plan the party." Ticking the last step never
  completes the task: **Complete** (**Mark received** on Waiting) stays the main button.
- A tick has no **Undo**: tick it again. Like an edit, it withdraws an older Undo offer, which
  would refer to an earlier revision.
- While Forewit is busy with something else, a tick is put back and a step you add waits in
  its field, and the status line says "Forewit is busy; try again in a moment." If the task
  changed elsewhere since it was shown, the view reloads and says why, and each box shows
  what is stored. A step longer than 500 characters is refused with the reason, as in the
  editor, and stays in the field.

## Repeating tasks

Ticks and added steps belong to the occurrence. The series keeps its template's steps, so
the next occurrence starts with them, none done, as after any edit of one occurrence.
Ticking a step touches an occurrence, so reopening or undoing the one before it keeps it
([repeating tasks](p1-recurring-tasks.md)).

## What doesn't change

- There is no migration: steps stay in `action_steps` (`position`, `text`, `done`,
  `done_at_utc`). Each change is recorded as an `edited` activity, a kind every earlier build
  reads, so a database used with this version still opens in them.
- AI runs, follow-up proposals and adding an email never tick, add or change a step.
  Accepting a suggestion still makes its steps, none done.
- A step's words are the owner's or the suggestion's, shown as plain text beside the box:
  an "&" never becomes a keyboard shortcut, and no step appears in a tooltip.
- Search, AI drafting (the linked action's steps), backups, recovery and the writing export
  read steps as before.

## Owner acceptance

With Gmail disconnected:

1. Create a task with two steps. In the detail pane, tick the first. Confirm the status line
   says "1 of 2 steps done", the heading "Plan · 1 of 2 done", the step is muted and the row
   says "1/2 steps".
2. Click the second step's words. Confirm it ticks, the status says "Every step done", and
   the task is still open with **Complete** as the main button.
3. Untick it and confirm "1 of 2 steps done".
4. Type "Call the caterer" in **Add a step** and press Return. Confirm it is last and
   unticked, the field is empty, and Activity shows the changes as "Edited 4 times".
5. Press Tab from **Delete**: confirm the focus runs through the boxes to the field. Press
   Space on a box and confirm it ticks and keeps the focus.
6. Complete the task: confirm the plan shows "✓" and "○" and no field. Reopen it and confirm
   the boxes return.
7. In the editor, rename and reorder the steps; confirm the detail follows.
8. Quit and restart; confirm the ticks and the added step persist. Make a backup and a
   writing export in Data, and confirm `action_steps` carries `done` and `done_at_utc`.
9. Add a step to a repeating task and complete it. Confirm the next occurrence starts with the
   series' steps, unticked, without the added one.

With Gmail connected:

10. Accept a suggestion with steps and tick one in the detail pane. Confirm its source email,
    deadline and target date are unchanged.
