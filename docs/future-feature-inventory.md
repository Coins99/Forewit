# Forewit future feature inventory

Created: 2026-10-11. Product planning inventory, not an implementation commitment.

## Direction and scope

Forewit should become a general personal life planner connecting email, tasks,
projects, calendars, notes, goals and progress dashboards. Student features are
shelved temporarily. Custom charts and configurable dashboards belong in the
general-planner proof of concept, although advanced analytics can arrive later.

This inventory preserves the features discussed with the owner, including deferred
ideas. `P0` is the immediate local-first email privacy/latency priority. `POC`
means the proposed first general-planner proof of concept; `Later` means a
subsequent general-planner release; `Deferred` means a separate expansion.
These priorities are proposals for review, not promises or evidence of existing
implementation. Existing email functionality remains a foundation and still needs
the live acceptance documented in AGENTS.md.

Research, architecture, frontend integration and verification are recorded in
[Future implementations](future-implementations.md). Each feature ID maps to a
research and implementation cluster there. D09 was added on 2026-10-28
as proposed P0 work and is not yet implemented.

## Complete inventory

### A. Daily workspace and capture

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| A01 | Unified Today dashboard | POC | Important mail, chosen tasks, agenda and upcoming deadlines together. |
| A02 | Morning brief | POC | Summarize confirmed commitments and relevant mail, with source links. |
| A03 | Daily priorities and capacity | POC | Choose a small priority list and see estimated work against entered availability. |
| A04 | Universal quick capture and capture inbox | POC | Capture tasks or notes rapidly; expense, link and reminder capture can follow. |
| A05 | Upcoming commitments | POC | Combine deadlines and appointments; bills and renewals join later. |
| A06 | End-of-day reflection | Later | Review progress and decide what to carry forward. |
| A07 | Weekly reset and review | POC | Review completed work, unfinished commitments and next week's plan. |
| A08 | Unified search | POC | Search owner tasks, notes and saved mail metadata/briefs; expand by data type. |

### B. Tasks, projects and life organization

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| B01 | Manual and email-derived tasks | POC | One task model with optional email sources. |
| B02 | Projects and customizable life areas | POC | Group work under projects and areas such as Home, Health or Personal. |
| B03 | Priorities, tags and filters | POC | Find and order work without requiring a deadline. |
| B04 | Subtasks and checklists | POC | Start with existing ordered steps; independently schedulable child tasks later. |
| B05 | Recurring tasks and routines | POC | Daily/weekly/monthly recurrence; skip and reschedule occurrences. |
| B06 | Separate deadlines, target dates and scheduled times | POC | Preserve commitment dates when planned work moves. |
| B07 | Effort and duration estimates | POC | Estimate workload; distinguish estimated, scheduled and recorded time. |
| B08 | Waiting and commitment tracking | POC | Track promises, requests and things awaiting another person's response. |
| B09 | List, board and calendar task views | Later | Saved views over the same tasks; list first in POC. |
| B10 | Milestones and dependencies | Later | Track project stages and optional blocking relationships. |
| B11 | Task/project templates | Later | Reuse structures for routines and common projects. |
| B12 | Life administration | Later | Use templates for appointments, applications, renewals, returns and reimbursements. |

### C. Calendar, time and planning

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| C01 | Daily agenda and week calendar | POC | View local events, work blocks and deadline markers. |
| C02 | Manual events and busy blocks | POC | Enter appointments and commitments without an integration. |
| C03 | Task time blocks | POC | Schedule multiple sessions for one task; move, resize or unschedule with undo. |
| C04 | Availability, working hours and downtime | POC | Set usable hours, breaks, buffers and locked commitments. |
| C05 | Conflict and workload detection | POC | Explain collisions, missing estimates and overfilled days. |
| C06 | Suggested daily plans and rescheduling | Later | Preview feasible proposals; a small deterministic daily proposal can be a POC stretch. |
| C07 | External calendar connections | Later | Read-only Google Calendar first; external writes require their own design. |
| C08 | Focus sessions and optional actual-time tracking | Later | Explicit start/stop with correction; calendar time is never treated as measured work. |
| C09 | Meeting preparation and follow-up | Later | Link notes, context, decisions and accepted tasks. |
| C10 | Reminder and notification controls | Later | Opt-in alerts, quiet hours, snooze and deduplication. |

