# Calendar

The **Calendar** page (sidebar, Ctrl+7) shows one week at a time: your own events, your
open actions' deadlines and target dates, and, once you connect it, your Google Calendar.
Decisions and limits are in [ADR 0018](adr/0018-calendar.md).

## Your own events

**New event** adds a timed or all-day event; **Open** changes it and **Delete event**
hides it, with Undo. Events are kept on this computer, in backups and in **Export all
writing**. Deadlines and target dates come from your open actions and change only there.
**This week** shows the current week from then on, so a new week moves the page on by
itself. While MailBrief is busy with something else, such as a review waiting for you, you
can still move between weeks and open or add events; Save and **Delete event** wait until
it finishes.

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

MailBrief can show your Google calendars beside your own events. It only reads: nothing in
MailBrief can change, move or delete a Google event.

**Set up once**, in the Google Cloud project whose Desktop OAuth client MailBrief uses for
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

**What MailBrief reads and keeps.** For each calendar shown in Google Calendar's own list
(and always your primary one), the events of a ten-week window around the week you're
looking at: titles, times and links. Descriptions, locations, guests and meeting details
are never requested. Calendars shared with you as free/busy only, and working-location
entries, are skipped. The window refreshes when you connect, when you choose **Refresh**,
and when you open the page or move to a week the saved copy doesn't hold or that is more
than 15 minutes old. **Cancel** at the top stops a refresh, or **Allow Google Calendar**
while it waits for your browser; quitting stops them too. What was already read is kept.

**Disconnect** in the sidebar forgets the one sign-in and the saved Google events. It does
not revoke MailBrief's access in your Google account; do that at
myaccount.google.com/permissions if you want to. The saved Google events also go when
Google rejects the sign-in (it expired, or you revoked it), when Calendar access is taken
away, and when you restore a backup, since an old schedule would look current.

MailBrief only ever asks Google for read-only access to Gmail and Calendar. If Google's
answer grants more, for example because this client was once given wider access, the
sign-in is refused and nothing is saved: remove MailBrief at
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
   times and MailBrief's saved copy misses some of the four weeks, or is more than 15
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
5. Disconnect in the sidebar: Gmail disconnects and the Google rows disappear. Connect
   Gmail again: Google asks once for Gmail and both calendar permissions, and the rows
   come back.
