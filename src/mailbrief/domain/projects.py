"""Owner projects and optional life areas, independent of mailbox accounts."""

from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from mailbrief.domain.common import DomainModel, normalize_utc


class AreaEdit(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=120, repr=False)
    notes: str = Field(default="", max_length=10_000, repr=False)


class ProjectEdit(AreaEdit):
    area_public_id: str | None = Field(default=None, min_length=36, max_length=36)


class LifeArea(AreaEdit):
    public_id: str = Field(min_length=36, max_length=36)
    revision: int = Field(ge=1)
    created_at_utc: datetime
    updated_at_utc: datetime

    @field_validator("created_at_utc", "updated_at_utc")
    @classmethod
    def normalize_dates(cls, value: datetime) -> datetime:
        return normalize_utc(value)


class Project(ProjectEdit):
    public_id: str = Field(min_length=36, max_length=36)
    revision: int = Field(ge=1)
    created_at_utc: datetime
    updated_at_utc: datetime
    archived_at_utc: datetime | None = None
    area_name: str | None = Field(default=None, repr=False)

    @field_validator("created_at_utc", "updated_at_utc", "archived_at_utc")
    @classmethod
    def normalize_dates(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)
