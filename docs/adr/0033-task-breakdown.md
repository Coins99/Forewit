# ADR 0033: AI breakdown of one task from context the owner chooses

Date: 2026-11-14. Status: proposed (P5-2 plan, [P5 plan](../p5-review-assistance.md)); not
implemented.

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
  the owner's instruction (at most 1,000 characters).
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
  the revision the generation read; it never changes, reorders, ticks or removes an existing
  step, and never touches title, notes, dates, deadline, priority, tags, estimate, person,
  project, focus or blocks. Undo removes exactly the added steps while the task is still at
  Apply's revision.
- **Failure.** A failed, declined, cancelled, invalid, stale or dismissed generation leaves
  the task and every plan exactly as they were. Logs and exceptions carry codes and counts.
- **Generation history.** `task_breakdowns` records each applied or dismissed generation
  without owner text: provider, model, prompt and schema versions, the part codes, counts
  proposed, applied and edited, the outcome, revisions read and produced and the added step
  IDs (for Undo and as a retry receipt). It survives backup, restore and purge and is left out
  of writing exports.
- **Provider.** A new `BreakdownProvider` port with a Groq adapter. No local-model breakdown
  while local AI is experimental, and no fallback between providers.

## Consequences

- A new additive migration adds `task_breakdowns`; consent needs none.
- Settings > AI lists the new consent beside drafting and calendar planning.
- Trial evidence for "AI suggestions accepted, edited or dismissed" comes from the counts,
  with no step text kept outside the task.
- Priority, estimate, subtask and multi-task suggestions need a new decision.
