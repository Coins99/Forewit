"""Validate each system's candidates as the app does, and score them against the labels.

Every message counts: one with no valid analysis scores wrong on every scored field.
"""

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from mailbrief.domain.analysis import (
    AnalysisCandidate,
    AnalysisProblem,
    AnalysisRequest,
    AnalysisResponse,
    DeadlinePrecision,
    MessageAnalysis,
)
from mailbrief.services.analysis import validate_candidate
from scripts.local_ai.fixture import FixtureMessage

Status = Literal["valid", "rejected", "missing", "incomplete", "refused", "invalid_output", "error"]
_PROBLEM_STATUS: dict[AnalysisProblem, Status] = {
    AnalysisProblem.INCOMPLETE: "incomplete",
    AnalysisProblem.REFUSED: "refused",
    AnalysisProblem.INVALID_OUTPUT: "invalid_output",
}

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one system produced for one message."""

    key: str
    status: Status
    analysis: MessageAnalysis | None
    proposed_actions: int  # Actions in the candidate, before validation dropped any.


def outcomes(
    requests: Sequence[AnalysisRequest], response: AnalysisResponse | None
) -> list[Outcome]:
    """One outcome per request from one call's response; ``None`` means the call failed."""
    if response is None:
        return [Outcome(request.message_key, "error", None, 0) for request in requests]
    if response.problem is not None:
        status = _PROBLEM_STATUS[response.problem]
        return [Outcome(request.message_key, status, None, 0) for request in requests]
    by_key: dict[str, AnalysisCandidate] = {}
    for item in response.candidates:
        by_key.setdefault(item.message_key, item)
    found: list[Outcome] = []
    for request in requests:
        candidate = by_key.get(request.message_key)
        if candidate is None:
            found.append(Outcome(request.message_key, "missing", None, 0))
            continue
        try:
            analysis = validate_candidate(candidate, request)
        except ValueError:
            found.append(Outcome(request.message_key, "rejected", None, len(candidate.actions)))
            continue
        found.append(Outcome(request.message_key, "valid", analysis, len(candidate.actions)))
    return found


@dataclass(frozen=True, slots=True)
class MessageScore:
    key: str
    sensitivity: str
    status: Status
    category: bool
    action_required: bool | None  # None: not scored for this message.
    deadline: bool | None
    deadline_time: bool | None
    follow_up: bool
    expected_actions: int | None
    matched_actions: int
    unexpected_actions: int
    proposed_actions: int
    kept_actions: int
    summary_facts: int
    summary_facts_found: int
    language: bool | None
    forbidden: tuple[str, ...]  # must_not_mention text the output repeats.
    past_deadlines: int  # Deadlines resolved to a day before the email arrived.
    unsupported_numbers: tuple[str, ...]  # Numbers in the summary or action the email lacks.
    summary: str | None
    suggestion_titles: tuple[str, ...]
    # What the system said, so a miss can be told apart: None without a valid analysis.
    predicted_category: str | None
    predicted_action_required: bool | None
    predicted_deadline: str | None  # Its day, or "unresolved" or "none".
    predicted_follow_up: str | None


def _number(token: str) -> Decimal | str:
    """A number token's value, so "1,249" and "1249.00" match; text when it isn't one."""
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return token


def _unsupported_numbers(analysis: MessageAnalysis, request: AnalysisRequest) -> tuple[str, ...]:
    """Numbers the summary or action states that the subject and body never write, compared
    as whole numbers: "49" isn't supported by "1,249".

    Clock times are left out (the model may turn 6pm into 18:00), and so is the received
    year, which a resolved date may add. A review flag, not a score.
    """
    source = {
        _number(token) for token in _NUMBER.findall(f"{request.subject}\n{request.body_text}")
    }
    source.add(Decimal(request.received_at_utc.year))
    written = f"{analysis.summary}\n{analysis.action_text or ''}"
    return tuple(
        token for token in _NUMBER.findall(_CLOCK.sub(" ", written)) if _number(token) not in source
    )


def _mentions(title: str, word: str) -> bool:
    """Whether a casefolded title uses a casefolded keyword: from the start of a word for a
    Latin keyword, so "pr" isn't found in "approve", and anywhere for others, since Japanese
    has no spaces."""
    if word[:1].isascii() and word[:1].isalnum():
        return re.search(r"(?<![a-z0-9])" + re.escape(word), title) is not None
    return word in title


def _predicted_deadline(analysis: MessageAnalysis) -> str:
    if analysis.deadline_date is not None:
        return analysis.deadline_date.isoformat()
    return analysis.deadline_precision.value


def _deadline_days(analysis: MessageAnalysis) -> Iterable[dt.date]:
    if analysis.deadline_date is not None:
        yield analysis.deadline_date
    for suggestion in analysis.suggestions:
        if suggestion.deadline_date is not None:
            yield suggestion.deadline_date


def _deadline_scores(
    message: FixtureMessage, analysis: MessageAnalysis | None, zone: ZoneInfo
) -> tuple[bool | None, bool | None]:
    expected = message.expected.deadline
    if expected == "skip":
        return None, None
    if expected == "none":
        return analysis is not None and analysis.deadline_precision in (
            DeadlinePrecision.NONE,
            DeadlinePrecision.UNRESOLVED,
        ), None
    date_ok = analysis is not None and analysis.deadline_date == expected.date
    if expected.time is None:
        return date_ok, None
    at = None if analysis is None else analysis.deadline_at_utc
    time_ok = date_ok and at is not None and f"{at.astimezone(zone):%H:%M}" == expected.time
    return date_ok, time_ok


