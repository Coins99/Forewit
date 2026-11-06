# Calendar

The **Calendar** page (sidebar, Ctrl+7) shows your own events, the time you set aside for
tasks, your open actions' deadlines and target dates, and, once you connect it, your Google
Calendar. Decisions and limits are in [ADR 0018](adr/0018-calendar.md), for time set
aside for tasks [ADR 0028](adr/0028-work-blocks.md), and for working hours, Busy/Free and
the tasks to schedule [ADR 0029](adr/0029-availability.md).

## Day, Week, Month and List

Choose how the page shows your days with the tabs under its title; Forewit remembers
your choice on this computer.

- **Day**: one day of hours, with room for each entry. ‹ and ›, or Left and Right, move a
  day; **Today** comes back.

- **Week**: a week of hours, as a calendar app shows it. Timed events are blocks in their
  hours, side by side when they overlap; all-day events, events of a day or more and your
  actions' dates run across the top. Today's column is shaded, with a line at the time now.
- **Month**: six weeks of days around a month. Events of several days run across them as
  one bar; timed events take a line each ("09:00 Dentist"). When a day holds more than its
  square shows, **+N more** opens that week in the List view (the view you keep stays as
  you chose it).
- **List**: a week, day by day, as rows.

**Previous** and **Next** move by a week, or by a month in the Month view, and **This week**
or **This month** goes back to now. Click an event to select it and double-click it to
open it (a Google event opens in Google Calendar); double-click an empty day or hour to add
an event that day. In the grids, the arrow keys move between days, and Up and Down move
through a day's events in the Week view (Alt+Up and Alt+Down, or Option on a Mac, in the
Month view, including those behind **+N more**); Return opens the selected event, and in
the Month view Page Up and Page Down change the month.

## Your own events

**New event** adds a timed or all-day event; **Open** changes it and **Delete event**
hides it, with Undo. Events are kept on this computer, in backups and in **Export all
writing**. Deadlines and target dates come from your open actions and change only there.
**This week** (or **This month**) shows the current week (or month) from then on, so a new
week moves the page on by itself. While Forewit is busy with something else, such as a
review waiting for you, you can still move between weeks and months and open or add
events; Save and **Delete event** wait until it finishes.

Pick a time from the list (every 15 minutes) or type one as `HH:mm`. Up and Down move it to
the next quarter hour, later or earlier, as they move a date; Page Up and Page Down (Fn+Up
and Fn+Down on a Mac laptop) move it an hour. The end's list says how long the event
lasts, and runs past midnight into the next day. Moving the start moves the end with it.
An end at or before the start, or more than 31 days after it, isn't taken: the editor
puts the time back and says why. If that happens as you press Return or Save, nothing is
saved yet: check the times, then save again. Scrolling changes a date or time only while
you're in that field (click or Tab into it first), so scrolling across the editor never
moves an event.

## Google Calendar, read-only

Forewit can show your Google calendars beside your own events. It only reads: nothing in
Forewit can change, move or delete a Google event.

**Set up once**, in the Google Cloud project whose Desktop OAuth client Forewit uses for
Gmail:

1. In **APIs & Services > Library**, enable the **Google Calendar API**.
2. On the OAuth consent screen's **Data Access** page, add
   `https://www.googleapis.com/auth/calendar.events.readonly` and
   `https://www.googleapis.com/auth/calendar.calendarlist.readonly`.
3. Connect Gmail. Google asks once, in your browser, for Gmail and the two calendar
   permissions; tick them all. If you connected Gmail before this, or left the calendar
   boxes unticked, the Calendar page offers **Allow Google Calendar**: choose it once.

There is one Google sign-in for Gmail and Calendar (ADR 0019), kept in Gmail's entry in the
OS credential vault. Google Calendar refreshes after every Sync and review and every
automatic run, as well as from the Calendar page.

**What Forewit reads and keeps.** For each calendar shown in Google Calendar's own list
(and always your primary one), the events of a ten-week window around the week you're
looking at: titles, times and links. Descriptions, locations, guests and meeting details
are never requested. Calendars shared with you as free/busy only, and working-location
entries, are skipped. The window refreshes when you connect, when you choose **Refresh**,
and when you open the page or move to a week the saved copy doesn't hold or that is more
than 15 minutes old. **Cancel** at the top stops a refresh, or **Allow Google Calendar**
while it waits for your browser; quitting stops them too. What was already read is kept.

**Disconnect** (in **Settings**, under Account) forgets the one sign-in and the saved
Google events. It does
not revoke Forewit's access in your Google account; do that at
myaccount.google.com/permissions if you want to. The saved Google events also go when
Google rejects the sign-in (it expired, or you revoked it), when Calendar access is taken
away, and when you restore a backup, since an old schedule would look current.

Forewit only ever asks Google for read-only access to Gmail and Calendar. If Google's
answer grants more, for example because this client was once given wider access, the
sign-in is refused and nothing is saved: remove Forewit at
myaccount.google.com/permissions, then connect Gmail again.

**Open in Google Calendar** opens the selected Google event in your browser.

## Plan with AI

**Plan with AI** on the Calendar page asks Groq to change your own events, for example "move
the dentist to Friday at 10" or "block two hours tomorrow morning for the deck".

1. Type what should change. Your events in the four weeks from the Monday shown are always
   sent, as titles and times; notes never are. You can also send Google Calendar times
   (without titles, or with them) and your open actions' deadlines and target dates. Your
   instruction stays until changes from it are applied, so after Discard you can ask again.
