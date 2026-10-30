"""A lightweight local baseline: deterministic rules and no model.

It builds the same untrusted candidate an AI provider returns, so the app's validation
(``validate_candidate``) and the benchmark's scoring treat both alike. Its summary is the
email's own first sentences: an honest extractive preview, never presented as an AI summary.
"""

import datetime as dt
import re
from zoneinfo import ZoneInfo

from mailbrief.domain.analysis import (
    ActionCandidate,
    ActionOwnership,
    AnalysisCandidate,
    AnalysisCategory,
    AnalysisRequest,
    FollowUpKind,
)

MAX_ACTIONS = 5
CONFIDENCE = 0.3

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_JA_WEEKDAYS = "月火水木金土日"
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY = "|".join(_WEEKDAYS)
_TIME = r"\d{1,2}(?::\d{2})?\s?(?:am|pm)|\d{1,2}:\d{2}"
_DAY = (
    r"today|tonight|tomorrow|end of (?:the )?day|end of (?:the )?week|eod"
    rf"|(?:(?:this|next) )?(?:{_WEEKDAY})(?:'s)?"
    rf"|(?:{_MONTH})\.? \d{{1,2}}(?:st|nd|rd|th)?(?:,? \d{{4}})?"
    r"|\d{4}-\d{2}-\d{2}"
)
_DEADLINE = re.compile(
    rf"\b(?:by|before|due(?: on| by)?|until|no later than) "
    rf"(?:(?P<early>{_TIME}) )?(?P<day>{_DAY})(?: at (?P<late>{_TIME}))?\b",
    re.IGNORECASE,
)
_JA_DEADLINE = re.compile(
    rf"(?:(?P<week>来週|今週)の?(?P<weekday>[{_JA_WEEKDAYS}])曜日?"
    r"|(?P<month>\d{1,2})月(?P<day>\d{1,2})日)まで"
)
_REQUEST = re.compile(
    r"\b(?:please|could you|can you|would you|kindly|let me know|need you to|call us"
    r"|tell us|reply|rsvp)\b|ください",
    re.IGNORECASE,
)
_PROMISE = re.compile(
    r"\b(?:i'll|i will|we'll|we will) (?:send|share|forward|deliver|get you)\b", re.IGNORECASE
)
_CANCELLED = re.compile(r"\b(?:cancel(?:l?ed|l?ing)?|called off)\b|中止|キャンセル", re.I)
_DELIVERED = re.compile(
    r"\b(?:was delivered|has been delivered|is attached|attached is|here is|here are"
    r"|is done|is complete)\b",
    re.IGNORECASE,
)
_MOVED = re.compile(r"\b(?:moved to|pushed to|postponed|rescheduled|extended to)\b", re.I)
_DECISION = re.compile(r"\b(?:decided|decision|approved|agreed)\b", re.IGNORECASE)
_GREETING = re.compile(
    r"^(?:hi|hello|hey|dear|thanks|thank you|best|regards|cheers)\b[^.!?]{0,30}[,.!]?$", re.I
)
# A Latin sentence ends at punctuation and a space, so "$1,249.00" stays whole; a Japanese
# one ends at its own punctuation.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
_ENDS_SENTENCE = re.compile(r"[.!?。！？:]")


def _sentences(text: str) -> list[str]:
    """The body's sentences, each an exact substring, without greetings and sign-offs."""
    found: list[str] = []
    for line in text.splitlines():
        for piece in _SENTENCE_END.split(line.strip()):
            sentence = piece.strip()
            if not sentence or _GREETING.match(sentence):
                continue
            if len(sentence) < 25 and not _ENDS_SENTENCE.search(sentence):
                continue  # A name, a signature or a salutation such as "佐藤様".
            found.append(sentence)
    return found


def _clock(value: str | None) -> str | None:
    """A time such as "6pm", "6:30 pm" or "18:00" as 24-hour HH:MM."""
    if value is None:
        return None
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s?(am|pm)?", value.strip(), re.IGNORECASE)
    if match is None:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    suffix = (match[3] or "").casefold()
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def _next_weekday(start: dt.date, weekday: int) -> dt.date:
    return start + dt.timedelta(days=(weekday - start.weekday()) % 7)


