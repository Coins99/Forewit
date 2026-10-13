# Go to or search (Ctrl+K)

Press **Ctrl+K** (**Cmd+K** on macOS) anywhere in the window to open **Go to or search**.
Type, then use Up and Down and press Return to open the highlighted row; Esc closes.

## Commands

With the field empty, every command is listed; typing narrows them to those that contain
each word: **Go to** Today, Actions, Waiting, Drafts, Briefs, Calendar or Inbox; **New task**,
**New event today**, **New note**; **Sync and review**; and **Open saved mail**, **Open data
and recovery** and **Open settings**. A command does what its button does, and does nothing
when that button is unavailable (Sync and review while something runs, for example).

## Search

Two or more letters search your own records on this computer, once typing pauses:

| Section | What is searched | What opening does |
| --- | --- | --- |
| Actions | title, notes and steps | the Actions page (Waiting or Completed when that is where it is), with it selected; a project or area filter that hides it goes back to All tasks |
| Drafts and notes | title and text | the editor |
| Your events | title and notes | its week on the Calendar page, with it selected |
| Google Calendar | event title (the only text MailBrief keeps) | its week on the Calendar page, with it selected |
| Mail | the connected account's saved subject, sender and Gmail's short preview | the Inbox page at that email; an email no longer in the Inbox opens in Gmail |

Every word must appear in a record, in any order. Letters A to Z match in either case;
other letters match as typed. Each section shows its first 8 matches, open actions and the
newest records first, and says when there are more: add a word to narrow it.

Search reads only this computer's database. Nothing is sent to Gmail, Google or an AI
provider, nothing is saved, and the words you type never reach the log, which records only
how many words and matches there were. Email bodies are never stored, so they are never
searched; mail search covers only what the Inbox page and Saved mail already show.

Decisions: search uses bounded `LIKE` queries, not SQLite's full-text index, because a
full-text index adds tables and triggers that backup recovery refuses
([future implementations](future-implementations.md#31-daily-workspace-capture-and-reviews--a01a08)).
