# ADR 0018: A calendar, with MailBrief's own calendar in Google

Date: 2026-10-11. Status: accepted.

Builds on ADR 0005 (SQLite local storage), ADR 0007 (Gmail first), ADR 0011 (actions),
ADR 0013 (AI drafting) and ADR 0016 (follow-up proposals).

## Context

The owner wants to see their Google Calendar in MailBrief and have the AI change it.
MailBrief is read-only toward Google today (`gmail.readonly`), treats email and AI output as
untrusted, and lets AI change the owner's data only through previewed proposals that the
owner applies. Emails and calendar invitations come from other people, so either can carry
instructions aimed at the AI ("delete everything on Friday"). Google's Calendar API has
granular scopes, including one that limits writes to calendars the app itself creates. The
ecosystem roadmap puts an internal calendar after tasks, keeps deadlines, target dates and
scheduled time distinct, and asks for external adapters to be read-only first.

## Decision

- **Where changes land.** The owner's events live in MailBrief, owned by the owner and
  revisioned like actions. In Google, MailBrief writes only to a secondary "MailBrief"
  calendar that it creates (`calendar.app.created`). Every other calendar is read-only
  (`calendar.events.readonly`, `calendar.calendarlist.readonly`). MailBrief never
  creates, edits, moves or deletes an event outside the MailBrief calendar, never adds
  guests and never sends invitations. The scope enforces the calendar boundary, not who
  made an event: events the owner adds to the MailBrief calendar in Google are still the
  owner's, and phase 4 decides how edits there and in MailBrief reconcile.
- **A separate sign-in.** Calendar access is its own OAuth grant with its own entry in the
  OS credential vault. The Gmail grant stays exactly `gmail.readonly`: the calendar
  credential cannot read mail and the mail credential cannot touch the calendar.
- **AI proposes, the owner applies.** The AI never changes the calendar by itself. Its
  changes are proposals that Python validates (each names an event MailBrief may change,
  with valid times, and never moves an action's deadline or target date), that the owner
  previews, and that apply as one revision with Undo. What is sent needs its own owner
  consent, scoped "calendar" like drafting's.
- **Deadlines stay distinct.** Action deadlines and target dates are markers computed from
  the open actions. They are never stored as events, and changing an event never changes
  them.
- **What is stored.** Local events: title, notes, timing and the zone they were entered in.
  Google events (phase 2): title, times, location and calendar only; descriptions and
  guest lists are never stored.
- **Time.** A timed event keeps two UTC instants and its IANA zone; an all-day event keeps
  its first and last local days, inclusive, and stays on those days in every zone. An event
  lasts at most 31 days. A range shows the events touching its local days in the owner's
  zone, at most 42 days at a time.
- **Phases, one PR each.**
  1. A local Calendar page: the owner's events with Undo, and action markers. No network
     and no AI. (This decision's first PR.)
  2. Google, read-only: the Calendar sign-in, a synced rolling window, a view-only layer.
  3. AI changes from the owner's instructions, applied to MailBrief events.
  4. The MailBrief calendar in Google: create it, copy applied changes into it, and handle
     edits the owner makes there.
  5. Optional: "Add to calendar" suggestions from email analysis.

## Consequences

- The owner sees their real schedule in MailBrief while Google itself limits what MailBrief
  can change.
- Google asks for consent once more, for the Calendar grant, with the same unverified-app
  warning as Gmail's.
- Each phase brings its own additive migration; phase 1's table has no Google columns.
- Phase 1 is desktop-only: the diagnostic CLI has no calendar commands yet. Exports include
  events; the "deleted" retention cleanup does not yet remove deleted events.
- The Calendar page starts as a week agenda. A time grid with drag and resize can follow.

## Phase 2 decisions (2026-10-12): reading Google Calendar

Phase 2 ships in two PRs: 2a adds the sign-in, the sync and the cache; 2b shows them on the
Calendar page.

- **The grant.** `calendar.events.readonly` and `calendar.calendarlist.readonly` only, from
  the same Desktop OAuth client as Gmail but as a separate grant in its own vault entry
  (`MailBrief.GoogleCalendar`). Google lets the owner untick one permission; without both,
  nothing is saved. The account is the ID of its primary calendar, so no profile scope is
  needed, and it need not be the Gmail account. Phase 4 adds `calendar.app.created` then.
- **What is read.** Every request names its `fields`, so descriptions, guests and conference
  details never leave Google, and neither do locations or free/busy flags, which the page
  doesn't show. Repeating events are expanded to instances; working-location entries and
  calendars shared as free/busy only are skipped. Titles and calendar names are untrusted:
  control and format characters go and lengths are capped. Only `https` links on
  `www.google.com/calendar` or `calendar.google.com` are kept (never Google's `/url`
  redirector), and a link that can't be used drops only the link, not its event.
- **The cache.** Google's data, kept like cached mail: two tables holding only the
  calendars shown in Google Calendar (one the owner unticks goes with its events), replaced
  per calendar only by a complete read of it, excluded from exports and from the cleanup
  fingerprint (every sync rewrites it), and removed when the owner disconnects Google
  Calendar, since an out-of-date schedule misleads.
- **When it syncs.** A window of ten weeks around the week shown (the week before through
  eight weeks after), stale after 15 minutes. At most 20 calendars, the primary first, then
  the shown ones, read three at a time. Sign-in and rate-limit errors stop a sync; any other
  failure, a calendar Google refuses included, is that calendar's alone: it keeps its
  events but counts as checked for the window, as do the calendars a rate limit left
  unread, so a calendar that keeps failing is tried again when the window goes stale, not
  on every look. A calendar list without exactly one primary calendar is refused before
  the cache changes. The calendars shown are those Google Calendar's own list shows, and
  the primary one always.
- **Isolation.** A calendar sign-in failure never changes the Gmail connection.
