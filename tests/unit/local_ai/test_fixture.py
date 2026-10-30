"""The benchmark fixtures load, build valid requests and keep the planned mix."""

from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.local_ai.fixture import DEFAULT_FIXTURE, ExpectedDeadline, load_fixture

HOLDOUT = DEFAULT_FIXTURE.with_name("holdout-10.json")


@pytest.mark.parametrize("path", [DEFAULT_FIXTURE, HOLDOUT])
def test_every_message_builds_a_request_the_app_accepts(path: Path) -> None:
    fixture = load_fixture(path)
    requests = fixture.requests()
    assert [request.message_key for request in requests] == [m.key for m in fixture.messages]
    assert all(request.timezone_name == fixture.time_zone for request in requests)


def test_the_overnight_fixture_mixes_sensitive_ordinary_and_ambiguous_mail() -> None:
    fixture = load_fixture()
    assert len(fixture.messages) == 20
    mix = Counter(message.expected.sensitivity for message in fixture.messages)
    assert mix == {"sensitive": 6, "ordinary": 8, "ambiguous": 6}


@pytest.mark.parametrize("path", [DEFAULT_FIXTURE, HOLDOUT])
def test_expected_deadlines_fall_on_or_after_the_local_day_the_email_arrives(path: Path) -> None:
    fixture = load_fixture(path)
    zone = ZoneInfo(fixture.time_zone)
    for message in fixture.messages:
        deadline = message.expected.deadline
        if isinstance(deadline, ExpectedDeadline):
            assert deadline.date >= message.received_utc.astimezone(zone).date(), message.key


def test_duplicate_keys_are_refused(tmp_path: Path) -> None:
    text = DEFAULT_FIXTURE.read_text(encoding="utf-8").replace('"key": "m02"', '"key": "m01"')
    copy = tmp_path / "fixture.json"
    copy.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_fixture(copy)
