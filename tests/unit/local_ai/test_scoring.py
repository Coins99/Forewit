"""Scoring validates like the app and compares each analysis with its labels."""

from typing import Any

from mailbrief.domain.analysis import (
    AnalysisCandidate,
    AnalysisProblem,
    AnalysisResponse,
)
from scripts.local_ai.fixture import FixtureMessage, load_fixture
from scripts.local_ai.scoring import Outcome, outcomes, quality, score
from tests.unit.providers.groq.groq_fixtures import wire_action, wire_result

FIXTURE = load_fixture()


def _message(key: str) -> FixtureMessage:
    return next(message for message in FIXTURE.messages if message.key == key)


def _candidate(key: str, **fields: Any) -> AnalysisCandidate:
    fields.setdefault("evidence", _message(key).body_lines[0])
    return AnalysisCandidate.model_validate(wire_result(key, **fields))


def _outcome(key: str, **fields: Any) -> Outcome:
    request = FIXTURE.request(_message(key))
    response = AnalysisResponse(candidates=(_candidate(key, **fields),))
    return outcomes([request], response)[0]


GOOD_M01: dict[str, Any] = {
    "category": "action",
    "summary": "A $1,249 card transaction needs checking.",
    "action_required": True,
    "action_text": "Call the bank if you don't recognize it.",
    "deadline_text": "by 6pm today",
    "deadline_date": "2026-10-30",
    "deadline_time": "18:00",
    "actions": [
        wire_action(
            "call us at 1-800-555-0199",
            title="Call the bank about the charge",
            deadline_text="by 6pm today",
            deadline_date="2026-10-30",
            deadline_time="18:00",
        )
    ],
}


def test_a_good_analysis_scores_every_field() -> None:
    message = _message("m01")
    result = score(message, FIXTURE.request(message), _outcome("m01", **GOOD_M01))
    assert result.status == "valid"
    assert result.category and result.action_required and result.follow_up
    assert result.deadline is True and result.deadline_time is True
    assert (result.matched_actions, result.unexpected_actions) == (1, 0)
    assert (result.summary_facts_found, result.summary_facts) == (2, 2)
    assert result.forbidden == () and result.past_deadlines == 0
    assert result.unsupported_numbers == ()


def test_a_wrong_day_and_an_invented_number_are_caught() -> None:
    message = _message("m01")
    fields = GOOD_M01 | {
        "deadline_date": "2026-10-28",
        "summary": "A $2,000 charge at 18:00 in 2026 needs checking.",
    }
    result = score(message, FIXTURE.request(message), _outcome("m01", **fields))
    assert result.deadline is False and result.deadline_time is False
    assert result.past_deadlines == 1
    assert result.unsupported_numbers == ("2,000",)  # The clock time and the year are left out.


def test_echoing_a_code_is_flagged() -> None:
    message = _message("m03")
    result = score(
        message,
        FIXTURE.request(message),
        _outcome("m03", evidence="Your verification code is 482913.", summary="Code 482913."),
    )
    assert result.forbidden == ("482913",)


def test_an_unresolved_phrase_counts_as_no_specific_deadline() -> None:
    message = _message("m02")
    request = FIXTURE.request(message)
    fields = {"deadline_text": "within the next two weeks"}
    assert score(message, request, _outcome("m02", **fields)).deadline is True
    fields = {"deadline_text": "within the next two weeks", "deadline_date": "2026-11-13"}
    assert score(message, request, _outcome("m02", **fields)).deadline is False


def test_failures_score_wrong_on_every_scored_field() -> None:
    message = _message("m01")
    request = FIXTURE.request(message)
    for outcome in [
        outcomes([request], None)[0],
        outcomes([request], AnalysisResponse(problem=AnalysisProblem.INCOMPLETE))[0],
        outcomes([request], AnalysisResponse())[0],
        _outcome("m01", evidence="text that is not in the email"),
    ]:
        result = score(message, request, outcome)
        assert not result.category and result.action_required is False
        assert result.deadline is False and result.matched_actions == 0
    statuses = [
        outcomes([request], None)[0].status,
        outcomes([request], AnalysisResponse(problem=AnalysisProblem.INCOMPLETE))[0].status,
        outcomes([request], AnalysisResponse())[0].status,
        _outcome("m01", evidence="text that is not in the email").status,
    ]
    assert statuses == ["error", "incomplete", "missing", "rejected"]


def test_quality_counts_only_scored_fields() -> None:
    m01, m16 = _message("m01"), _message("m16")
    scores = [
        score(m01, FIXTURE.request(m01), _outcome("m01", **GOOD_M01)),
        score(m16, FIXTURE.request(m16), outcomes([FIXTURE.request(m16)], None)[0]),
    ]
    summary = quality(scores)
    assert (summary.valid.hits, summary.valid.total) == (1, 2)
    assert summary.action_required.total == 1  # m16 leaves it unscored.
    assert (summary.action_recall.hits, summary.action_recall.total) == (1, 1)
    assert summary.valid_by_sensitivity["ambiguous"].hits == 0
    assert str(summary.valid) == "1/2 (50%)"
