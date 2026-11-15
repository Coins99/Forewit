"""The AI task-breakdown interface owned by the MailBrief application (ADR 0033)."""

from typing import Protocol, runtime_checkable

from mailbrief.domain.breakdown import BreakdownRequest, BreakdownResponse


@runtime_checkable
class BreakdownProvider(Protocol):
    """Proposes checklist steps from exactly the context in a BreakdownRequest."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def breakdown_prompt_version(self) -> str: ...

    @property
    def privacy_notice(self) -> str: ...

    @property
    def requests_sent(self) -> int: ...

    async def credentials_available(self) -> bool: ...

    async def break_down(self, request: BreakdownRequest) -> BreakdownResponse:
        """One logical call; the candidate comes back unchecked. Failures raise
        ``mailbrief.ports.errors`` types, which never contain task text."""
        ...
