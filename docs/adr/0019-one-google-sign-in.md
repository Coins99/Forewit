# ADR 0019: One Google sign-in for Gmail and Google Calendar

Date: 2026-10-12. Status: accepted. Replaces ADR 0018's separate calendar sign-in.

## Context

ADR 0018 gave Google Calendar its own OAuth grant and vault entry, so that a calendar
credential couldn't read mail. In use, that meant a second Connect, a second consent and a
second Disconnect for the same Google account, and the owner asked for one. Both
credentials live in the same OS vault and are used by the same app, so the isolation
bought little.

## Decision

- **One grant.** Connect Gmail asks Google once for `gmail.readonly`,
  `calendar.events.readonly` and `calendar.calendarlist.readonly`, with
  `include_granted_scopes=true` so asking again only adds permissions. The refresh
  credential stays in Gmail's vault entry, in the same format, so older builds can still
  read it.
- **Gmail required, Calendar optional.** The token exchange still requires only
  `gmail.readonly`. The sign-in remembers what Google granted; with either calendar
  permission missing (unticked on Google's page, or a sign-in made before this ADR), the
  Calendar page offers **Allow Google Calendar**, which opens Google once more
  (`ask_again`) for the same account.
- **One Disconnect.** Calendar's Connect and Disconnect buttons are gone. Disconnect in the
  sidebar forgets the one sign-in and the saved Google events. The old calendar vault
  entry (`MailBrief.GoogleCalendar`) is deleted on the first sign-in of a start; failing to
  delete it never fails a sign-in.
- **Calendar follows Gmail.** After every Sync and review that isn't cancelled, after every
  automatic run and after connecting Gmail, MailBrief refreshes Google Calendar quietly: a
  problem is added to the status line and never fails the brief. A sign-in Google rejects
  during a calendar refresh is reported as Gmail's, since it is the same sign-in.
- **Unchanged from ADR 0018:** calendar access is read-only, requests name their fields,
  and the cache rules, sync window and link rules stay as they were. Phase 4 adds
  `calendar.app.created` to this one grant, asked once.

## Consequences

- One consent for the owner, once; a sign-in from before this ADR needs one Allow.
- One credential can now read mail and calendars. A leaked calendar-only credential could
  no longer read mail; that isolation is gone.
- `providers/gcal/` keeps no sign-in of its own: it uses the Gmail session for tokens.
