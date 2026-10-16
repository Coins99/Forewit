"""Static Google Calendar errors; messages never include server text, IDs or tokens."""

from typing import Final

from mailbrief.errors import ConfigurationError
from mailbrief.ports.errors import ProviderResponseError
from mailbrief.providers.google.requests import error_reasons

# Quota reasons a retry won't clear; the client reports them as a rate limit, not a refusal.
QUOTA_REASONS: Final = frozenset({"dailyLimitExceeded", "quotaExceeded"})
QUOTA_GUIDANCE: Final = (
    "Google Calendar's API quota is used up for now; check the project's quotas and retry later."
)


class CalendarSetupError(ConfigurationError):
    """Use only static guidance: never include paths, payloads or backend exceptions."""


NOT_ALLOWED: Final = (
    "Google Calendar isn't allowed yet; choose Allow Google Calendar on the Calendar page."
)


def calendar_response_error(message: str) -> ProviderResponseError:
    """An unusable answer from Google Calendar; ``message`` must be static."""
    return ProviderResponseError(message)


def calendar_permission_guidance(payload: object) -> str:
    """Translate recognized Google reasons without exposing response contents."""
    reasons = error_reasons(payload)
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return (
            "Google Calendar API is disabled for this OAuth client's Google Cloud project. "
            "Enable it in that project's API Library, then retry."
        )
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return (
            "Google did not grant calendar read access. Choose Allow Google Calendar and tick "
            "both calendar boxes."
        )
    if reasons & {"domainPolicy", "ORG_RESTRICTION_VIOLATION"}:
        return "Your Google administrator does not allow this app to read calendars."
    if reasons & (QUOTA_REASONS | {"rateLimitExceeded", "userRateLimitExceeded"}):
        return QUOTA_GUIDANCE
    return "Google Calendar denied access; choose Allow Google Calendar and retry."
