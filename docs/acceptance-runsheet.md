# Owner acceptance run-sheet

One checklist for walking through Forewit's planner in the real desktop app, a page at a time.
It gathers the "Owner acceptance" steps scattered over the feature docs, in the order you meet
them in the sidebar, with duplicates merged. Each step links to the section it came from.

## How to use it

- Tick a step that works.
- For one that fails, send back its ID and one line saying what you saw (a screenshot helps),
  for example: `TASK-07: the next task kept the old title`.
- Ticking is your act. Nothing in this file, and no agent or test run, marks a feature
  accepted; the docs keep saying "pending" until you decide otherwise.
- Use a test profile for anything that deletes, restores or changes data. Gmail stays
  disconnected unless a step says "Gmail connected".
- Steps that say "light and dark" mean: look again with the system in each mode.
- Where the source says a step is for a macOS or fractional-scale display, it is listed under
  [Platform checks](#platform-checks).

Questions where two docs disagree, or a step may describe an older screen, are at the end under
[Questions for the owner](#questions-for-the-owner). A step marked (?) is one of them.

## Setup

- [ ] **SETUP-01** Start the app with a test profile and Gmail disconnected. Confirm the sidebar
  shows Today, Tasks, Projects, Calendar, Notes, Insights and Mail, and that Ctrl/Cmd+1 to 8 still
  open Today, Inbox, Tasks, Waiting, Mail drafts, brief history, Calendar and Notes.
  Source: [p2-navigation](p2-navigation.md#implemented-behavior), [AGENTS.md](../AGENTS.md). (?)
- [ ] **SETUP-02** The first run on a database from before the calendar work applies its
  migrations without an error. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) (P3-1 step 0). (?)

## Today

- [ ] **TODAY-01** Today leads with My focus, its estimated time, the count of tasks with no
  estimate and the waiting follow-ups. Under it are the agenda preview and "Other commitments
  needing attention". Source: [p2-focus](p2-focus.md#workflow),
  [p2-workspace](p2-workspace.md#owner-workflow). (?)
- [ ] **TODAY-02** Choose **Plan today**. Search for open tasks, add two, and reorder them with
  Move up and Move down and with Alt+Up and Alt+Down. Choose **Remove** on one: only the
  selection goes, the task stays. Source: [p2-focus](p2-focus.md#workflow).
- [ ] **TODAY-03** Complete and reopen a selected task from here, then Undo. Completed selections
  stay visible. A waiting task can be selected as a follow-up and its estimate is not counted as
  your time. Source: [p2-focus](p2-focus.md#workflow).
- [ ] **TODAY-04** Use the date picker and **Go to today** in Plan today. A date you browsed to
  stays after a refresh. **Carry** or **Move** selections to another day: you get a preview, copy
  keeps the source, move changes both, and no task date or deadline changes. Source:
  [p2-workspace](p2-workspace.md#owner-workflow).
- [ ] **TODAY-05** Choose **Your day**. Three cards show: Needs attention (overdue, due today,
  planned for today, planned earlier; each task once), Today's agenda (all-day events first),
  and New replies. Double-click a row, or press Return on it, to open it. Source:
  [today](today.md). (?)
- [ ] **TODAY-06** With a task due today, check its row says "Due 17:00" (or "Due today" for a
  date) and an overdue one says "Overdue". Complete or edit a task and see the cards follow.
  Source: [today](today.md), [p1-deadlines](p1-deadlines.md#owner-acceptance) step 2.
- [ ] **TODAY-07** The agenda shows up to two appointments with long titles shortened;
  **Agenda** opens today's full calendar. Source: [p2-focus](p2-focus.md#workflow).
- [ ] **TODAY-08** Today's focus says how much time is free today. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop) step 7.
- [ ] **TODAY-09** Upcoming shows seven dates and can expand to thirty; a task's target and
  deadline reasons share one row, and events stay separate. Upcoming points to the Calendar for
  free time, and does not claim to plan capacity. Source:
  [p2-workspace](p2-workspace.md#owner-workflow),
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop) step 7.

## Tasks

- [ ] **TASK-01** On Tasks, create a task with notes, two steps and a target date. Edit its
  title and tick a step. Source: [p1-manual-tasks](p1-manual-tasks.md#acceptance) steps 1-2.
- [ ] **TASK-02** Complete the task, then reopen it. Activity still shows the completion and the
  reopening times. Undo the reopen: the original completion time returns. Reopen once more.
  Source: [p1-manual-tasks](p1-manual-tasks.md#acceptance) steps 2-4.
- [ ] **TASK-03** Do TASK-02 again on a task that came from an email (Gmail connected): the same
  controls work. Source: [p1-manual-tasks](p1-manual-tasks.md#acceptance) step 7.
- [ ] **TASK-04** Create undated tasks with High, Low and no priority. Open lists High, Low, then
  unset. Give one a target date: it moves above the undated ones whatever its priority. Source:
  [p1-priorities-tags](p1-priorities-tags.md#owner-acceptance) steps 1-2.
- [ ] **TASK-05** Tag two tasks `home` and `Home`. Both read `home`, and the filter offers one
  **Tag: home**. Change it to `Home` on one task: both read `Home`. Source:
  [p1-priorities-tags](p1-priorities-tags.md#owner-acceptance) step 3.
- [ ] **TASK-06** Choose the tag filter: both tasks show, and Completed shows one after you
  complete it. Remove the tag from both: the filter goes back to All tasks. Source:
  [p1-priorities-tags](p1-priorities-tags.md#owner-acceptance) steps 4 and 6.
- [ ] **TASK-07** Type a 41-character tag. The editor stays open with the reason and your input.
  Source: [p1-priorities-tags](p1-priorities-tags.md#owner-acceptance) step 5.
- [ ] **TASK-08** Repeat the priority and tag edits on an email-derived task (Gmail connected).
  Source: [p1-priorities-tags](p1-priorities-tags.md#owner-acceptance) step 8.
- [ ] **TASK-09** Create a task with **Every day** and no target date: it is dated today. Complete
  it: the status line names tomorrow, and a new open task is dated tomorrow with the same title,
  unticked steps and the same tags. **Undo complete**: the first is open and tomorrow's is gone.
  Source: [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance) steps 1-3.
- [ ] **TASK-10** Skip it: it is in neither Open nor Completed, and tomorrow's says "Follows ... .
  Skipped". Undo the skip. Source: [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance)
  step 4.
- [ ] **TASK-11** Edit it: rename, untick **Change later repeats too**, move the target date two
  days on, then complete it. The next task has the old title and tomorrow's date. Source:
  [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance) step 5.
- [ ] **TASK-12** Make a weekly task on Tuesday and Friday and a monthly task on the 31st. The
  detail lines read "Repeats every Tue and Fri" and "Repeats on the 31st of each month (not in
  shorter months)". Source: [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance) step 6.
- [ ] **TASK-13** Set **Until** to tomorrow on a daily task and complete it twice: no third
  occurrence. Choose **Doesn't repeat** on a repeating task: "Repeats stopped", no Skip, and no
  next task on completion. Source: [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance)
  steps 7-8.
- [ ] **TASK-14** Add a step to a repeating task and complete it: the next occurrence starts with
  the series' steps, unticked, without the added one. Source:
  [p1-checklists](p1-checklists.md#owner-acceptance) step 9.
- [ ] **TASK-15** Create a task with **Due by** next Friday at 17:00. **Suggested** names
  Thursday, no target is ticked for you, and the detail says "Due Fri ..., 17:00 . Set by you".
  Source: [p1-deadlines](p1-deadlines.md#owner-acceptance) step 1.
- [ ] **TASK-16** Tick a target on Wednesday, then move the deadline to the Monday after: the
  target stays Wednesday. In the time field press Up on 16:59: it becomes 17:00. Untick **Due by**
  and save: no deadline, target kept. Source: [p1-deadlines](p1-deadlines.md#owner-acceptance)
  steps 3-4.
- [ ] **TASK-17** The deadline shows on the Calendar and, on the day, in Your day. Source:
  [p1-deadlines](p1-deadlines.md#owner-acceptance) step 2.
- [ ] **TASK-18** (Gmail connected) Accept a suggestion with a dated deadline. The editor says "The
  email says ..." and has no **Use the email's**. Move the deadline and save: "You set this
  deadline. The email said ...", then **Use the email's** and save brings the email's back.
  Source: [p1-deadlines](p1-deadlines.md#owner-acceptance) steps 6-7.
- [ ] **TASK-19** (Gmail connected) When a reply proposes a new deadline over one you set, it says
  "Replace your deadline with ..."; apply it, then Undo: your deadline is back. Source:
  [p1-deadlines](p1-deadlines.md#owner-acceptance) step 8.
- [ ] **TASK-20** (Gmail connected) Accept a suggestion whose deadline is words alone ("ASAP"):
  **Clear deadline**, save, and the task has none. Reopen the editor, **Use the email's**, save:
  "ASAP" is back. Source: [p1-deadlines](p1-deadlines.md#owner-acceptance) step 9.
- [ ] **TASK-21** On Waiting, choose **New task**. **Whose** is **Waiting for someone** and the
  rows read **Waiting on** and **Follow up**. Name Alex, follow-up today, save. The row says
  "Alex . since today" with the avatar "A", the detail "Waiting on Alex . open" with the prompt
  "Follow up with Alex", and Your day "Follow up today . waiting on Alex". Source:
  [p1-waiting](p1-waiting.md#owner-acceptance) steps 1-2.
- [ ] **TASK-22** Edit a task of yours, name Sam under **For** and save: "Yours, for Sam . open",
  and the filter "Person: Sam" lists it. Change a waiting task's **Whose** to **Mine** and back:
  **Waiting since** restarts today. Source: [p1-waiting](p1-waiting.md#owner-acceptance) steps 3-4.
- [ ] **TASK-23** (Gmail connected) Accept a suggestion that waits for someone: it names no one
  and waits since today. In the editor **Use sender** gives the sender's name. When the person
  replies in the thread the prompt reads "{sender} replied", then **Mark received**. Source:
  [p1-waiting](p1-waiting.md#owner-acceptance) steps 7-8.
- [ ] **TASK-24** On a task with two steps, tick the first in the detail pane. Status "1 of 2 steps
  done", heading "Plan . 1 of 2 done", the step muted, the row "1/2 steps". Click the second step's
  words: it ticks, status "Every step done", the task stays open with **Complete** the main
  button. Untick: "1 of 2 steps done". Source: [p1-checklists](p1-checklists.md#owner-acceptance)
  steps 1-3.
- [ ] **TASK-25** Type "Call the caterer" in **Add a step**, press Return: it is last and unticked,
  the field is empty, and Activity says "Edited 4 times". Source:
  [p1-checklists](p1-checklists.md#owner-acceptance) step 4.
- [ ] **TASK-26** Press Tab from **Delete**: focus runs through the boxes to the field. Space on a
  box ticks it and keeps focus. Source: [p1-checklists](p1-checklists.md#owner-acceptance) step 5.
- [ ] **TASK-27** Complete the task: the plan shows check and circle marks and no field. Reopen:
  the boxes return. In the editor rename and reorder steps: the detail follows. Source:
  [p1-checklists](p1-checklists.md#owner-acceptance) steps 6-7.
- [ ] **TASK-28** (Gmail connected) Accept a suggestion with steps and tick one: its source email,
  deadline and target date do not change. Source:
  [p1-checklists](p1-checklists.md#owner-acceptance) step 10.
- [ ] **TASK-29** Quit and restart: the task, edits, steps, ticks, added step, activity, series
  and skipped occurrence persist. Source: [p1-manual-tasks](p1-manual-tasks.md#acceptance) step 5,
  [p1-checklists](p1-checklists.md#owner-acceptance) step 8,
  [p1-recurring-tasks](p1-recurring-tasks.md#owner-acceptance) step 9.
- [ ] **TASK-30** Search (Ctrl+K) for "alex": the waiting task is found. Source:
  [p1-waiting](p1-waiting.md#owner-acceptance) step 5.

## Projects

- [ ] **PROJ-01** Create a life area named Learning and a project under it. Create a manual task
  assigned to the project, and assign an existing email task to it. Source:
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) steps 1-2.
- [ ] **PROJ-02** Filter Tasks by the project and the area: both tasks use the usual editor,
  completion and reopening. Rename the project: labels update. Source:
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) step 3.
- [ ] **PROJ-03** Complete a task, then move it to another project. The writing export keeps the
  earlier completion's original project and area. Source:
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) step 4.
- [ ] **PROJ-04** Archive the first project: its tasks stay visible and editable, new tasks cannot
  choose it. Reopen the project: it can be chosen again. Source:
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) step 5.
- [ ] **PROJ-05** Remove the area: its projects and tasks stay, with no area. Source:
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) step 6.
- [ ] **PROJ-06** The Projects page shows a project's context: its tasks and notes. Source:
  [p2-workspace](p2-workspace.md#owner-workflow).

## Calendar

- [ ] **CAL-01** Open **Working hours**, choose **Weekdays 9 to 5**, set a 10-minute buffer and
  save. Hours outside 09:00-17:00 and the weekend are shaded; the line under the hours says how
  much is free. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 1.
- [ ] **CAL-02** Make a timed event **Free**: it shows as an outline and free time does not drop.
  Make an all-day event **Busy**: that day says "Day off". Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 2.
- [ ] **CAL-03** With Google Calendar connected, mark an event Free there and refresh: it is an
  outline here too. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 3.
- [ ] **CAL-04** Switch to **Day**. The left and right arrows, and the Left and Right keys, move by
  a day; **Today** comes back. Restart: the Day view is still chosen. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 4.
- [ ] **CAL-05** Show **Tasks to schedule**. A task with a 3-hour estimate and a 1-hour block today
  says "2 hrs left of 3 hrs". Select a task and press Return: the block editor opens at the next
  free stretch. Save. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) steps 5-6.
- [ ] **CAL-06** Drag another task onto Wednesday 10:15 in Week view: it is scheduled there; press
  **Undo schedule**. A block at 07:00 says it is outside your working hours. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) steps 7-8.
- [ ] **CAL-07** Give a task a 2-hour estimate. **Schedule time...** suggests 2 hours. Save: the
  block shows in Week, Month, List and Today, and the detail says 2 hrs. Add a second block on
  another day. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) steps 1-2.
- [ ] **CAL-08** Drag a block to another day and time, **Undo move**. Resize it by its bottom
  edge, **Undo resize**. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) steps 3-4.
- [ ] **CAL-09** Select a block, press Ctrl or Cmd with the arrows, then Ctrl or Cmd with
  Shift+Down. **Undo**: the whole run goes back. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) step 5.
- [ ] **CAL-10** Unschedule a block with Delete, **Undo unschedule**. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) step 6.
- [ ] **CAL-11** Schedule a block over an event and over a Google event: the status line names
  both. Schedule one after the task's deadline: a warning, and the task's dates stay unchanged.
  Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) steps 7-8.
- [ ] **CAL-12** Complete the task: its blocks grey out and stop moving. Reopen it, delete it, then
  Undo. Complete a repeating occurrence that has a block: the next occurrence has none. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) steps 9-10.
- [ ] **CAL-13** With weekday hours 9 to 5, give Wednesday 6 hours of blocks and three focus tasks
  of an hour each: Wednesday's head says "Over by 1h"; choose the day and the line under the hours
  explains the sum. Remove one task's estimate: the day becomes provisional ("Over by 1h+", "at
  least"). Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop) steps 1-2.
- [ ] **CAL-14** With a 10-minute buffer, put a block 5 minutes after a meeting: it has a conflict
  mark, choosing it says "within the buffer of ...", and saving said so too. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop) step 3.
- [ ] **CAL-15** Overlap two blocks, put one on a day off, one after its task's deadline, and set
  more time aside than a task's estimate: each says why. Make the meeting Free: its conflict and
  busy time go away. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop) steps 4-5.
- [ ] **CAL-16** With Google Calendar connected, look at a week its cache does not hold:
  provisional; after Refresh, not. Past days show figures but no flags. Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop) steps 6 and 8.
- [ ] **CAL-17** In Plan with AI with Google times ticked, the preview counts only busy Google
  events and shows no blocks. A proposed change onto a block warns "Task . ...". Source:
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 9,
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) step 11.
- [ ] **CAL-18** One ordinary week: split working hours, a dated day off, Busy and Free events, a
  locked block, a stale review, batch Undo. The scheduling add-ons have no numbered steps, so judge
  each against its description. Source:
  [p3-scheduling-addons](p3-scheduling-addons.md#hands-on-acceptance-still-required). (?)
- [ ] **CAL-19** A task's deadline and target date show on the Calendar, and moving a block never
  moves them. Source: [p1-deadlines](p1-deadlines.md#owner-acceptance) step 2,
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) step 8.

## Notes

- [ ] **NOTE-01** Create projects Home and Work and archive Work. Choose **Notes** (Ctrl+8). Mail's
  drafts (Ctrl+5) no longer list notes, and their count excludes them. Source:
  [p2-notes](p2-notes.md#owner-acceptance) steps 1-2.
- [ ] **NOTE-02** New note: type a title and text, pick Home under Project. Filter Notes by Home and
  No project: the note moves between them. Work is not offered. Source:
  [p2-notes](p2-notes.md#owner-acceptance) step 3.
- [ ] **NOTE-03** **Link to task...**, search for a task and link it, then Unlink: the task itself
  is unchanged. Source: [p2-notes](p2-notes.md#owner-acceptance) step 4.
- [ ] **NOTE-04** Open the note from Ctrl+K and from Capture's **Open**: both show the Notes view.
  Source: [p2-notes](p2-notes.md#owner-acceptance) step 5.
- [ ] **NOTE-05** Rename Home: the note's row shows the new name. Archive Home: the note keeps it,
  but cannot move back once moved out. Source: [p2-notes](p2-notes.md#owner-acceptance) step 6. (?)
- [ ] **NOTE-06** A task's writing menu opens its linked notes; **All notes** clears that filter.
  Source: [p2-workspace](p2-workspace.md#owner-workflow).

## Insights

- [ ] **INS-01** Insights is in the sidebar and the palette, and opens offline with no sign-in.
  Ctrl/Cmd+1 to 8 keep their meanings. Source: [p4-insights](p4-insights.md#current-increment),
  [AGENTS.md](../AGENTS.md). (?)
- [ ] **INS-02** Choose **Manage metrics** and create an amount metric and a point-value metric.
  Log entries with exact decimal input, a date and a project. Edit, delete, restore and Undo one.
  Repeating an identical log makes a new observation. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-03** With unsaved entry input, selecting another row does not replace it; **Discard
  input** is explicit. Archive and reactivate a metric: its charts and history stay. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-04** **Add widget**: pick a metric, chart, project or area filter, daily or category
  grouping, a date range and zone, a size, a target and a prior comparison. **Preview** writes
  nothing. Incompatible choices explain their limits. Add up to twelve widgets; a thirteenth is
  refused. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-05** **Edit layout**: Earlier, Later, Size and Remove work by keyboard. Remove and
  Reset offer Undo; a later layout change withdraws it. Restart: the layout is kept. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-06** The dashboard dates start at the last seven local dates; change them and widget
  overrides stay. The range is ordered and at most 366 dates. A project or area filter reaches
  only the metrics that can take it; the others say so in their caption. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-07** **View data** shows the exact values of each bucket. Contributing records open the
  canonical task, or the Calendar for block and time rows. Change a task, and the table says
  "Data changed; refreshed". Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-08** Complete a task or schedule a block and return to Insights: the cards update in
  place, keeping focus, an open data table and the scroll position. Source:
  [p4-insights](p4-insights.md#dashboard-increment-behavior-and-acceptance-handoff). (?)
- [ ] **INS-09** With the email AI enabled and running, Insights still works and sends nothing to
  Gmail or an AI provider. Source:
  [p4-insights](p4-insights.md#requirement-and-scenario-register). (?)

## Capture and the command palette

- [ ] **CAP-01** Choose **Capture** from any page, press Ctrl+Shift+N (Cmd+Shift+N), and pick
  **Capture task or note** in the palette. A task needs only a title; **Add notes** expands the
  notes. Source: [p2-capture](p2-capture.md#owner-workflow).
- [ ] **CAP-02** Save a task. **Open** goes to it, **Capture another** starts fresh, **Done**
  returns to the page, and **Add to today** puts it in My focus without changing its dates or
  creating it twice. A note may hold a title, text or both. Source:
  [p2-capture](p2-capture.md#owner-workflow), [p2-focus](p2-focus.md#workflow).
- [ ] **CAP-03** Type into Capture and close it: it offers saving or **Discard and close**. An empty
  cancel creates nothing. Quitting the app with an unsaved capture asks first. Source:
  [p2-capture](p2-capture.md#owner-workflow).
- [ ] **CAP-04** Tick **Review later** on a capture: it lands in the review inbox. Tasks and Notes
  have review views and a combined one; **Keep as is** needs no project, date or estimate.
  Unticking it keeps the record at once. Source: [p2-workspace](p2-workspace.md#owner-workflow).
- [ ] **CAP-05** New task (Ctrl/Cmd+N) and New note keep working beside Capture. Source:
  [p2-capture](p2-capture.md#owner-workflow).
- [ ] **CAP-06** Press Ctrl+K with the field empty: the commands list shows Go to each page, New
  task, New event today, New note, Sync and review, Open saved mail, Open data and recovery and
  Open settings. Source: [search](search.md#commands). (?)
- [ ] **CAP-07** Search two or more letters. Each section shows at most 8 matches and says when
  there are more. Opening a task, draft or note, event, Google event or mail result goes to the
  right page with it selected; a task hidden by a filter is revealed. Source:
  [search](search.md#search), [p2-navigation](p2-navigation.md).
- [ ] **CAP-08** Search also finds project names and descriptions and saved brief summaries and
  actions; opening a brief hit rechecks it against the connected account. Source:
  [p2-workspace](p2-workspace.md#owner-workflow).
- [ ] **CAP-09** A word matches letters in either case; a record needs every word, in any order.
  Search sends nothing to Gmail, Google or an AI provider. Source: [search](search.md#search).

## Settings

- [ ] **SET-01** Choose the system's light mode, then dark. Today, Tasks, Calendar, Notes and
  Insights read clearly in both. Settings > Appearance can keep one mode. Source:
  [AGENTS.md](../AGENTS.md), steps ending "light and dark" in the sources above.
- [ ] **SET-02** Working hours, the Day view choice and the Tasks-to-schedule panel survive a
  restart. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 10.

## Data and recovery

Do these in a test profile, after the pages above have records to restore.

- [ ] **DATA-01** Create a backup and a writing export in Data. Source:
  [p1-manual-tasks](p1-manual-tasks.md#acceptance) step 6.
- [ ] **DATA-02** Restore the backup into a test profile: the same tasks, activity, projects, areas,
  tags, series, steps, notes with their links, and Insights layouts return. Organization and
  historical grouping survive. Source: [p1-manual-tasks](p1-manual-tasks.md#acceptance) step 6,
  [p1-projects-life-areas](p1-projects-life-areas.md#owner-acceptance) step 7,
  [p2-notes](p2-notes.md#owner-acceptance) step 7.
- [ ] **DATA-03** The writing export contains `action_activity`, `action_tags`, `action_series`,
  `action_steps` (with `done` and `done_at_utc`), the deadline and `deadline_origin`, and the
  person with `waiting_since_utc`. Source: the P1 sections above.
- [ ] **DATA-04** After restore, work hours, Busy/Free choices, blocks and the panel choice remain.
  Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1) step 10,
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2) step 12.

## Platform checks

Only you can do these; list the ones you have no machine for as skipped.

- [ ] **PLAT-01** Repeat the Calendar steps (CAL-01 to CAL-17) on Windows after the macOS pass, or
  the reverse. Source: [p3-calendar](p3-calendar.md#owner-acceptance-desktop),
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-1),
  [p3-calendar](p3-calendar.md#owner-acceptance-desktop-2).
- [ ] **PLAT-02** On macOS, run the Today, Tasks, Notes and Insights steps once. Source:
  [p3-scheduling-addons](p3-scheduling-addons.md#hands-on-acceptance-still-required),
  [p4-insights](p4-insights.md#requirement-and-scenario-register).
- [ ] **PLAT-03** On a Windows display at 125% and 150%, use the Calendar and the date pickers.
  Source: [p3-scheduling-addons](p3-scheduling-addons.md#hands-on-acceptance-still-required).
- [ ] **PLAT-04** With Narrator or VoiceOver, hear the dates, task choices, warnings and review
  controls, and the Insights cards. Source:
  [p3-scheduling-addons](p3-scheduling-addons.md#hands-on-acceptance-still-required),
  [p4-insights](p4-insights.md#requirement-and-scenario-register).

## Live Gmail and Groq checklists

These need your real accounts and are not rewritten here. Follow each document's own steps:

- [Gmail setup](gmail-setup.md) and [sign-in and Inbox](google-signin-inbox.md)
- [AI analysis](ai-analysis.md)
- [Desktop acceptance (M5)](m5-acceptance.md)
- [Actions and suggestions (M6)](m6-actions.md)
- [Drafts and AI drafting (M7)](m7-drafts.md)
- [Daily operation (M8)](m8-daily-operation.md)
- [Data and recovery with real records (M9)](m9-live-validation.md)

## Questions for the owner

1. **Insights has no step list.** `p4-insights.md` says "the eight owner scenarios in the
   implementation guide remain pending", but no such list is in the repository. INS-01 to INS-09
   are written from the dashboard increment's behaviour text and its scenario table. Do you have
   the eight scenarios, or should these stand?
2. **"Actions" and "Tasks".** The P1 docs say the Actions page; the sidebar now says Tasks. The
   steps use Tasks. Is every control named the same?
3. **Today's layout.** `today.md` and `p2-focus.md` describe **Your day** as three cards above
   the brief; `p2-workspace.md` says the full brief lives in Mail. TODAY-01 and TODAY-05 follow
   both. Which is current?
4. **Palette and sidebar lists.** `search.md` lists Go to for Today, Inbox, Actions, Waiting,
   Drafts, Briefs and Calendar only, and `p2-navigation.md` shows the sidebar without Insights.
   Are Notes, Projects and Insights in the palette (SETUP-01, CAP-06, INS-01)?
5. **Calendar docs were written for three different branches.** P3-1 says the first run applies
   migration 0032 and P3-2 says 0033, and the scheduling add-ons then add 0034 to 0036. SETUP-02
   asks only that every migration applies. Which database are you testing against?
6. **P3 scheduling add-ons** (`p3-scheduling-addons.md`) has a list of what is still to be done by
   hand but no steps for dated hours, buffers, locks or Apply and Undo. CAL-18 is a loose check.
7. **P2 capture, focus and navigation** have no numbered acceptance steps. TODAY-02 to TODAY-04
   and CAP-01 to CAP-05 come from their "workflow" sections.
8. **NOTE-05.** The source says an archived project's note "can't move back once moved out".
   Does that mean it cannot be re-selected from the project menu?
9. **Flat buttons (#108).** Steps that say "choose" a button or a date field were written
   before that change. Anything that now looks different is a finding, not a failure.
