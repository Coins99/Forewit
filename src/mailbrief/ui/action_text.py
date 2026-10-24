"""What the Actions page says about an action, worked out once for its list text, its painted
row and its detail pane, so they can't disagree."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from mailbrief.domain.actions import (
    PERSON_MAX_CHARS,
    TARGET_REASON_TEXT,
    Action,
    ActionPriority,
    ActionProposal,
    ActionStatus,
    clean_person,
)
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.recurrence import (
    ALL_WEEKDAYS,
    WORKING_WEEKDAYS,
    RepeatFrequency,
    RepeatRule,
)
from mailbrief.ui.brief_list import with_address
from mailbrief.ui.deadline_text import (
    FULL_WEEKDAYS,
    WEEKDAYS,
    day_text,
    deadline_text,
    length_text,
    weekday,
)
from mailbrief.ui.proposals_view import pending_proposals
from mailbrief.ui.who_text import who_text


@dataclass(frozen=True)
class ActionDetails:
    """An action's dates, progress, state and thread activity, as of ``now``.

    ``waiting``, ``overdue`` and ``carried_over`` hold only for an open action;
    ``target_reason`` is set only while the target is still the suggested one. A waiting
    action's target date is its follow-up date (B08).
    """

    target: date | None
    target_reason: str | None
    due: str | None
    due_by_owner: bool  # The owner set the deadline (B06), rather than an email.
    estimate: str | None
    steps_done: int
    steps: int
    waiting: bool
    overdue: bool
    carried_over: bool
    source_gone: bool
    new_messages: int
    activity: str | None
    replied: str | None
    proposals: tuple[ActionProposal, ...]
    completed: date | None
    repeat: str | None  # "Repeats every Mon and Wed", or "Repeats stopped".
    previous: str | None  # The occurrence it follows: "Follows Wed Oct 14 · Skipped".
    today: date  # The owner's day, which decides whether a date names its year.
    waiting_since: date | None = None  # The day it started waiting, in the owner's zone.
    followed_up_on: date | None = None  # followed_up_at's day, in the owner's zone (B08).


def followed_up_at(action: Action) -> datetime | None:
    """The latest message in the action's threads that the owner sent or marked seen (B08):
    once one comes after a follow-up fell due, the owner has followed up or had an answer."""
    thread = action.thread
    sent = None if thread is None else thread.owner_replied_at_utc
    return max((at for at in (action.thread_seen_until_utc, sent) if at is not None), default=None)


def action_details(action: Action, *, today: date, zone: ZoneInfo, now: datetime) -> ActionDetails:
    """The details of ``action``; dates and times read in ``zone``, the owner's."""
    is_open = action.status is ActionStatus.OPEN
    reason = action.target_reason
    keeps_reason = (
        reason is not None
        and action.target_date is not None
        and action.target_date == action.suggested_target_date
    )
    activity = replied = None
    new_messages = 0
    thread = action.thread
    if thread is not None:
        new_messages = thread.new_messages
        if thread.latest_at_utc is not None and thread.latest_sender is not None:
            latest = _when(thread.latest_at_utc, today=today, zone=zone, clock=True)
            activity = (
                f"{thread.new_messages} new in thread, latest {latest} from {thread.latest_sender}"
            )
        if thread.owner_replied_at_utc is not None:
            day = _when(thread.owner_replied_at_utc, today=today, zone=zone, clock=False)
            replied = f"you replied {day}"
    completed = action.completed_at_utc
    followed = followed_up_at(action)
    repeat = previous = None
    if action.repeat is not None:
        repeat = (
            "Repeats stopped" if action.repeat.ended else repeat_text(action.repeat.rule, today)
        )
        if action.repeat.previous_date is not None and action.repeat.previous_status is not None:
            previous = (
                f"Follows {day_text(action.repeat.previous_date, today=today)} · "
                + _CLOSED[action.repeat.previous_status]
            )
    return ActionDetails(
        target=action.target_date,
        target_reason=TARGET_REASON_TEXT[reason] if keeps_reason and reason is not None else None,
        due=deadline_text(action, zone, today=today),
        due_by_owner=action.owner_deadline,
        estimate=estimate_text(action.estimate_minutes),
        steps_done=sum(step.done for step in action.steps),
        steps=len(action.steps),
        waiting=is_open and action.ownership is ActionOwnership.WAITING_FOR,
        overdue=is_open and action.is_overdue(now),
        carried_over=is_open and action.carried_over(today, zone),
        source_gone=bool(action.sources) and not any(source.available for source in action.sources),
        new_messages=new_messages,
        activity=activity,
        replied=replied,
        proposals=pending_proposals(action),
        completed=None if completed is None else completed.astimezone(zone).date(),
        repeat=repeat,
        previous=previous,
        today=today,
        waiting_since=(
            None
            if action.waiting_since_utc is None
            else action.waiting_since_utc.astimezone(zone).date()
        ),
        followed_up_on=None if followed is None else followed.astimezone(zone).date(),
    )