### D. Email and communication

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| D01 | Important email and thread summaries | POC | Preserve existing briefs; expand thread summaries only with bounded consented context. |
| D02 | Suggested actions and promised-action detection | POC | Reuse accepted suggestions; extraction proposes rather than commits. |
| D03 | Unanswered mail and follow-up reminders | Later | Extend existing tracked-thread activity; define what unanswered means. |
| D04 | Multi-account and additional mail providers | Later | Separate credentials, sources and consent; Outlook remains dormant until scoped. |
| D05 | Sender rules and project-linked conversations | Later | Preserve exclusions; add owner-approved organization rules and links. |
| D06 | Reply drafting and reusable writing | POC | Reuse local drafts, selected context, versions, copy and export. |
| D07 | Gmail draft saving or sending | Deferred | Explicit new scope and permission design; current Gmail remains read-only. |
| D08 | What-changed brief | Later | New replies, changed deadlines, meetings and blockers since the last view. |
| D09 | Local-first privacy-aware email summarization | P0 | Opt-in local summaries without per-message cloud approval; separate conservative consent for optional cloud enrichment; incremental sync, validated cache, progressive UI and optional OS precomputation. |

### E. Notes, knowledge and context

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| E01 | Standalone and linked notes | POC | Reuse owner notes and link them to tasks/projects. |
| E02 | Modest formatting and text/Markdown export | POC | Searchable writing with recoverable revisions; rich editing later. |
| E03 | Backlinks and project context pages | Later | Show related tasks, notes, mail sources, meetings and eventually expenses. |
| E04 | Attachments, files and bookmarks | Later | Explicit imports, safe storage, export and source provenance. |
| E05 | Note/meeting templates | Later | Reuse structures for reviews, plans and meeting records. |
| E06 | AI summaries and action extraction from notes | Later | Selected context and review before creating tasks. |

### F. Goals, habits and personal measurements

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| F01 | Goals, milestones and targets | Later | Connect objectives to projects, tasks or metrics. |
| F02 | Custom numeric logs | POC | Record values such as reading minutes or a weekly savings balance. |
| F03 | Habit completion and flexible routines | Later | Daily/weekly expectations, skip rules and corrections. |
| F04 | Personal check-ins, mood and energy | Later | Optional private records; no mandatory monitoring. |
| F05 | Goal-to-plan assistance | Later | Propose milestones and work from a selected goal. |
| F06 | Trends and configurable habit reminders | Later | Inspect patterns and set opt-in reminders without a universal productivity score. |

### G. Dashboards, charts and progress

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| G01 | Configurable dashboard and widget editor | POC | Add, configure, resize and reorder widgets; one saved dashboard first. |
| G02 | Number cards | POC | Show a defined aggregate, optionally compared with a prior period. |
| G03 | Line and bar charts | POC | Numeric trends, daily completions and breakdowns by project. |
| G04 | Progress bars, heatmaps and breakdown charts | Later | Targets, activity calendars and proportion charts where appropriate. |
| G05 | Metric, filters, grouping and time-range controls | POC | Guided setup with explicit units, aggregation and bucket boundaries. |
| G06 | Optional targets and comparisons | POC | A simple target line/card first; sophisticated comparison settings later. |
| G07 | Chart drill-down and data inspection | POC | Open contributing records and explain calculation and missing data. |
| G08 | Starter templates and multiple dashboards | Later | My Week, Goals and Money; one starter template in POC. |
| G09 | Task and workload analytics | POC | Created/completed work and current overdue state; historical trends require event history. |
| G10 | Advanced formulas and threshold alerts | Later | Validated expressions and opt-in alerts; never arbitrary SQL or Python. |
| G11 | Chart/dashboard export | Later | Export data and visuals with metric definitions and time ranges. |

