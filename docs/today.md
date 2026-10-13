# Today: your day at a glance

The **Today** page opens with **Your day**, three cards above the brief. They come from
what is already on this computer, so they show with Gmail disconnected, with no AI set up
and offline. Nothing in them is sent anywhere or written.

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

Each card's heading counts its rows. Three rows show and the rest scroll. Double-click a
row, or select it and press Return or Enter, to open it: an action on the Actions page
(Waiting when someone else does it), or an event in this week of the Calendar page. When a
project or area filter on Actions hides the action, the filter goes back to All tasks first.

The cards follow every change: an action accepted, edited, completed or reopened, an event
added or moved, a sync that brings new replies, your time zone, and midnight. Opening an
older brief from the Briefs page leaves the cards on today.

## For developers

`services/today.py` builds a `DaySummary` (`domain/today.py`) from the open and waiting
actions and `EventService.list_range` for the day. It is a read model only: no table, no
copy of any record, nothing written. `ui/today_view.py` paints it (`DayPanel`, with
`DayRowDelegate` drawing each row with `drawText`), and `MainWindow._refresh_day` reads it
after the actions or the calendar are read.

This is the first slice of the planned Today (A01, A02 and A05 in the
[feature inventory](future-feature-inventory.md)). Moving the brief to a Mail page,
day plans with chosen priorities, capture and progress cards come later
([future implementations](future-implementations.md#61-navigation-and-migration-of-current-screens)).
