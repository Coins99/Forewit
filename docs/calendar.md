# Calendar

The **Calendar** page (sidebar, Ctrl+6) shows one week at a time: your own events, your
open actions' deadlines and target dates, and, once you connect it, your Google Calendar.
Decisions and limits are in [ADR 0018](adr/0018-calendar.md).

## Your own events

**New event** adds a timed or all-day event; **Open** changes it and **Delete event**
hides it, with Undo. Events are kept on this computer, in backups and in **Export all
writing**. Deadlines and target dates come from your open actions and change only there.

## Google Calendar, read-only

MailBrief can show your Google calendars beside your own events. It only reads: nothing in
MailBrief can change, move or delete a Google event.

**Set up once**, in the Google Cloud project whose Desktop OAuth client MailBrief uses for
Gmail:

1. In **APIs & Services > Library**, enable the **Google Calendar API**.
2. On the OAuth consent screen's **Data Access** page, add
   `https://www.googleapis.com/auth/calendar.events.readonly` and
   `https://www.googleapis.com/auth/calendar.calendarlist.readonly`.
3. On the Calendar page, choose **Connect Google Calendar**. Google asks in your browser,
   with the same unverified-app warning as Gmail; allow both permissions.

This is a separate sign-in from Gmail's, kept in its own entry in the OS credential vault.
It can't read mail, and the Gmail sign-in can't read calendars. Disconnecting one never
disconnects the other.

**What MailBrief reads and keeps.** For each calendar shown in Google Calendar's own list
(and always your primary one), the events of a ten-week window around the week you're
looking at: titles, times, locations and links. Descriptions, guests and meeting details
are never requested. Calendars shared with you as free/busy only, and working-location
entries, are skipped. The window refreshes when you connect, when you choose **Refresh**,
and when you open the page or move to a week the saved copy doesn't hold or that is more
than 15 minutes old.

**Disconnect Google Calendar** forgets the sign-in and the saved Google events. It does not
revoke MailBrief's access in your Google account; do that at
myaccount.google.com/permissions if you want to.

**Open in Google Calendar** opens the selected Google event in your browser.

## Messages

- "Google Calendar needs you to connect it again": the sign-in expired or was revoked;
  choose **Connect Google Calendar**.
- "Google Calendar API is disabled…": enable the API (step 1 above).
- "Google didn't grant both calendar permissions…": reconnect and allow both.
- "Couldn't reach Google Calendar…": the page shows the copy saved at the last refresh.

## Live check

1. Connect, then find a repeating event and an all-day event in the right days.
2. Choose Refresh; move nine weeks ahead and watch that week load.
3. Disconnect: the Google rows disappear, and Gmail stays connected.