### H. Money and spending

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| H01 | Manual expenses, income and categories | Later | Dedicated financial records; do not infer transactions from generic metric logs. |
| H02 | Budgets, savings goals and spending trends | Later | Currency-aware totals, targets and comparisons. |
| H03 | Receipt and invoice extraction | Later | Propose reviewed records with source links and duplicate detection. |
| H04 | Bills, subscriptions and renewal alerts | Later | Keep projected bills separate from paid transactions. |
| H05 | Refunds, reimbursements and returns | Later | Link transactions and follow-up tasks without double counting. |
| H06 | CSV import/export | Later | Preview mapping, locale/currency handling and duplicates. |
| H07 | Financial account connections | Deferred | Separate provider, custody, security and reconciliation project. |

### I. People and relationships

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| I01 | Lightweight contact pages | Later | Owner-curated people, notes and optional contact links. |
| I02 | Linked conversations and meetings | Later | Find relevant context without copying source records. |
| I03 | Relationship follow-up and next-conversation notes | Later | Promises, requests and reminders associated with a person. |

### J. AI and automation

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| J01 | Task breakdown and priority suggestions | POC | Small optional proposal from selected tasks; preserve owner edits. |
| J02 | Ask selected workspace content | Later | Retrieval with citations and explicit context selection. |
| J03 | Project updates, meeting briefs and weekly review explanations | Later | Generate from confirmed records with selected scope. |
| J04 | Explainable workload and blocker suggestions | Later | Deterministic facts first; AI explanation cannot override constraints. |
| J05 | Rules and reusable workflows | Later | Triggers, conditions and proposed actions with preview, history and undo. |
| J06 | Scheduled reviews and threshold notifications | Later | Explicit job ownership, permission, quiet hours and retry rules. |
| J07 | Consent, context controls and generation history | POC | Extend existing controls per capability; no blanket project transmission. |

### K. Personalization, access and durability

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| K01 | Custom fields, linked records and saved views | Later | Typed fields and relations; no full arbitrary database builder in POC. |
| K02 | Keyboard shortcuts and command palette | POC | Capture, navigate and operate controls without a mouse. |
| K03 | Accessibility and adaptable layouts | POC | Keyboard alternatives, visible focus, readable charts and narrow-window behavior. |
| K04 | Offline access and backup/recovery | POC | Extend existing local storage, exports and tested restore to new records. |
| K05 | Version history and undo | POC | Retain owner text and revision-based conflict protection. |
| K06 | Web, mobile capture and device synchronization | Deferred | Desktop first; choose sync and server ownership after validation. |
| K07 | Workspace templates and notification preferences | Later | Optional depth and user-controlled defaults. |

### L. Collaboration

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| L01 | Shared projects, tasks, notes and calendars | Deferred | Separate ownership and authorization model. |
| L02 | Assignment, comments, permissions and activity history | Deferred | Define roles, conflicts and deletion before implementation. |

### S. Student expansion (shelved)

| ID | Feature | Scope | Intended behavior |
| --- | --- | --- | --- |
| S01 | Courses and semesters | Deferred | Course grouping built over projects and periods. |
| S02 | Assignments and assignment breakdowns | Deferred | Specialized task templates and deadlines. |
| S03 | Reviewed syllabus import | Deferred | Proposed dates and grading weights, never automatic authoritative facts. |
| S04 | Exam and study planning | Deferred | Study sessions and milestones over calendar/tasks. |
| S05 | Weighted grades and score scenarios | Deferred | Deterministic calculations with explicit grading rules. |
| S06 | Study summaries, practice questions and flashcards | Deferred | Selected notes and optional spaced review. |
| S07 | Group assignment responsibilities | Deferred | Reuse waiting/commitments; collaboration only when its model exists. |
| S08 | Student budgeting | Deferred | Templates over general finance features. |

## Scope boundaries

- Shipping the POC does not require every Later or Deferred item.
- A custom savings-balance log in the POC is not a bank connection or expense ledger.
- Checklists in the POC do not imply a full parent/child dependency engine.
- Manual calendar entries come before calendar synchronization or writes.
- Scheduled work is not measured work; suggestions are not confirmed records.
- Current Gmail permissions, email privacy rules and owner-controlled changes remain
  in effect. This inventory authorizes planning only, not new application behavior.
