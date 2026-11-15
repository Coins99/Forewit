# ADR 0033: AI breakdown of one task from context the owner chooses

Date: 2026-11-14. Status: accepted with P5-2 ([P5 plan](../p5-review-assistance.md));
implemented, owner live acceptance open.

Builds on ADR 0010 (Groq), ADR 0013 (drafting consent and previews), ADR 0018 (proposals the
owner applies), ADR 0022 (the task's person) and ADR 0024 (retry receipts).

## Context

P5 adds J01's small task-breakdown proposal and J07's per-capability consent and generation
history. It is a new data path to the AI provider: task text leaves the device for a purpose
the owner has not consented to before. Drafting already sends a linked task's title, steps and
notes, but only as context for a draft, under drafting consent. Consent must not carry over
between purposes (§3.10: proposals "cannot inherit permission" from another capability), and
the model stays untrusted: a task's notes may hold pasted email text that tries to steer it.

## Decision

- **One task, the owner's choice.** A breakdown starts from one live, open task the owner
  selects. Nothing runs automatically, in bulk or across projects.
- **Sent always:** a random request key, the task title, today's date in the owner's zone and
  the owner's instruction (at most 1,000 characters). The preview marks a title that came from
  an email. While email analysis is in local mode, a task with an email source cannot be
  broken down, so a title written from local-only mail never reaches the cloud.
- **Sent only when ticked for that generation:** the notes (at most 2,000 characters), the
  existing steps in order with done marks (at most 2,000 in all), the project and area names,
  the deadline and target date as local values, and the owner's estimate. None is ticked by
  default.
- **Never sent:** the task's person, its tags, source-email snapshots, email bodies, other
  tasks, notes or drafts, work blocks, events, metrics, Gmail or Forewit IDs, the owner's
  address or credentials. Ticked parts are sent as written, and the consent text says that
  addresses, links or numbers inside them go too.
- **Consent and preview.** First use records consent in `owner_consents` with scope
  `breakdown` and disclosure `1`, per provider, for the owner. Every generation shows each
  part with its size, the provider, model and privacy notice, and waits for approval.
  Brief, drafting and calendar-planning consent grant nothing here, and this grants nothing
  to them. Revoke AI consent revokes it with the rest.
- **Output schema, version 1:** one to eight steps of at most 120 characters and up to three
  missing-context items of at most 200; strict Structured Outputs, nothing else. Python
  removes control and format characters, collapses whitespace and drops empty, repeated and
  already-present steps. Groq's HTTP 400 `json_validate_failed` is retried once.
- **Apply.** Only the owner applies, after reviewing and optionally editing or unticking each
  step. Apply appends steps through the B04 checklist path as one `edited` revision, needing
  the revision the generation read, in the same transaction as the generation's record; it never changes, reorders, ticks or removes an existing
  step, and never touches title, notes, dates, deadline, priority, tags, estimate, person,
  project, focus or blocks. Undo removes exactly the added steps while the task is still at
  Apply's revision.
- **Failure.** A failed, declined, cancelled, invalid, stale or dismissed generation leaves
  the task and every plan exactly as they were. Logs and exceptions carry codes and counts.
- **Generation history.** `task_breakdowns` records every generation that reaches the
  preview, declined, cancelled and failed ones included, without owner text: provider, model,
  prompt and schema versions, the part codes, counts proposed, applied and edited, the outcome,
  an error code for a failure, revisions read and produced and the added step IDs (for Undo
  and as a retry receipt that commits with the steps). It survives backup, restore and purge and is left out
  of writing exports.
- **Provider.** A new `BreakdownProvider` port with a Groq adapter. No local-model breakdown
  while local AI is experimental, and no fallback between providers.

## Consequences

- Migration `20261114_0040` adds `task_breakdowns`; consent needs none.
- Settings' AI card lists the active owner consents, breakdown among them, and its revoke
  button names breakdown.
- Trial evidence for "AI suggestions accepted, edited or dismissed" comes from the counts,
  with no step text kept outside the task.
- Priority, estimate, subtask and multi-task suggestions need a new decision.

## Decided while building (2026-11-14)

- **When the history row is written.** At the end of a run: `proposed`, `no_steps`, `failed`
  with its code, `declined` or `cancelled`. When the owner closes the preview without asking,
  the window records `declined`; when they stop a call, `cancelled`. Previewing again with
  other ticks replaces the prepared generation without a row, so one sitting is one row.
  The diagnostic's `--dry-run` writes none.
- **Two keys.** The request key is random, sent and never stored. The generation's public ID
  is stored and never sent, since Forewit IDs never go.
- **Oversized steps are dropped, not cut.** A step over 120 characters is left out; half an
  instruction is worse than none. More than eight steps, or more than the task has room
  for, keep the first ones.
- **The deadline is sent as a local value only**, never the words an email stated it in:
  those are the email's.
- **Apply writes the steps itself.** `BreakdownService.apply` adds the step rows, the task's
  revision and its `edited` activity, and updates the generation's row, in one commit.
  `edited_count` is worked out there from the proposal in memory, so no step text is kept
  outside the task. Applying an already applied generation returns the task as it is.
- **Undo** records an `undo` activity that names Apply's revision, as the other undos do.
- **Where it is refused.** The service refuses a missing, deleted, closed or full task, and
  in local mode a task with an email source; the desktop's button says the same reasons and
  also when no Groq key or model is saved.

## Known limit

The local-mode rule looks at the mode now. A task made from mail while analysis ran on this
computer can be broken down after the owner switches email analysis to the cloud: its title
would then reach Groq. The preview marks every task that has an email source, and the owner
approves each send. Recording which analysis wrote a task's title would need new storage and
a new decision.
