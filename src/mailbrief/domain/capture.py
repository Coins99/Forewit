"""A bounded owner capture, independent of email, AI and organization."""

from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.actions import ACTION_NOTES_MAX_CHARS, ActionEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.common import DomainModel
from mailbrief.domain.drafts import DRAFT_BODY_MAX_CHARS, DRAFT_TITLE_MAX_CHARS, DraftEdit


class CaptureRequest(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, str_strip_whitespace=False)

    command_id: str
    kind: Literal["task", "note"]
    review: bool = False
    title: str = Field(default="", max_length=DRAFT_TITLE_MAX_CHARS, repr=False)
    body: str = Field(default="", max_length=DRAFT_BODY_MAX_CHARS, repr=False)

    @field_validator("command_id")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        return str(UUID(value))

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if self.kind == "task":
            if not self.title.strip() or len(self.body) > ACTION_NOTES_MAX_CHARS:
                raise ValueError("A task needs a title and at most 10,000 characters of notes.")
        elif not (self.title.strip() or self.body.strip()):
            raise ValueError("Enter a note title or some text.")
        return self

    def task_edit(self) -> ActionEdit:
        return ActionEdit(
            title=self.title,
            notes=self.body,
            ownership=ActionOwnership.MINE,
            person=None,
            priority=None,
            tags=(),
            effort=None,
            estimate_minutes=None,
            target_date=None,
        )

    def note_edit(self) -> DraftEdit:
        return DraftEdit(title=self.title, body=self.body)