_CLOSED: dict[ActionStatus, str] = {
    ActionStatus.OPEN: "Open",
    ActionStatus.COMPLETED: "Done",
    ActionStatus.SKIPPED: "Skipped",
}


def _ordinal(number: int) -> str:
    suffix = "th" if 11 <= number % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10)
    return f"{number}{suffix or 'th'}"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def rule_text(rule: RepeatRule) -> str:
    """How a rule repeats, without its end: "every day", "every Mon and Wed", "every
    weekday", "on the 31st of each month (not in shorter months)"."""
    match rule.frequency:
        case RepeatFrequency.DAILY:
            return "every day"
        case RepeatFrequency.WEEKLY:
            if rule.weekdays == ALL_WEEKDAYS:
                return "every day"
            if rule.weekdays == WORKING_WEEKDAYS:
                return "every weekday"
            if len(rule.weekdays) == 1:
                return "every " + FULL_WEEKDAYS[rule.weekdays[0]]
            return "every " + _join([WEEKDAYS[day] for day in rule.weekdays])
        case RepeatFrequency.MONTHLY_DAY:
            day = rule.month_day or 1
            text = f"on the {_ordinal(day)} of each month"
            return text + " (not in shorter months)" if day > 28 else text
        case RepeatFrequency.MONTHLY_LAST:
            return "on the last day of each month"


def repeat_text(rule: RepeatRule, today: date) -> str:
    """The rule as the detail pane says it: "Repeats every day until Fri Oct 30"."""
    text = "Repeats " + rule_text(rule)
    if rule.end_date is not None:
        text += f" until {day_text(rule.end_date, today=today)}"
    return text


def repeat_short(action: Action) -> str | None:
    """A list row's word for a repeating task: "Repeats daily", "weekly" or "monthly"."""
    repeat = action.repeat
    if repeat is None or repeat.ended:
        return None
    match repeat.rule.frequency:
        case RepeatFrequency.DAILY:
            return "Repeats daily"
        case RepeatFrequency.WEEKLY:
            return "Repeats daily" if repeat.rule.weekdays == ALL_WEEKDAYS else "Repeats weekly"
        case _:
            return "Repeats monthly"


PRIORITY_TEXT: dict[ActionPriority, str] = {
    ActionPriority.HIGH: "High priority",
    ActionPriority.MEDIUM: "Medium priority",
    ActionPriority.LOW: "Low priority",
}


def priority_text(priority: ActionPriority | None) -> str | None:
    return None if priority is None else PRIORITY_TEXT[priority]


def tags_text(tags: tuple[str, ...]) -> str | None:
    """The owner's tags as one plain line, or None without any."""
    return ("Tags: " + ", ".join(tags)) if tags else None