def _match_actions(message: FixtureMessage, analysis: MessageAnalysis | None) -> tuple[int, int]:
    """(matched expected actions, kept suggestions that match none), greedily in order."""
    wanted = message.expected.actions
    if wanted is None or analysis is None:
        return 0, 0
    unused = list(analysis.suggestions)
    matched = 0
    for action in wanted:
        words = [word.casefold() for word in action.any_of]
        for suggestion in unused:
            title = suggestion.title.casefold()
            if suggestion.ownership is action.ownership and any(
                _mentions(title, word) for word in words
            ):
                unused.remove(suggestion)
                matched += 1
                break
    return matched, len(unused)


def score(message: FixtureMessage, request: AnalysisRequest, outcome: Outcome) -> MessageScore:
    """How one outcome compares with its message's labels."""
    expected = message.expected
    analysis = outcome.analysis
    zone = ZoneInfo(request.timezone_name)
    deadline, deadline_time = _deadline_scores(message, analysis, zone)
    matched, unexpected = _match_actions(message, analysis)
    summary = "" if analysis is None else analysis.summary.casefold()
    facts_found = sum(
        any(spelling.casefold() in summary for spelling in fact)
        for fact in expected.summary_mentions
    )
    written = (
        ""
        if analysis is None
        else "\n".join(
            [analysis.summary, analysis.action_text or ""]
            + [item.title for item in analysis.suggestions]
        ).casefold()
    )
    received = request.received_at_utc.astimezone(zone).date()
    return MessageScore(
        key=message.key,
        sensitivity=expected.sensitivity,
        status=outcome.status,
        category=analysis is not None and analysis.category in expected.categories,
        action_required=(
            None
            if expected.action_required is None
            else analysis is not None and analysis.action_required == expected.action_required
        ),
        deadline=deadline,
        deadline_time=deadline_time,
        follow_up=analysis is not None and analysis.follow_up in expected.follow_ups,
        expected_actions=None if expected.actions is None else len(expected.actions),
        matched_actions=matched,
        unexpected_actions=unexpected,
        proposed_actions=outcome.proposed_actions,
        kept_actions=0 if analysis is None else len(analysis.suggestions),
        summary_facts=len(expected.summary_mentions),
        summary_facts_found=facts_found,
        language=(
            None
            if expected.summary_language == "en"
            else analysis is not None and _JAPANESE.search(analysis.summary) is not None
        ),
        forbidden=tuple(text for text in expected.must_not_mention if text.casefold() in written),
        past_deadlines=(
            0 if analysis is None else sum(day < received for day in _deadline_days(analysis))
        ),
        unsupported_numbers=() if analysis is None else _unsupported_numbers(analysis, request),
        summary=None if analysis is None else analysis.summary,
        suggestion_titles=() if analysis is None else tuple(s.title for s in analysis.suggestions),
        predicted_category=None if analysis is None else analysis.category.value,
        predicted_action_required=None if analysis is None else analysis.action_required,
        predicted_deadline=None if analysis is None else _predicted_deadline(analysis),
        predicted_follow_up=None if analysis is None else analysis.follow_up.value,
    )


@dataclass(frozen=True, slots=True)
class Ratio:
    hits: int
    total: int

    @property
    def rate(self) -> float | None:
        return self.hits / self.total if self.total else None

    def __str__(self) -> str:
        rate = self.rate
        return "n/a" if rate is None else f"{self.hits}/{self.total} ({rate:.0%})"


@dataclass(frozen=True, slots=True)
class Quality:
    """One system's quality over every scored message."""

    valid: Ratio
    valid_by_sensitivity: dict[str, Ratio]
    category: Ratio
    action_required: Ratio
    deadline: Ratio
    deadline_time: Ratio
    follow_up: Ratio
    action_recall: Ratio  # Expected actions a kept suggestion matched.
    action_precision: Ratio  # Kept suggestions that matched an expected action.
    suggestions_kept: Ratio  # Proposed actions that survived validation.
    summary_facts: Ratio
    language: Ratio
    forbidden_echoes: int
    past_deadlines: int
    unsupported_numbers: int


def _ratio(values: Iterable[bool | None]) -> Ratio:
    scored = [value for value in values if value is not None]
    return Ratio(sum(scored), len(scored))


def quality(scores: Sequence[MessageScore]) -> Quality:
    actions = [item for item in scores if item.expected_actions is not None]
    matched = sum(item.matched_actions for item in actions)
    kept_scored = sum(item.kept_actions for item in actions)
    sensitivities = sorted({item.sensitivity for item in scores})
    return Quality(
        valid=_ratio(item.status == "valid" for item in scores),
        valid_by_sensitivity={
            name: _ratio(item.status == "valid" for item in scores if item.sensitivity == name)
            for name in sensitivities
        },
        category=_ratio(item.category for item in scores),
        action_required=_ratio(item.action_required for item in scores),
        deadline=_ratio(item.deadline for item in scores),
        deadline_time=_ratio(item.deadline_time for item in scores),
        follow_up=_ratio(item.follow_up for item in scores),
        action_recall=Ratio(matched, sum(item.expected_actions or 0 for item in actions)),
        action_precision=Ratio(matched, kept_scored),
        suggestions_kept=Ratio(
            sum(item.kept_actions for item in scores),
            sum(item.proposed_actions for item in scores),
        ),
        summary_facts=Ratio(
            sum(item.summary_facts_found for item in scores),
            sum(item.summary_facts for item in scores),
        ),
        language=_ratio(item.language for item in scores),
        forbidden_echoes=sum(len(item.forbidden) for item in scores),
        past_deadlines=sum(item.past_deadlines for item in scores),
        unsupported_numbers=sum(len(item.unsupported_numbers) for item in scores),
    )