2. **Preview what is sent** lists each part and its size. When you send Google Calendar
   times and Forewit's saved copy misses some of the four weeks, or is more than 15
   minutes old, it reads Google first; the preview says when the times cover only some of
   the days. The first time, tick the consent box; then choose **Ask Groq**. Cancel request
   stops it, and so does closing the dialog (Close or Esc). If something goes wrong, the
   dialog says so at the top.
3. Groq's changes come back checked: each says what it does, why, and which events it would
   overlap. Deletions come unticked: tick the ones you want. The overlaps follow what is
   ticked. Untick any change you don't want and choose **Apply ticked changes**.
4. **Undo AI changes** reverses the whole set, as long as none of those events has changed
   since.

Groq can only add, change or delete your own events, at most 10 per request; it can't touch
Google events, actions, deadlines or target dates. An end time Groq gives without a date is
the first such time after the start: later that day, or the next morning for an overnight
event of at most 12 hours. If your calendar changed while you were
reviewing, nothing is applied: discard and ask again. Revoking AI consent in Settings covers
this too.

## Busy or free, and working hours

- An event is **Busy** (it takes its time) or **Free** (it's only shown). Unless you choose
  in the editor's **Show as**, a timed event is busy and an all-day one free. A free event
  shows as an outline. An all-day Busy event is a day off.
- Google events follow Google's own **Show as**; Forewit reads it with the event and
  nothing else new.
- **Working hours** (under the calendar) sets your hours for each weekday as text, such as
  "09:00–12:00, 13:00–17:00", up to three a day on quarter hours, and a buffer kept clear
  before and after busy events. Until you set them, nothing changes. Once set, the Day and
  Week views shade the hours outside them, and a line under the hours says how much
  working time is still free from now on: working hours less busy events and their
  buffers, less the time set aside for open tasks. Time that has passed is never free.
- A block outside your working hours says so when you save it. Overlap warnings count busy
  events only, and a day off overlaps every block on it.

## Tasks to schedule

**Tasks to schedule** (under the calendar, in the Day and Week views) shows your open
tasks that still need time beside the hours: those with more estimate than time set aside
from today on, and those with no estimate and no time set aside from today on. Waiting
tasks aren't listed. They're in the order of the Tasks page, at most 50.

- Select one and press Return: the block editor opens at the next free stretch of working
  time, for what is left of its estimate (up to 2 hours; 1 hour without one).
- Or drag it onto the hours: it is scheduled there at once, with **Undo schedule**.

## Time for tasks

Set aside time on the calendar for a task: a **block**, from a start to an end, as many as
you like for one task. A block is your plan, never a record of work done, and it never
changes the task: its title, dates, deadline and history stay as they are, and moving a
deadline or target date never moves a block.

- **Schedule task**, beside New event, asks which open task, then when. **Schedule time…**
  in a task's detail does the same for that task. A new block starts at the next whole hour
  today (09:00 on another day) and lasts what is left of the task's estimate after its time
  from today on (earlier blocks aren't work done), rounded up to 15 minutes and at most 2
  hours, or 1 hour without an estimate.
- Blocks show in the Week, Month and List views and in Today's agenda, with "Task" beside
  their time: a white card with a blue rim, or grey once the task is done. The task's
  detail says how much time is scheduled ("1 hr 30 mins in 2 blocks · estimate 2 hrs") and
  lists the next three blocks.
- In the Week view, drag a block to move it, in steps of 15 minutes and across days, or
  drag its bottom edge to change its length; Escape stops a drag. With a block selected,
  Ctrl+Up and Ctrl+Down (Cmd on a Mac) move it 15 minutes, Ctrl+Left and Ctrl+Right a day,
  Ctrl+Shift+Up and Ctrl+Shift+Down change its length, and Delete or Backspace unschedules
  it. Double-click a block, or press Return, to open it: change its time, **Unschedule** it
  or **Open task**.
- Every change can be undone with **Undo**. Several moves of one block in a row undo
  together, back to where it started.
- A block lasts 5 minutes to 12 hours, and a task holds at most 50. Only an open task's
  blocks move: a done task keeps its blocks, greyed, until you unschedule them or reopen
  it, and trying to move one says so. The task editor shows the scheduled time beside the
  estimate; blocks change on the Calendar, not there.
- Forewit says when a block overlaps a busy event, a Google event or another block, when it
  ends after the task's deadline, and when it is outside your working hours. These are
  warnings: the block is still saved.
- Deleting a task hides its blocks, and restoring it brings them back. Blocks are kept in
  backups and in **Export all writing**. Plan with AI never sees them; it only warns when a
  change it proposes overlaps one.

## Messages

- "Gmail: session expired…": the one Google sign-in expired or was revoked; connect Gmail
  again.
- "Google Calendar isn't allowed yet…": choose **Allow Google Calendar** and tick both
  calendar boxes.
- "Google Calendar API is disabled…": enable the API (step 1 above).
- "Google didn't grant calendar access…": choose **Allow Google Calendar** and tick both
  calendar boxes.
- "Couldn't reach Google Calendar…": the page shows the copy saved at the last refresh.

## Live check

1. With a Gmail sign-in from before ADR 0019, open the Calendar page: it says Google
   Calendar isn't allowed yet. Choose **Allow Google Calendar**, then Cancel on Google's
   page: Gmail stays connected and Allow is still offered.
2. Choose **Allow Google Calendar** again, with the same Google account, and tick both
   calendar boxes. Find a repeating event and an all-day event in the right days.
3. Choose Refresh; move nine weeks ahead and watch that week load.
4. Run Sync and review: the line under the Calendar heading shows a new update time.
5. Disconnect in Settings (Account): Gmail disconnects and the Google rows disappear.
   Sign in with Google again: Google asks once for Gmail and both calendar permissions, and the rows
   come back.