def estimate_text(minutes: int | None) -> str | None:
    """The owner's estimate as "Estimate 45 mins" or "Estimate 1 hr 30 mins", in the event
    editor's words for a length."""
    return None if minutes is None else "Estimate " + length_text(timedelta(minutes=minutes))


def target_text(details: ActionDetails, *, reason: bool = True) -> str | None:
    """The target as "Target Fri Oct 9", or "Follow up Fri Oct 9" while the owner waits for
    someone (B08); with ``reason``, the reason too in the brief detail's wording while it
    applies: "Target Fri Oct 9, one working day before the deadline"."""
    day = follow_up_text(details, reason=reason)
    if day is None:
        return None
    return ("Follow up " if details.waiting else "Target ") + day


def follow_up_text(details: ActionDetails, *, reason: bool = True) -> str | None:
    """The target's day alone, "Fri Oct 9", with its reason as target_text gives it."""
    target = details.target
    if target is None:
        return None
    text = day_text(target, today=details.today)
    if not reason or details.target_reason is None:
        return text
    return f"{text}, {details.target_reason}"


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def waited_for(details: ActionDetails) -> str | None:
    """How long an open action has waited for someone (B08): "since today", "1 day" or "12
    days"; None when it doesn't wait."""
    since = details.waiting_since
    if not details.waiting or since is None:
        return None
    days = (details.today - since).days
    return "since today" if days <= 0 else _days(days)


def waited_text(details: ActionDetails) -> str | None:
    """waited_for as a sentence's start: "Waiting since today", "Waiting 12 days"."""
    waited = waited_for(details)
    return None if waited is None else "Waiting " + waited


def since_text(details: ActionDetails) -> str | None:
    """The day an action started waiting, and while it waits for how long: "Mon Oct 12 ·
    11 days"; None for one that never waited."""
    since = details.waiting_since
    if since is None:
        return None
    text = day_text(since, today=details.today)
    days = (details.today - since).days
    return f"{text} · {_days(days)}" if details.waiting and days > 0 else text


def person_text(action: Action, *, unnamed: str | None = None) -> str | None:
    """Who ``action`` is with, in who_text's shared phrase (B08)."""
    return who_text(action.waiting, action.person, unnamed=unnamed)


def sender_person(action: Action) -> str | None:
    """Who the action's first email is from, as a person (B08): the cached message's display
    name when it can't pass for another address, else the address. It is the email's text,
    so it is cleaned as a typed name is (one line, no control or format characters, at most
    PERSON_MAX_CHARS) before the editor shows it. None without a source."""
    if not action.sources:
        return None
    source = action.sources[0]
    address = clean_person(source.sender_address[:PERSON_MAX_CHARS])
    if with_address(source.sender_name, source.sender_address) == source.sender_address:
        return address
    return clean_person((source.sender_name or "")[:PERSON_MAX_CHARS]) or address


def completed_text(details: ActionDetails) -> str | None:
    """The completion as "Completed Tue Oct 6", the day in the owner's zone, or None for an open
    action."""
    day = details.completed
    return None if day is None else f"Completed {day_text(day, today=details.today)}"


def gmail_source(action: Action) -> str | None:
    """The first source link that opens Gmail, if any; nothing else is ever opened."""
    for source in action.sources:
        link = source.web_link
        if link.scheme == "https" and link.host == "mail.google.com":
            return str(link)
    return None


def _when(moment: datetime, *, today: date, zone: ZoneInfo, clock: bool) -> str:
    """A moment of the past week by weekday ("Tue 14:02"), an older one by its date ("Sun
    Sep 20, 10:30", with the year when it isn't this year's), in ``zone``.

    Weekday names are fixed, like the rest of the window's English text.
    """
    local = moment.astimezone(zone)
    if today - timedelta(days=6) <= local.date() <= today:
        return weekday(local.date()) + (f" {local:%H:%M}" if clock else "")
    day = day_text(local.date(), today=today)
    return f"{day}, {local:%H:%M}" if clock else day
