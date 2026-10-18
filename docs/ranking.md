# Ranking: which emails the review suggests

Before anything is read or sent, Forewit scores each Inbox message locally from its
metadata and suggests the best ones in the review, up to your messages per brief. The
score is deterministic: the same mail, choices and settings always give the same order.
Nothing here calls AI, and the review always lets you change the selection.

## The rules

Each rule that applies adds or takes away points, in this order, and shows its reason in
the review (hover a row) and in `forewit-gmail-diagnostic sync --show-metadata`.

| Rule | Points | Reason shown |
| --- | --- | --- |
| Gmail marked it important | +20 | high importance |
| Low importance (Outlook only) | -10 | low importance |
| Unread | +8 | unread |
| Addressed to you (To, any of your addresses) | +8 | direct recipient |
| Has attachments (never set for Gmail: metadata can't tell) | +3 | has attachments |
| Action or deadline wording, such as "due", "deadline", "urgent" | +20 | action or deadline language |
| Approval or reply wording, such as "please review", "can you" | +10 | approval or reply language |
| Received in the last 3 hours | +8 | very recent |
| Received 3 to 8 hours ago | +5 | recent |
| From a no-reply address | -12 | automated sender |
| A later reply in the thread of an open action, not from you ([ADR 0016](adr/0016-follow-up-proposals.md)) | +20 | reply in a thread you track |
| Starred in Gmail | +15 | starred in Gmail |
| In Gmail's Promotions tab | -20 | in Gmail's Promotions tab |
| In Gmail's Social tab | -15 | in Gmail's Social tab |
| In Gmail's Forums tab | -8 | in Gmail's Forums tab |
| In Gmail's Updates tab | -5 | in Gmail's Updates tab |
| Mailing list or bulk mail, when no tab rule applied | -8 | mailing list or bulk mail |
| From one of your important senders | +30 | a sender you marked important |
| From a sender whose mail you've wanted | +10 | you've wanted this sender's mail before |
| From a sender whose mail you keep leaving out | -10 | you've left this sender's mail out before |

In the Promotions and Social tabs the two wording rules don't apply: marketing is written to
sound urgent. Mail from an important sender never gets the last two rules.

Messages scoring 10 or more qualify. The review suggests the highest scores, newest first
on a tie; when fewer than three qualify, it fills up to three with the next best. The day's
brief keeps its messages ahead of these, an email you left out of a review isn't suggested
again, and mail from an excluded sender never is ([ADR 0017](adr/0017-automatic-runs.md)).

## Gmail's tabs, stars and bulk mail

Forewit reads Gmail's category label, its star and three headers with the rest of the
metadata, still read-only. A message is bulk mail when it has a `List-Id` or
`List-Unsubscribe` header, or a `Precedence` of `bulk`, `list` or `junk`. Only whether the
header is there is kept: unsubscribe links are never stored or shown. Mail cached before this
version has no tab or star until Gmail returns it again, which the next sync does.

## Important senders

In **Settings > Preferences**, **Important senders** takes one address or `@domain` per line,
like **Excluded senders**; `@domain` also covers its subdomains. Their mail gets +30, so it
is among the first suggestions. A sender can't be in both lists, and when an excluded domain
covers an important address, the exclusion wins: excluded mail is never read or analyzed.
`forewit-gmail-diagnostic preferences show` lists them.

## What your choices teach

For each sender, Forewit counts what you did in the last 90 days:

- **Wanted**: emails you checked in a review although they weren't suggested (or named with
  `--include`), emails you marked **Should have been in my brief** on the Inbox page, and the
  open or completed actions that came from their email.
- **Left out**: emails you unchecked in a review after they were suggested.
- **Kept**: their emails in the briefs saved in that time.

A sender with at least one wanted email, and at least as many wanted as left out, ranks +10.
One with two or more left out, and none wanted or kept, ranks -10. Your own addresses never
count. Only senders' addresses are used, never subjects or previews, and nothing leaves your
computer. Clearing cached mail in **Data** forgets what that mail taught; actions keep
theirs.

## Should have been in my brief

On the **Inbox** page, open an email and choose **Should have been in my brief** when the
review should have suggested it. Its row gets a flag, the open email a **Wanted in brief**
note, its sender counts as wanted, and **Remove mark** takes it back. Gmail is never told.
The flag in the page's footer counts the emails you wanted in the last 14 days that the
review didn't suggest, whether you picked them in a review or marked them here: the
"important mail missed" column of the
[personal-use journal](m9-live-validation.md#personal-use-journal-714-days).

Decisions are in [ADR 0020](adr/0020-ranking-signals.md).
