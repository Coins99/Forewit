"""Canonical workspace destinations. Routes contain identity, never copied record text."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.common import DomainModel
from mailbrief.domain.drafts import DraftKind

PublicId = Annotated[str, Field(min_length=36, max_length=36)]


class PageRoute(DomainModel):
    kind: Literal["page"] = "page"
    page: Literal["today", "tasks", "projects", "calendar", "notes", "mail"]


class TaskRoute(DomainModel):
    kind: Literal["task"] = "task"
    public_id: PublicId | None = None
    view: ActionFilter = ActionFilter.OPEN


class WritingRoute(DomainModel):
    kind: Literal["writing"] = "writing"
    public_id: PublicId
    draft_kind: DraftKind | None = None


class CalendarRoute(DomainModel):
    kind: Literal["calendar"] = "calendar"
    day: date
    # The calendar's typed entry keys: a local event, a work block or a cached Google event.
    entry: (
        tuple[Literal["event"], PublicId]
        | tuple[Literal["block"], PublicId]
        | tuple[Literal["google"], str, str]
        | None
    ) = None


class MailRoute(DomainModel):
    kind: Literal["mail"] = "mail"
    view: Literal["inbox", "current", "history", "saved", "drafts"] = "inbox"


class MessageRoute(DomainModel):
    kind: Literal["message"] = "message"
    account_email: str = Field(min_length=1, repr=False)
    message_id: str = Field(min_length=1, repr=False)
    web_link: str | None = Field(default=None, repr=False)
    in_inbox: bool = True


class BriefRoute(DomainModel):
    kind: Literal["brief"] = "brief"
    account_email: str = Field(min_length=1, repr=False)
    day: date


class ProjectRoute(DomainModel):
    kind: Literal["project"] = "project"
    public_id: PublicId


Route = Annotated[
    PageRoute
    | TaskRoute
    | WritingRoute
    | CalendarRoute
    | MailRoute
    | MessageRoute
    | BriefRoute
    | ProjectRoute,
    Field(discriminator="kind"),
]


class NavigationStamp(DomainModel):
    """A pending open belongs to one navigation generation and account context."""

    generation: int = Field(ge=0)
    account_email: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_account(self) -> "NavigationStamp":
        if self.account_email == "":
            raise ValueError("account context must be absent or nonempty")
        return self
