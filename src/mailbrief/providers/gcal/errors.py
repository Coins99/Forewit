"""Static Google Calendar errors; messages never include server text, IDs or tokens."""

from mailbrief.errors import ConfigurationError
from mailbrief.ports.errors import ProviderResponseError


class CalendarSetupError(ConfigurationError):
    """Use only static guidance: never include paths, payloads or backend exceptions."""


def calendar_response_error(message: str) -> ProviderResponseError:
    """An unusable answer from Google Calendar; ``message`` must be static."""
    return ProviderResponseError(message)


def calendar_permission_guidance(payload: object) -> str:
    """Translate recognized Google reasons without exposing response contents."""
    reasons: set[str] = set()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        for field in ("errors", "details"):
            entries = error.get(field)
            if isinstance(entries, list):
                for entry in entries:
                    reason = entry.get("reason") if isinstance(entry, dict) else None
                    if isinstance(reason, str):
                        reasons.add(reason)
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return (
            "Google Calendar API is disabled for this OAuth client's Google Cloud project. "
            "Enable it in that project's API Library, then retry."
        )
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return (
            "Google did not grant calendar read access. Disconnect Google Calendar, "
            "reconnect and allow both calendar permissions."
        )
    if reasons & {"domainPolicy", "ORG_RESTRICTION_VIOLATION"}:
        return "Your Google administrator does not allow this app to read calendars."
    return "Google Calendar denied access; reconnect Google Calendar and retry."
