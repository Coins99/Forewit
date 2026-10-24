# ADR 0022: Waiting and commitments

Date: 2026-10-23. Status: proposed.

Builds on ADR 0011 (actions and suggestions), ADR 0015 (thread continuity) and ADR 0021
(owner deadlines).

## Context

Waiting has been an ownership value since M6: an open action that is `waiting_for` someone
else appears on the Waiting page instead of Open. Nothing recorded who that someone was, so the
Waiting page's avatar showed the first letters of the source email's address, or "?" for a
manual task, and its "Waiting since" showed the day the action was created, which is wrong for
a task the owner switches to waiting later. The owner's own commitments had no person at all,
so "what do I owe Sam?" had no answer. The general planner (B08) needs to track promises,
requests and the things the owner waits for, without inventing a contact system (I01, later)
and without asking the AI to guess who anyone is.

## Decision

- **One person per task.** `actions.person` is the owner's text, at most 120 characters,
  cleaned to one line (`clean_person`: no control or format characters, single spaces) and
  NULL when blank. On a waiting task it is who the owner waits on; on one of their own, who
  it is for. `person_key` is its NFKC case-folded key (`person_key`, the tags' rule), NULL
  exactly when there is no person, so "Alex", "ALEX" and full-width "Ａｌｅｘ" are one person to the
  filter and the lists. There is no person record: the name is the person, as a tag's name is
  the tag.
- **Only the owner names a person.** `ActionEdit.person` is required like the other fields,
  so an edit can't erase it by leaving it out. Accepting a suggestion names no one, and AI
  runs, follow-up proposals and adding an email never set or change it. The analysis schema
  doesn't change: the model is never asked who a task is with. The editor's **Use sender**
  fills the field from the source email (its cached display name when that can't pass for
  another address, else the address), and the owner saves it or not.
- **Waiting since.** `actions.waiting_since_utc` is set exactly while ownership is
  `waiting_for` (a CHECK keeps the two in step). Creating or accepting a waiting task, and an
  edit that makes a task waiting, set it to now; an edit that keeps it waiting leaves it; an
  edit that makes it the owner's clears it. Completing, reopening, deleting and restoring keep
  it, so a received item keeps when its wait began. A repeat's next occurrence
  waits from when it is made, like a task created then. The migration gives existing waiting
  tasks their creation time, which is what the Waiting page showed before.
- **No new date.** A waiting task's target date is its follow-up date, and its deadline is
  when the other person owes it, so "Overdue" means they are late. The desktop says "Follow
  up" where it would say "Target", and Your day says "Follow up today".
- **Repeats** copy the person through the series template (`action_series.person`) when
  **Change later repeats too** is ticked, like the other fields.
- **What it reaches.** The person is searched with the title, notes and steps, offered by the
  Actions filter (`group="person"`), printed by `actions list` and exported with the actions
  table. AI drafting does not send it: the linked action's parts stay those ADR 0013 lists,
  and a follow-up draft keeps a `[[name]]` placeholder until the owner chooses otherwise in a
  separate decision.

## Consequences

- The Waiting page names whom each item waits on and for how long, and prompts a follow-up
  only when one is due: a reply from others in the thread, or a follow-up date that has come
  or a deadline that has passed with no message in the thread since that the owner sent or
  marked seen.
- Additive migration `20261023_0024`. Downgrading drops the columns and keeps every task;
  upgrading again counts each waiting task's wait from its creation.
- A name is free text: two spellings that differ in more than case or width are two people.
  Contact records that join them are I01's.
- Sending the person with a drafting request, and asking the analysis who a task is with,
  are left for separate decisions.
