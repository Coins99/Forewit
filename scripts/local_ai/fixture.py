"""The benchmark's synthetic messages and the labels each system is scored against."""

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mailbrief.domain.analysis import (
    ActionOwnership,
    AnalysisCategory,
    AnalysisRequest,
    FollowUpKind,
)
from mailbrief.domain.messages import EmailContact

DEFAULT_FIXTURE = Path(__file__).with_name("overnight-20.json")

Sensitivity = Literal["sensitive", "ordinary", "ambiguous"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedDeadline(_Frozen):
    """The day a deadline means, and its local clock time when the email states one."""

    date: dt.date
    time: str | None = Field(default=None, pattern=r"^[0-9]{2}:[0-9]{2}$")


class ExpectedAction(_Frozen):
    """An action a good analysis suggests: its ownership and words one of its titles uses."""

    ownership: ActionOwnership
    any_of: tuple[str, ...] = Field(min_length=1)


class Expected(_Frozen):
    """What a good analysis says. ``None`` (or ``"skip"`` for a deadline) leaves a field
    unscored where reasonable readers would disagree."""

    sensitivity: Sensitivity
    categories: tuple[AnalysisCategory, ...] = Field(min_length=1)
    action_required: bool | None
    # "none": no specific day (an unresolved phrase such as "within two weeks" also passes).
    deadline: ExpectedDeadline | Literal["none", "skip"]
    follow_ups: tuple[FollowUpKind, ...] = Field(min_length=1)
    actions: tuple[ExpectedAction, ...] | None
    # Facts the summary should carry; each is any one of its spellings.
    summary_mentions: tuple[tuple[str, ...], ...] = ()
    # Text that should never reach the summary, action or titles (a code, injected words).
    must_not_mention: tuple[str, ...] = ()
    summary_language: Literal["en", "ja"] = "en"


class FixtureMessage(_Frozen):
    """One synthetic, already prepared email: plain text with quoted history removed."""

    key: str = Field(min_length=1, max_length=64)
    subject: str
    sender_name: str | None
    sender_address: str
    received_utc: dt.datetime
    body_lines: tuple[str, ...] = Field(min_length=1)
    body_truncated: bool = False
    expected: Expected

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines)


class Fixture(_Frozen):
    name: str
    description: str
    time_zone: str
    messages: tuple[FixtureMessage, ...] = Field(min_length=1)

    def request(self, message: FixtureMessage) -> AnalysisRequest:
        """The minimized request the app would build for this message."""
        return AnalysisRequest(
            message_key=message.key,
            subject=message.subject,
            sender=EmailContact(name=message.sender_name, address=message.sender_address),
            received_at_utc=message.received_utc,
            timezone_name=self.time_zone,
            body_text=message.body,
            body_truncated=message.body_truncated,
        )

    def requests(self) -> tuple[AnalysisRequest, ...]:
        return tuple(self.request(message) for message in self.messages)


def load_fixture(path: Path = DEFAULT_FIXTURE) -> Fixture:
    """Load and check a fixture; its keys must be unique."""
    fixture = Fixture.model_validate_json(path.read_bytes())
    keys = [message.key for message in fixture.messages]
    if len(set(keys)) != len(keys):
        raise ValueError("fixture message keys must be unique")
    return fixture


def fixture_digest(path: Path) -> str:
    """SHA-256 of the fixture file, recorded with every result."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
