# Today: your day at a glance

**My focus** now leads Today: choose ordinary open tasks for this date, order them,
complete/reopen them with Undo and see known estimates, unknown estimates and
waiting follow-ups separately. **Plan today** opens the full chooser. Selection
changes no task dates, priority or lifecycle facts. It persists offline across
restart and recovery; see [daily focus](p2-focus.md) for behavior and verification.
The agenda preview and **Other commitments needing attention** follow focus, with
selected tasks excluded from the other-commitments list. The saved brief remains
below the planner for this increment.

Choose **Your day** on the **Today** page to expand three cards above the brief. They come from
what is already on this computer, so they show with Gmail disconnected, with no AI set up
and offline. The cards themselves are read-only projections.

- **Needs attention**: your open actions, yours and the ones you wait for, that are
  - **overdue**: a dated deadline has passed ("Overdue · Mon Oct 12");
  - **due today**: the deadline falls today ("Due 17:00", or "Due today" for a date);
  - **planned for today**: the target date is today ("Today");
  - **planned earlier**: the target date has passed and the action is still open
    ("Planned Fri Oct 9").

  Each action shows once, under its most urgent reason, then in the Actions page's order.
  "waiting" marks one someone else does. Overdue and due-today notes are in the warning
  colour.
- **Today's agenda**: your events and your Google Calendar's for today, all-day ones first,
  then by start time, with a Google event's calendar at the right. Actions' deadlines and
  target dates are under Needs attention, not here.
- **New replies**: open actions whose threads have messages from others since you last
  marked them seen, latest first, with how many and who wrote last.

Each card's heading counts its rows. Up to three complete rows show and the rest scroll
inside that card. The expanded section fits its content; shorter windows show one or two
rows to leave room for the brief, and empty cards stay compact. Double-click a
row, or select it and press Return or Enter, to open it: an action on the Actions page
(Waiting when someone else does it), or an event in this week of the Calendar page. When a
project or area filter on Actions hides the action, the filter goes back to All tasks first.

The cards follow every change: an action accepted, edited, completed or reopened, an event
added or moved, a sync that brings new replies, your time zone, and midnight. Opening an
older brief from the Briefs page leaves the cards on today.

## For developers

`services/today.py` builds a `DaySummary` (`domain/today.py`) from the open and waiting
actions, `DayPlanService.load` and `EventService.list_range` for the day. The summary
is a read model; focus commands write the independent day-plan tables described in
[ADR 0026](adr/0026-durable-daily-focus.md). `ui/today_view.py` paints it (`DayPanel`, with
`DayRowDelegate` drawing each row with `drawText`), and `MainWindow._refresh_day` reads it
after the actions or the calendar are read.

This is the first slice of the planned Today (A01, A02 and A05 in the
[feature inventory](future-feature-inventory.md)). Moving the brief to a Mail page,
historical day browsing, carry/move and progress cards come later
([future implementations](future-implementations.md#61-navigation-and-migration-of-current-screens)).
