"""The AI calendar-planning interface owned by the MailBrief application (ADR 0018)."""

from typing import Protocol, runtime_checkable

from mailbrief.domain.calendar_planning import CalendarPlanningRequest, PlanningResponse


@runtime_checkable
class CalendarPlanningProvider(Protocol):
    """Proposes calendar changes from exactly the context in a CalendarPlanningRequest."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def planning_prompt_version(self) -> str: ...

    @property
    def privacy_notice(self) -> str: ...

    @property
    def requests_sent(self) -> int: ...

    async def credentials_available(self) -> bool: ...

    async def plan_calendar(self, request: CalendarPlanningRequest) -> PlanningResponse:
        """One logical call; the candidate comes back unchecked. Failures raise
        ``mailbrief.ports.errors`` types, which never contain calendar text."""
        ...
