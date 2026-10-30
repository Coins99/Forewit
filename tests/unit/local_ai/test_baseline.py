"""The rule-based baseline: deadlines it can read, and candidates the app always accepts."""

import datetime as dt
from pathlib import Path

import pytest

from mailbrief.domain.analysis import ActionOwnership, FollowUpKind
from mailbrief.services.analysis import validate_candidate
from scripts.local_ai import baseline
from scripts.local_ai.fixture import DEFAULT_FIXTURE, load_fixture

FRIDAY = dt.date(2026, 10, 30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("reply NO by 6pm today and", ("by 6pm today", "2026-10-30", "18:00")),
        ("sign it by Nov 6 so payroll", ("by Nov 6", "2026-11-06", None)),
        ("before Monday's release", ("before Monday's", "2026-11-02", None)),
        ("due on November 12, 2026.", ("due on November 12, 2026", "2026-11-12", None)),
        ("by tomorrow at 9:30 am", ("by tomorrow at 9:30 am", "2026-10-31", "09:30")),
        ("by next Friday", ("by next Friday", "2026-11-06", None)),
        ("by end of week", ("by end of week", "2026-10-30", None)),
        ("by 2026-12-01", ("by 2026-12-01", "2026-12-01", None)),
        ("by Jan 5", ("by Jan 5", "2027-01-05", None)),  # Past this year, so next year's.
        ("来週の水曜日までに払って", ("来週の水曜日まで", "2026-11-04", None)),
        ("11月20日までに", ("11月20日まで", "2026-11-20", None)),
    ],
)
def test_find_deadline_reads_the_phrase_and_its_day(
    text: str, expected: tuple[str, str | None, str | None]
) -> None:
    assert baseline.find_deadline(text, FRIDAY) == expected


@pytest.mark.parametrize("text", ["see you next week", "within the next two weeks", ""])
def test_text_without_a_deadline_has_none(text: str) -> None:
    assert baseline.find_deadline(text, FRIDAY) is None


def test_a_month_day_long_past_is_next_years_in_both_languages() -> None:
    december = dt.date(2026, 12, 20)
    assert baseline.find_deadline("1月10日までに", december) == ("1月10日まで", "2027-01-10", None)
    assert baseline.find_deadline("by Jan 10", december) == ("by Jan 10", "2027-01-10", None)
    assert baseline.find_deadline("12月1日までに", december) == ("12月1日まで", "2026-12-01", None)


def test_an_impossible_day_keeps_the_phrase_unresolved() -> None:
    assert baseline.find_deadline("2月30日までに", FRIDAY) == ("2月30日まで", None, None)
    assert baseline.find_deadline("by Feb 30", FRIDAY) == ("by Feb 30", None, None)


@pytest.mark.parametrize("path", [DEFAULT_FIXTURE, DEFAULT_FIXTURE.with_name("holdout-10.json")])
def test_every_baseline_candidate_passes_the_apps_validation(path: Path) -> None:
    fixture = load_fixture(path)
    for request in fixture.requests():
        analysis = validate_candidate(baseline.analyze(request), request)
        assert analysis.message_key == request.message_key


def test_a_promise_becomes_a_waiting_action_and_a_request_the_owners() -> None:
    fixture = load_fixture()
    request = fixture.request(next(m for m in fixture.messages if m.key == "m12"))
    candidate = baseline.analyze(request)
    assert [action.ownership for action in candidate.actions] == [
        ActionOwnership.MINE,
        ActionOwnership.WAITING_FOR,
    ]
    assert candidate.deadline_date == "2026-10-30"


def test_a_cancellation_is_a_follow_up_with_its_sentence() -> None:
    fixture = load_fixture()
    request = fixture.request(next(m for m in fixture.messages if m.key == "m13"))
    candidate = baseline.analyze(request)
    assert candidate.follow_up is FollowUpKind.CANCELLED
    assert candidate.follow_up_evidence == "Sorry, I have to cancel our 1:1 on Tuesday."


def test_amounts_stay_inside_one_sentence() -> None:
    fixture = load_fixture()
    request = fixture.request(next(m for m in fixture.messages if m.key == "m08"))
    assert baseline.analyze(request).summary.startswith(
        "Your invoice INV-2207 for $340.00 is due on November 12, 2026."
    )
