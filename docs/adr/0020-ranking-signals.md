# ADR 0020: Ranking signals from Gmail and from the owner's own choices

Date: 2026-10-13. Status: proposed.

Builds on ADR 0007 (Gmail first), ADR 0014 (owner preferences) and ADR 0017 (remembered
declines).

## Context

A brief holds at most ten emails, so the local ranking decides what the owner ever sees
analyzed: every promotion that is suggested pushes out real mail. The ranking used only
Gmail's IMPORTANT label, unread state, direct addressing, recency, a `noreply` test and
keyword rules. Gmail already sorts mail into Primary, Promotions, Social, Updates and
Forums, and the owner already tells MailBrief a lot: the messages they leave out of a review,
the ones they pick that weren't suggested, and the emails their actions come from. None of
that reached the ranking, and marketing mail that says "confirm" or "due" was rewarded for
it. The M9 journal's first column, important mail missed, had no number behind it.

## Decision

- **Gmail's signals.** The metadata read keeps three more facts about each message, still
  with `format=metadata` under `gmail.readonly`:
  - its category label (Primary, Social, Promotions, Updates, Forums; none when Gmail gives
    none);
  - whether it is starred;
  - whether it is bulk mail: a non-empty `List-Id` or `List-Unsubscribe` header, or a
    `Precedence` of `bulk`, `list` or `junk`. Only that yes or no is kept, never a header's
    value, so unsubscribe links and their tokens are never stored or shown.
- **Rules.** Applied after the existing ones, so earlier reasons keep their order:
  - starred +15;
  - Promotions -20, Social -15, Forums -8, Updates -5, and Primary or unknown 0;
  - bulk mail -8, only when no category rule applied;
  - in Promotions and Social the action/deadline (+20) and approval/reply (+10) wording
    rules don't apply, because marketing is written to sound urgent. In Updates and Forums
    they still do: bills that are due and reviews someone asks for live there.
- **Important senders.** A preference beside the excluded senders, with the same rule forms
  (an address, or `@domain` covering its subdomains), the same normalization and the same
  limit of 200. Their mail gets +30, which in practice puts it among the first suggestions.
  A rule can't be in both lists; when an excluded domain covers an important address, the
  exclusion wins, so ADR 0014's guarantee that excluded mail is never selected, downloaded
  or analyzed is unchanged. The desktop edits it in Settings > Preferences, and
  `preferences show` prints it. Logs carry only the count.
- **What the owner's choices teach.** Each run looks at the last 90 days of the connected
  account, by sender address:
  - *wanted*: messages the owner picked in a review although the automatic selection
    hadn't suggested them (or named with `--include`), messages marked **Should have been
    in my brief** on the Inbox page, and live actions with an email from the sender;
  - *left out*: messages the owner unchecked after they were suggested (ADR 0017's
    declines);
  - *kept*: messages in the briefs saved in that time.

  A sender with at least one wanted message, and at least as many wanted as left out, gets
  +10 ("you've wanted this sender's mail before"). One with two or more left out, none
  wanted and none kept gets -10. An important sender gets neither, and the owner's own
  addresses never count. Only addresses are read for this, never subjects or previews.
- **Marks.** A message carries at most one review mark: wanting it forgets its decline,
  and declining it forgets that it was wanted. A mark keeps the time it was first set. The
  Inbox page's mark is local, can be removed, and sends nothing to Gmail. The page shows how
  many messages were wanted in the last 14 days without being suggested, for the owner's
  journal.
- **What doesn't change.** Ranking stays deterministic, local and explained: every rule
  that applies shows its reason in the review and in `sync --show-metadata`. Nothing here
  calls AI, sends anything, changes the mailbox or overrides the owner's review, limit,
  exclusions or declines. A wanted mark never raises its own message: a message picked by
  hand is already carried through the day (ADR 0017), and a second bonus would reorder the
  day's brief.

## Consequences

- Migration `20261013_0018` adds the message columns and the preference column; existing
  rows get no category, no star, not bulk, no mark and no important senders. A message's
  new fields fill in the next time Gmail returns it, which the full Inbox scan and each day's
  sync do.
- Learned signals live only in the cache. Clearing cached mail (Data > cleanup) forgets
  the marks and declines it held; actions keep their source snapshots, so what they teach
  survives.
- A sender's address can be forged. A forged important sender only moves a message up the
  review; the owner still chooses what is read and approves what is sent.
- Bulk-mail detection also catches useful lists, such as GitHub notifications; -8 is a
  nudge, and wording or a learned +10 can outweigh it.
- See [Ranking](../ranking.md) for every rule and its weight.
