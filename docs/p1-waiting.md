# P1: waiting and commitments

This P1 slice is B08 in the [feature inventory](future-feature-inventory.md): track promises,
requests and the things you wait for from other people. Before it, a task was yours or
someone else's, but Forewit didn't know whose: the Waiting page showed the first letters of
the email's address, or "?", and dated the wait from when the task was created. Now a task
names who it is with, and a waiting task knows since when it waits. The design is
[ADR 0022](adr/0022-waiting-and-commitments.md). It builds on manual tasks
([p1-manual-tasks.md](p1-manual-tasks.md)), repeating tasks
([p1-recurring-tasks.md](p1-recurring-tasks.md)) and deadlines
([p1-deadlines.md](p1-deadlines.md)). Owner desktop acceptance is still pending.

## Who a task is with

The task editor has a row under **Whose**:

- **Waiting on**, for a task someone else does: who you're waiting on.
- **For**, for one of yours: who it's for, if anyone. That makes it a promise you can find
  again: "Send Sam the slides", for Sam.

The label follows **Whose** as you change it. A name is what you type, tidied to one line, up
to 120 characters; clear it to name no one. Names match whatever their case or width, so
"Alex" and "ALEX" are the same person in the filter and the lists, but "Alex" and "Alex Kim"
are two.

A task accepted from a brief names no one: only you do. **Use sender** fills the field from
the task's email: the sender's name while the email is still in local mail, unless the name
looks like an email address, and otherwise the address, tidied as a name you type is. Save
to keep it.

## Waiting since

A waiting task waits from when it became someone else's: when you created or accepted it as
**Waiting for someone**, or saved it with **Whose** changed to that. Saving other changes
keeps the date. Making it yours ends the wait, and making it waiting again starts a new one.
Tasks that were waiting before this version wait from when they were created, as the Waiting
page showed before.

## Follow-up dates

For a task you wait for, the target date is when you'll follow up: the editor calls it
**Follow-up date** (the row is **Follow up**), the detail pane and rows say "Follow up Mon
Oct 5", Your day says "Follow up today", and the Calendar's marker says "Follow up · …". The
deadline stays when the other person owes it, so "Overdue" means they are late. Nothing else
changes about either date.

## The Waiting page

- Each row starts with whom it waits on and for how long ("Alex Kim · 3 days"), or "Waiting
  3 days" without a name; the avatar shows the person's initials ("AK"; "A" for "Alex", as
  the reading pane shows a sender's), else the email's sender's. Search waiting items finds
  names too.
- The detail pane's state line says "Waiting on Alex Kim · open", and its facts are
  **Waiting on**, **Waiting since** ("Wed Sep 30 · 3 days") and **Follow-up**.
- A prompt appears only when something calls for it: "{sender} replied" when the thread has
  messages from others since you marked it seen ("If it's what you were waiting for, mark it
  received."), else "Follow up with Alex" (or "Time to follow up") when the deadline has
  passed or the follow-up date has come, unless you've already followed up: a message in
  the thread you sent, or marked seen, on or after that day quiets it. **Draft follow-up** and
  **Mark received** are where they were.
- **New task** shows on the Waiting page too, and starts the task as **Waiting for someone**.

For your own tasks the state line reads "Yours, for Sam · open", the **Owner** fact "You, for
Sam", rows "For Sam", Your day "Today · for Sam" and the Calendar "… · for Sam". The Actions
filter offers each person ("Person: Sam") beside projects, areas and tags. It narrows the
Actions page only: Waiting, which has its own search, always lists everything you wait for.

## Repeating tasks

The person is part of a repeating task's template: **Change later repeats too** carries a
change to later repeats, as it does for the title. Each new occurrence of a waiting series
waits from when it is made.

## What doesn't change

- AI runs, follow-up proposals and adding an email never name or change the person.
- AI drafting doesn't send the person: the linked action's parts are as before (ADR 0013), so
  a drafted check-in keeps a `[[name]]` placeholder for you to fill.
- The analysis isn't asked who a task is with.

## Where else it shows

- Go to or search (Ctrl+K) finds a task by its person.
- `forewit-gmail-diagnostic actions list`: "(waiting for Alex)" or "(mine, for Sam)", and
  "waiting since 2026-10-12" for a waiting task, in the zone you pass. Editing stays in the
  desktop.

## Storage, durability and history

Additive Alembic revision `20261023_0024` follows `20261020_0023`:

- `actions.person` and `actions.person_key`: the name, and its key (`person_key`, NFKC case
  folding), both NULL when there is none (CHECKs `person_length` and `person_key_set`), with
  an index on the key.
- `actions.waiting_since_utc`: set exactly while the task waits for someone (CHECK
  `waiting_since_set`). The upgrade gives existing waiting tasks their creation time.
- `action_series.person`: the template's person (CHECK `person_length`).

A change is part of the edit's one revision, with an ordinary edit in the activity history.
Backups, recovery and the writing export carry the new columns, the export as an additive
extension of format version 1. Downgrading drops the columns and keeps every task.

## Owner acceptance

With Gmail disconnected:

1. On Waiting, choose **New task**. Confirm **Whose** is **Waiting for someone** and the rows
   read **Waiting on** and **Follow up**. Name Alex, set the follow-up date to today and save.
2. Confirm the row says "Alex · since today", the avatar "A", the detail "Waiting on Alex ·
   open" with the prompt "Follow up with Alex", and Your day "Follow up today · waiting on
   Alex".
3. Edit a task of yours, name Sam under **For** and save. Confirm "Yours, for Sam · open",
   and that the Actions filter's "Person: Sam" lists it.
4. Change a waiting task's **Whose** to **Mine** and back. Confirm **Waiting since** restarts
   today.
5. Search Ctrl+K for "alex" and confirm the task is found.
6. Quit and restart; make a backup and a writing export in Data, restore in a test profile,
   and confirm the person and `waiting_since_utc` in the export.

With Gmail connected:

7. Accept a suggestion that waits for someone. Confirm it names no one and waits since today.
   Open the editor, choose **Use sender** and confirm the sender's name, then save.
8. When the other person replies in the thread, confirm the prompt "{sender} replied", then
   **Mark received**.
