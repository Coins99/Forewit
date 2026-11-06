"""Shared domain-model behavior."""

import uuid
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

ZONE_KEY_MAX_CHARS: Final = 64  # The longest IANA zone key stored with a time.


class DomainModel(BaseModel):
    """Immutable base for values crossing application boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def normalize_utc(value: datetime) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a time zone")
    return value.astimezone(UTC)


def utc_now() -> datetime:
    """The current time, aware and in UTC: the clock services and ORM defaults use."""
    return datetime.now(UTC)


def new_public_id() -> str:
    """A new random public ID for an owner record."""
    return str(uuid.uuid4())


def known_zone(key: str) -> str:
    """``key`` when it names an IANA time zone; raises ValueError with a static message."""
    try:
        ZoneInfo(key)
    except (KeyError, ValueError, OSError):
        raise ValueError("the time zone is unknown") from None
    return key