def _resolve_day(phrase: str, received: dt.date) -> dt.date | None:
    """The day an English phrase names, counted from the received day."""
    text = phrase.casefold().removesuffix("'s")
    if text in ("today", "tonight", "eod") or (text.startswith("end of") and "day" in text):
        return received
    if text == "tomorrow":
        return received + dt.timedelta(days=1)
    if text.startswith("end of"):
        return _next_weekday(received, _WEEKDAYS.index("friday"))
    words = text.split()
    if words[-1] in _WEEKDAYS:
        day = _next_weekday(received, _WEEKDAYS.index(words[-1]))
        if words[0] == "next":
            monday = received - dt.timedelta(days=received.weekday()) + dt.timedelta(days=7)
            day = monday + dt.timedelta(days=_WEEKDAYS.index(words[-1]))
        return day
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return None
    match = re.fullmatch(rf"({_MONTH})\.? (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?", text)
    if match is None:
        return None
    year = int(match[3]) if match[3] else received.year
    try:
        day = dt.date(year, _MONTHS[match[1]], int(match[2]))
    except ValueError:
        return None
    if not match[3] and day < received - dt.timedelta(days=31):
        day = day.replace(year=year + 1)
    return day


def find_deadline(text: str, received: dt.date) -> tuple[str, str | None, str | None] | None:
    """The first deadline phrase in ``text``, exactly as written, with its YYYY-MM-DD day
    and HH:MM time when they can be worked out."""
    english = _DEADLINE.search(text)
    japanese = _JA_DEADLINE.search(text)
    if english is not None and (japanese is None or english.start() <= japanese.start()):
        day = _resolve_day(english["day"], received)
        clock = _clock(english["late"] or english["early"])
        return english[0], None if day is None else day.isoformat(), clock
    if japanese is None:
        return None
    if japanese["weekday"] is not None:
        monday = received - dt.timedelta(days=received.weekday())
        if japanese["week"] == "来週":
            monday += dt.timedelta(days=7)
        day = monday + dt.timedelta(days=_JA_WEEKDAYS.index(japanese["weekday"]))
        return japanese[0], day.isoformat(), None
    try:
        dated = dt.date(received.year, int(japanese["month"]), int(japanese["day"]))
    except ValueError:
        return japanese[0], None, None
    return japanese[0], dated.isoformat(), None


def _action(sentence: str, ownership: ActionOwnership, received: dt.date) -> ActionCandidate:
    deadline = find_deadline(sentence, received)
    text, day, clock = deadline if deadline is not None else (None, None, None)
    return ActionCandidate(
        title=sentence,
        ownership=ownership,
        effort=None,
        deadline_text=text,
        deadline_date=day,
        deadline_time=clock,
        stated_timezone=None,
        steps=(),
        evidence=sentence,
    )


def _follow_up(sentences: list[str], received: dt.date) -> tuple[FollowUpKind, str | None]:
    for sentence in sentences:
        if _CANCELLED.search(sentence):
            return FollowUpKind.CANCELLED, sentence
        if _MOVED.search(sentence) and find_deadline(sentence, received) is not None:
            return FollowUpKind.NEW_DEADLINE, sentence
        if _DELIVERED.search(sentence):
            return FollowUpKind.DELIVERED, sentence
    return FollowUpKind.NONE, None


def analyze(request: AnalysisRequest) -> AnalysisCandidate:
    """The baseline's candidate for one request."""
    received = request.received_at_utc.astimezone(ZoneInfo(request.timezone_name)).date()
    sentences = _sentences(request.body_text) or [request.subject or request.body_text.strip()]
    asks = [sentence for sentence in sentences if _REQUEST.search(sentence)]
    promises = [s for s in sentences if _PROMISE.search(s) and s not in asks]
    deadline = None
    for sentence in [*asks, *sentences]:
        deadline = find_deadline(sentence, received)
        if deadline is not None:
            break
    text, day, clock = deadline if deadline is not None else (None, None, None)
    follow_up, follow_up_evidence = _follow_up(sentences, received)
    if any(_DECISION.search(sentence) for sentence in sentences):
        category = AnalysisCategory.DECISION
    elif asks:
        category = AnalysisCategory.ACTION
    elif deadline is not None:
        category = AnalysisCategory.DEADLINE
    else:
        category = AnalysisCategory.INFORMATION
    lead = sentences[0]
    summary = lead if not asks or asks[0] == lead else f"{lead} {asks[0]}"
    actions = [_action(s, ActionOwnership.MINE, received) for s in asks]
    actions += [_action(s, ActionOwnership.WAITING_FOR, received) for s in promises]
    return AnalysisCandidate(
        message_key=request.message_key,
        category=category,
        summary=summary,
        action_required=bool(asks),
        action_text=asks[0] if asks else None,
        deadline_text=text,
        deadline_date=day,
        deadline_time=clock,
        stated_timezone=None,
        confidence=CONFIDENCE,
        evidence=asks[0] if asks else lead,
        actions=tuple(actions[:MAX_ACTIONS]),
        follow_up=follow_up,
        follow_up_evidence=follow_up_evidence,
    )
