# ADR 0021: Deadlines the owner sets

Date: 2026-10-20. Status: proposed.

Builds on ADR 0011 (actions and suggestions), ADR 0016 (follow-up proposals) and ADR 0018
(calendar).

## Context

An action's deadline came only from an email: the analysis resolves the email's phrase to a
date or an instant, accepting a suggestion copies it, and an applied follow-up proposal
replaces it. The owner could move the target date but not the deadline, and a manual task
never had one. The general planner (B06) needs commitments the owner states, kept apart from
the day they plan to do the work, and it must not lose what the email said when the owner
disagrees with it. The deadline columns are read in many places: ordering, overdue, Your day,
the Calendar's markers, thread links, Plan with AI and the CLI.

## Decision

- **One current deadline.** The `deadline_*` columns keep holding the current deadline,
  whoever set it, so every reader stays as it is. `actions.deadline_origin` says who set it,
  `email` or `owner`, and is NULL exactly when there is none; a CHECK keeps the two in step.
- **The email's deadline is kept.** When the owner replaces or clears an email's deadline,
  it is copied to `stated_deadline_*` first. Replacing the owner's own deadline leaves that
  copy alone, so it is always the latest email deadline the owner overrode. Going back to it
  (`DeadlineChange.from_email()`) makes it the current deadline again, as the email's.
- **An owner deadline** is a date from 2000 to 2099, or a wall-clock time on it, in the
  owner's zone (`OwnerZone`, shared with repeating tasks). It has no phrase:
  `deadline_text` stays an email's words, and `has_phrase()` relaxes the shared check for an
  action whose deadline the owner set. A time a clock change skips is read as `wall_clock()`
  reads it, and a zone with no IANA name (tzlocal's "local") is stored as UTC, keeping the
  instant. AI drafting sends an owner deadline as its date, or date and local time.
  Downgrading writes each one's date as its text, since older versions require a phrase.
- **Changes.** Only `ActionService.create()` and `save()` take a `DeadlineChange` (set,
  clear, or back to the email's); leaving it out keeps the deadline. Each is part of the
  edit's one revision and records an ordinary edit. A deadline that falls when the current
  one does changes nothing, so an email's stays the email's.
- **Targets.** A changed deadline re-suggests the target, counted from the day of the change;
  the target date stays the owner's. The editor may move a ticked target that was the
  suggestion, in its fields, before the owner saves, but never ticks one.
- **Follow-up proposals.** A new deadline from a later email stays a proposal the owner
  applies; over an owner deadline it says it replaces theirs. Applying it makes the deadline
  the email's, and Undo restores the deadline with its origin; an apply saved before this
  ADR restores an origin inferred from its deadline. While the owner's choice stands (they set
  or cleared the deadline), an email naming the email deadline they replaced proposes
  nothing; once an email's deadline is current again, that copy no longer counts.
- **What doesn't change.** AI runs never write a deadline. Moving a target date, a Calendar
  event or, later, a work block never moves a deadline. A repeating task's next occurrence
  starts without one.

## Consequences

- Owners can date any task's commitment, and disagree with an email without losing it.
- Additive migration `20261020_0023`; downgrading keeps every deadline as an ordinary one,
  but forgets who set it.
- One copy of the email's deadline, not a history: replacing an email deadline twice, with a
  proposal applied in between, keeps the later email's.
- A deadline relative to a repeating task's date, and work blocks (C03), are left for later.
