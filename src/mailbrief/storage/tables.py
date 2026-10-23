"""SQLAlchemy table mappings for local MailBrief state."""

from datetime import date, datetime
from typing import TypedDict

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mailbrief.domain.actions import ESTIMATE_MAX_MINUTES, TAG_MAX_CHARS, ActionPriority
from mailbrief.domain.analysis import MAX_ANALYSIS_BATCH, DeadlineFields, DeadlinePrecision
from mailbrief.domain.briefs import AUTO_SEND_LIMIT_MAX
from mailbrief.domain.common import utc_now
from mailbrief.domain.preferences import (
    AI_BODY_CHARS_MAX,
    AI_OUTPUT_TOKENS_MAX,
    AI_OUTPUT_TOKENS_MIN,
    AI_REQUESTS_MAX,
    AI_TIMEOUT_MAX,
    AI_TIMEOUT_MIN,
    REFRESH_INTERVALS,
    SHORTLIST_LIMIT_MAX,
)
from mailbrief.domain.recurrence import RepeatFrequency
from mailbrief.storage.types import UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_PRECISION_CHECK = "deadline_precision IN ('none','unresolved','date','datetime')"
_OWNERSHIP_CHECK = "ownership IN ('mine','waiting_for')"
_DEADLINE_ORIGIN_CHECK = "deadline_origin IS NULL OR deadline_origin IN ('email','owner')"
_DRAFT_KIND_CHECK = "kind IN ('reply','email','note','message')"
_DRAFT_ORIGIN_CHECK = "origin IN ('created','edited','restored','generated')"
_DRAFT_TONE_CHECK = "tone IN ('neutral','warm','formal','direct')"
_DRAFT_LENGTH_CHECK = "length IN ('short','medium','long')"
_DEFAULT_TONE_CHECK = "draft_tone IN ('neutral','warm','formal','direct')"
_DEFAULT_LENGTH_CHECK = "draft_length IN ('short','medium','long')"
_FOLLOW_UP_CHECK = "follow_up_kind IN ('none','new_deadline','cancelled','delivered')"
_PROPOSAL_KIND_CHECK = "kind IN ('new_deadline','cancelled','delivered')"
_PROPOSAL_STATE_CHECK = "state IN ('pending','applied','dismissed')"
_PRIORITY_CHECK = (
    f"priority IS NULL OR priority IN ({', '.join(repr(item.value) for item in ActionPriority)})"
)
_REPEAT_FREQUENCY_CHECK = (
    f"frequency IN ({', '.join(repr(item.value) for item in RepeatFrequency)})"
)
_REFRESH_INTERVAL_CHECK = (
    "refresh_interval_minutes IS NULL OR refresh_interval_minutes IN "
    f"({', '.join(str(minutes) for minutes in REFRESH_INTERVALS)})"
)


class Base(DeclarativeBase):
    """Declarative base shared by mappings and Alembic metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DeadlineColumns:
    """The deadline columns of an analysis, suggestion, action or proposal (DeadlineFields);
    each of those tables also checks ``_PRECISION_CHECK``."""

    deadline_text: Mapped[str | None] = mapped_column(Text)
    deadline_precision: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="none",
        server_default="none",
    )
    deadline_date: Mapped[date | None] = mapped_column(Date)
    deadline_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deadline_timezone: Mapped[str | None] = mapped_column(String(128))


class DeadlineValues(TypedDict):
    """A row's deadline as DeadlineFields takes it."""

    deadline_text: str | None
    deadline_precision: DeadlinePrecision
    deadline_date: date | None
    deadline_at_utc: datetime | None
    deadline_timezone: str | None


class DeadlineColumnValues(TypedDict):
    """A model's deadline as DeadlineColumns stores it."""

    deadline_text: str | None
    deadline_precision: str
    deadline_date: date | None
    deadline_at_utc: datetime | None
    deadline_timezone: str | None


def deadline_values(row: DeadlineColumns) -> DeadlineValues:
    """The deadline stored on ``row``, for a domain model's constructor."""
    return DeadlineValues(
        deadline_text=row.deadline_text,
        deadline_precision=DeadlinePrecision(row.deadline_precision),
        deadline_date=row.deadline_date,
        deadline_at_utc=row.deadline_at_utc,
        deadline_timezone=row.deadline_timezone,
    )


def deadline_columns(model: DeadlineFields) -> DeadlineColumnValues:
    """``model``'s deadline, for a row's columns."""
    return DeadlineColumnValues(
        deadline_text=model.deadline_text,
        deadline_precision=model.deadline_precision.value,
        deadline_date=model.deadline_date,
        deadline_at_utc=model.deadline_at_utc,
        deadline_timezone=model.deadline_timezone,
    )


class AccountTable(Base):
    """One connected provider account."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uq_accounts_provider_identity",
        ),
        Index("ix_accounts_email_address", "email_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    account_addresses: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_sync_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MessageTable(Base):
    """Normalized provider message metadata; full bodies are never stored."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "provider_message_id",
            name="uq_messages_account_provider_message",
        ),
        Index("ix_messages_account_received", "account_id", "received_at_utc"),
        Index("ix_messages_account_rank", "account_id", "rank_score"),
        Index("ix_messages_account_conversation", "account_id", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    internet_message_id: Mapped[str | None] = mapped_column(String(998))
    conversation_id: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_name: Mapped[str | None] = mapped_column(String(255))
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    to_recipients_json: Mapped[list[dict[str, str | None]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    importance: Mapped[str] = mapped_column(String(16), nullable=False)
    is_in_inbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    # Sent from this account; cached only inside tracked threads (ADR 0015).
    is_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # When the owner unchecked this message in a review that had picked it (ADR 0017).
    review_declined_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # When the owner wanted this message in a brief the automatic selection didn't give it:
    # picked in a review, or marked "Should have been in my brief" (ADR 0020).
    review_wanted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Gmail's category tab, star and mailing-list headers (ADR 0020); NULL category is unknown.
    category: Mapped[str | None] = mapped_column(String(16))
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    is_bulk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    has_attachments: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    body_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    web_link: Mapped[str] = mapped_column(Text, nullable=False)
    rank_score: Mapped[int | None] = mapped_column(Integer)
    rank_reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    synced_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AnalysisTable(DeadlineColumns, Base):
    """Cached structured AI result for one versioned message input."""

    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "input_hash",
            "provider",
            "model",
            "prompt_version",
            "schema_version",
            name="uq_analyses_cache_identity",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint(_PRECISION_CHECK, name="deadline_precision_known"),
        CheckConstraint(_FOLLOW_UP_CHECK, name="follow_up_kind_known"),
        Index("ix_analyses_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    action_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    analyzed_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    # The follow-up signal (ADR 0016); rows from before schema 7 read "none".
    follow_up_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    follow_up_evidence: Mapped[str | None] = mapped_column(String(160))


class DigestTable(Base):
    """One daily brief for one account and local date."""

    __tablename__ = "digests"
    __table_args__ = (
        UniqueConstraint("account_id", "local_date", name="uq_digests_account_local_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    # Brief coverage; every column is NULL for briefs saved before M4.
    sync_complete: Mapped[bool | None] = mapped_column(Boolean)
    shortlisted_count: Mapped[int | None] = mapped_column(Integer)
    analyzed_count: Mapped[int | None] = mapped_column(Integer)
    reused_count: Mapped[int | None] = mapped_column(Integer)
    failed_count: Mapped[int | None] = mapped_column(Integer)
    skipped_count: Mapped[int | None] = mapped_column(Integer)
    # Messages an automatic run left for the next review (ADR 0017); 0 for older briefs.
    deferred_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    ai_provider: Mapped[str | None] = mapped_column(String(64))
    ai_model: Mapped[str | None] = mapped_column(String(128))


class AIConsentTable(Base):
    """Per-account consent to send minimized email content to one AI provider."""

    __tablename__ = "ai_consents"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "provider",
            "disclosure_version",
            name="uq_ai_consents_scope",
        ),
        CheckConstraint(
            f"auto_send_limit BETWEEN 0 AND {AUTO_SEND_LIMIT_MAX}", name="auto_send_limit_range"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    disclosure_version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # How many messages an automatic run may send without asking, and since when (ADR 0017).
    # It belongs to this consent: revoking the consent, or a new disclosure version, ends it.
    auto_send_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    auto_send_granted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DigestItemTable(Base):
    """Ordered membership of a message and analysis in a digest."""

    __tablename__ = "digest_items"
    __table_args__ = (
        UniqueConstraint("digest_id", "position", name="uq_digest_items_digest_position"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
    )

    digest_id: Mapped[int] = mapped_column(
        ForeignKey("digests.id", ondelete="CASCADE"),
        primary_key=True,
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)


class SyncRunTable(Base):
    """Auditable state of one mailbox synchronization attempt."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("range_end_utc > range_start_utc", name="valid_range"),
        CheckConstraint("page_count >= 0", name="page_count_nonnegative"),
        CheckConstraint("message_count >= 0", name="message_count_nonnegative"),
        Index("ix_sync_runs_account_started", "account_id", "started_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    range_start_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    range_end_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sanitized_error_code: Mapped[str | None] = mapped_column(String(128))
    inbox_continuation: Mapped[str | None] = mapped_column(Text)
    failed_message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class InboxScanMessageTable(Base):
    """Transient observations of one resumable Inbox scan."""

    __tablename__ = "inbox_scan_messages"
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )


class LifeAreaTable(Base):
    __tablename__ = "life_areas"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_life_areas_public_id"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}


class ProjectTable(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_projects_public_id"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    area_public_id: Mapped[str | None] = mapped_column(
        ForeignKey("life_areas.public_id", ondelete="SET NULL")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    archived_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}


class ActionSeriesTable(Base):
    """A repeating task's series (B05): its rule, and the template its later occurrences are
    made from. Its occurrences are ordinary actions; the series has no account and is never
    deleted with one of them, so their history stays together."""

    __tablename__ = "action_series"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_action_series_public_id"),
        CheckConstraint(_REPEAT_FREQUENCY_CHECK, name="frequency_known"),
        CheckConstraint(
            "CASE WHEN frequency = 'weekly' THEN weekday_mask BETWEEN 1 AND 127"
            " ELSE weekday_mask = 0 END",
            name="weekdays_weekly",
        ),
        CheckConstraint(
            "(frequency = 'monthly_day') = (month_day IS NOT NULL)"
            " AND (month_day IS NULL OR month_day BETWEEN 1 AND 31)",
            name="month_day_monthly",
        ),
        CheckConstraint(_OWNERSHIP_CHECK, name="ownership_known"),
        CheckConstraint(_PRIORITY_CHECK, name="priority_known"),
        CheckConstraint(
            f"estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND {ESTIMATE_MAX_MINUTES}",
            name="estimate_minutes_range",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    weekday_mask: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    month_day: Mapped[int | None] = mapped_column(Integer)
    end_date: Mapped[date | None] = mapped_column(Date)
    # The template: what a later occurrence starts with. Its date comes from the rule.
    project_public_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.public_id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    ownership: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(16))
    effort: Mapped[str | None] = mapped_column(String(16))
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())  # Repeats stopped.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}


class ActionTable(DeadlineColumns, Base):
    """An action the owner accepted.

    Actions have no account foreign key: they belong to the owner and outlive account or
    message deletion through the snapshots in their sources.
    """

    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_actions_public_id"),
        CheckConstraint("status IN ('open','completed','skipped')", name="status_known"),
        CheckConstraint(_OWNERSHIP_CHECK, name="ownership_known"),
        CheckConstraint(_PRECISION_CHECK, name="deadline_precision_known"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            f"estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND {ESTIMATE_MAX_MINUTES}",
            name="estimate_minutes_range",
        ),
        CheckConstraint(_PRIORITY_CHECK, name="priority_known"),
        CheckConstraint(_DEADLINE_ORIGIN_CHECK, name="deadline_origin_known"),
        CheckConstraint(
            "(deadline_origin IS NULL) = (deadline_precision = 'none')", name="deadline_origin_set"
        ),
        CheckConstraint(
            "stated_deadline_precision IN ('none','unresolved','date','datetime')",
            name="stated_deadline_precision_known",
        ),
        # One occurrence per series and date, so making the next one twice makes one.
        UniqueConstraint("series_id", "occurrence_date", name="uq_actions_series_occurrence"),
        Index("ix_actions_status", "status"),
        Index("ix_actions_follows_action_id", "follows_action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_public_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.public_id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    ownership: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(16))
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)  # The owner's, never the AI's.
    priority: Mapped[str | None] = mapped_column(String(16))  # The owner's, never the AI's.
    suggested_target_date: Mapped[date | None] = mapped_column(Date)
    target_reason: Mapped[str | None] = mapped_column(String(32))
    target_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # The owner's "seen" watermark for later messages in the action's threads (ADR 0015).
    thread_seen_until_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # A repeating task's series and this occurrence's own date in it (B05), and the
    # occurrence whose completion or skip made this one.
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_series.id", ondelete="SET NULL")
    )
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    follows_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("actions.id", ondelete="SET NULL")
    )
    # Who set the deadline (B06): 'email' or 'owner', NULL exactly when there is none.
    deadline_origin: Mapped[str | None] = mapped_column(String(8))
    # The email's deadline the owner last replaced or cleared (B06), stored as the deadline
    # columns store one; 'none' when there is none.
    stated_deadline_text: Mapped[str | None] = mapped_column(Text)
    stated_deadline_precision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    stated_deadline_date: Mapped[date | None] = mapped_column(Date)
    stated_deadline_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stated_deadline_timezone: Mapped[str | None] = mapped_column(String(128))


class ActionTagTable(Base):
    """One of the owner's tags on an action. A tag is its name: tasks share a tag when
    their ``name_key`` (``tag_key``) matches, so there is no tag record to keep in step."""

    __tablename__ = "action_tags"
    __table_args__ = (
        UniqueConstraint("action_id", "name_key", name="uq_action_tags_name"),
        CheckConstraint(f"length(name) BETWEEN 1 AND {TAG_MAX_CHARS}", name="name_length"),
        Index("ix_action_tags_name_key", "name_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_key: Mapped[str] = mapped_column(Text, nullable=False)


class ActionActivityTable(Base):
    """Append-only owner activity; saved in the transaction that changes the action."""

    __tablename__ = "action_activity"
    __table_args__ = (
        UniqueConstraint("action_id", "revision", name="uq_action_activity_revision"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_action_activity_action_id", "action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(16))
    previous_completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    undo_of_revision: Mapped[int | None] = mapped_column(Integer)
    previous_source_count: Mapped[int | None] = mapped_column(Integer)
    project_public_id: Mapped[str | None] = mapped_column(String(36))
    area_public_id: Mapped[str | None] = mapped_column(String(36))
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ActionSuggestionTable(DeadlineColumns, Base):
    """One validated action suggested by a cached analysis, at its position."""

    __tablename__ = "action_suggestions"
    __table_args__ = (
        UniqueConstraint("analysis_id", "position", name="uq_action_suggestions_position"),
        UniqueConstraint("analysis_id", "fingerprint", name="uq_action_suggestions_fingerprint"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(_OWNERSHIP_CHECK, name="ownership_known"),
        CheckConstraint(_PRECISION_CHECK, name="deadline_precision_known"),
        # A deleted suggestion's ID is never given to another, so a stale ID fails.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    ownership: Mapped[str] = mapped_column(String(16), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(16))
    suggested_target_date: Mapped[date | None] = mapped_column(Date)
    target_reason: Mapped[str | None] = mapped_column(String(32))
    steps_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class ActionStepTable(Base):
    """One ordered step of an accepted action's plan."""

    __tablename__ = "action_steps"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
        Index("ix_action_steps_action_id", "action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    done: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    done_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ActionSourceTable(Base):
    """A snapshot of a message an action came from; it stays when the message goes."""

    __tablename__ = "action_sources"
    __table_args__ = (
        UniqueConstraint("action_id", "provider_message_id", name="uq_action_sources_message"),
        Index("ix_action_sources_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    web_link: Mapped[str] = mapped_column(Text, nullable=False)
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # The message's provider, account and thread, so its thread can be tracked (ADR 0015).
    # NULL for sources whose email had left local mail before revision 0010.
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_account_id: Mapped[str | None] = mapped_column(String(255))
    provider_thread_id: Mapped[str | None] = mapped_column(String(512))


class SuggestionDecisionTable(Base):
    """The owner's decision on one suggestion, kept per provider message and title fingerprint.

    Decisions have no message or account foreign key: they are keyed by the provider
    account and message IDs, so they survive message, cache and account deletion.
    """

    __tablename__ = "suggestion_decisions"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_account_id",
            "provider_message_id",
            "fingerprint",
            name="uq_suggestion_decisions_identity",
        ),
        CheckConstraint("decision IN ('accepted','dismissed')", name="decision_known"),
        Index("ix_suggestion_decisions_action_id", "action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id", ondelete="SET NULL"))
    decided_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ActionProposalTable(DeadlineColumns, Base):
    """A later email's proposed update to an action, applied only by the owner (ADR 0016).

    It keeps a snapshot of the email, so it outlives the cached message. ``previous_json``,
    ``source_added`` and ``applied_revision`` record what applying changed, for Undo.
    """

    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint(
            "action_id", "provider_message_id", "kind", name="uq_action_proposals_identity"
        ),
        CheckConstraint(_PROPOSAL_KIND_CHECK, name="kind_known"),
        CheckConstraint(_PROPOSAL_STATE_CHECK, name="state_known"),
        CheckConstraint(_PRECISION_CHECK, name="deadline_precision_known"),
        Index("ix_action_proposals_action_id", "action_id"),
        # A deleted proposal's ID is never given to another, so a stale ID fails.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    suggested_target_date: Mapped[date | None] = mapped_column(Date)
    target_reason: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    web_link: Mapped[str] = mapped_column(Text, nullable=False)
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    previous_json: Mapped[dict[str, str | None] | None] = mapped_column(JSON)
    source_added: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    decided_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DraftTable(Base):
    """A draft or note the owner is writing (ADR 0012).

    Drafts have no account foreign key: they belong to the owner. Their action link is
    SET NULL, and the action's title is kept as a snapshot, so they outlive the action.
    """

    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_drafts_public_id"),
        CheckConstraint(_DRAFT_KIND_CHECK, name="kind_known"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_drafts_updated_at_utc", "updated_at_utc"),
        Index("ix_drafts_action_id", "action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    to_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cc_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id", ondelete="SET NULL"))
    action_title: Mapped[str | None] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class DraftVersionTable(Base):
    """One saved version of a draft's text; numbers only grow, so pruning leaves gaps."""

    __tablename__ = "draft_versions"
    __table_args__ = (
        UniqueConstraint("draft_id", "number", name="uq_draft_versions_number"),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint(_DRAFT_ORIGIN_CHECK, name="origin_known"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    to_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cc_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DraftSourceTable(Base):
    """A snapshot of an email a draft came from; it stays when the message goes."""

    __tablename__ = "draft_sources"
    __table_args__ = (
        UniqueConstraint("draft_id", "provider_message_id", name="uq_draft_sources_message"),
        Index("ix_draft_sources_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    web_link: Mapped[str] = mapped_column(Text, nullable=False)
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OwnerConsentTable(Base):
    """The owner's consent to one use of an AI provider, such as drafting (ADR 0013).

    It mirrors ``ai_consents`` without an account: drafting consent belongs to the owner.
    A consent is active until it is revoked.
    """

    __tablename__ = "owner_consents"
    __table_args__ = (
        UniqueConstraint("provider", "scope", "disclosure_version", name="uq_owner_consents_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    disclosure_version: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DraftGenerationTable(Base):
    """How a generated draft version was made; it goes when its version is pruned."""

    __tablename__ = "draft_generations"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_draft_generations_version"),
        CheckConstraint(_DRAFT_TONE_CHECK, name="tone_known"),
        CheckConstraint(_DRAFT_LENGTH_CHECK, name="length_known"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("draft_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    tone: Mapped[str] = mapped_column(String(16), nullable=False)
    length: Mapped[str] = mapped_column(String(16), nullable=False)
    parts_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    missing_context_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


def _range_check(column: str, low: int, high: int) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR {column} BETWEEN {low} AND {high}", name=f"{column}_range"
    )


class OwnerPreferencesTable(Base):
    """The owner's preferences: one revisioned row, shared by the desktop and CLI (ADR 0014).

    No row means the defaults. A NULL AI limit means "use the default".
    """

    __tablename__ = "owner_preferences"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint(
            f"shortlist_limit BETWEEN 1 AND {SHORTLIST_LIMIT_MAX}", name="shortlist_limit_range"
        ),
        CheckConstraint(_DEFAULT_TONE_CHECK, name="draft_tone_known"),
        CheckConstraint(_DEFAULT_LENGTH_CHECK, name="draft_length_known"),
        CheckConstraint(_REFRESH_INTERVAL_CHECK, name="refresh_interval_known"),
        _range_check("ai_batch_size", 1, MAX_ANALYSIS_BATCH),
        _range_check("ai_body_character_limit", 1, AI_BODY_CHARS_MAX),
        _range_check("ai_max_output_tokens", AI_OUTPUT_TOKENS_MIN, AI_OUTPUT_TOKENS_MAX),
        _range_check("ai_max_requests_per_run", 1, AI_REQUESTS_MAX),
        _range_check("ai_timeout_seconds", AI_TIMEOUT_MIN, AI_TIMEOUT_MAX),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time_zone: Mapped[str | None] = mapped_column(String(64))
    shortlist_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SHORTLIST_LIMIT_MAX, server_default=text("10")
    )
    excluded_senders_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Mail from these ranks higher (ADR 0020).
    important_senders_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    draft_tone: Mapped[str] = mapped_column(
        String(16), nullable=False, default="neutral", server_default="neutral"
    )
    draft_length: Mapped[str] = mapped_column(
        String(16), nullable=False, default="medium", server_default="medium"
    )
    ai_batch_size: Mapped[int | None] = mapped_column(Integer)
    ai_body_character_limit: Mapped[int | None] = mapped_column(Integer)
    ai_max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    ai_max_requests_per_run: Mapped[int | None] = mapped_column(Integer)
    ai_timeout_seconds: Mapped[float | None] = mapped_column(Float)
    # Automatic refresh while the desktop is open (ADR 0017): on launch, and every N minutes.
    refresh_on_launch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


_EVENT_TIMING_CHECK = (
    "(all_day = 1 AND start_date IS NOT NULL AND last_date IS NOT NULL"
    " AND last_date >= start_date AND starts_at_utc IS NULL AND ends_at_utc IS NULL)"
    " OR (all_day = 0 AND starts_at_utc IS NOT NULL AND ends_at_utc IS NOT NULL"
    " AND ends_at_utc > starts_at_utc AND start_date IS NULL AND last_date IS NULL)"
)


_GOOGLE_TIMING_CHECK = (
    "(all_day = 1 AND start_date IS NOT NULL AND last_date IS NOT NULL"
    " AND last_date >= start_date AND starts_at_utc IS NULL AND ends_at_utc IS NULL)"
    " OR (all_day = 0 AND starts_at_utc IS NOT NULL AND ends_at_utc IS NOT NULL"
    " AND ends_at_utc >= starts_at_utc AND start_date IS NULL AND last_date IS NULL)"
)


class CalendarEventTable(Base):
    """One of the owner's calendar events (ADR 0018).

    Events have no account foreign key: they belong to the owner. A timed event keeps two
    UTC instants and the zone it was entered in; an all-day event keeps its first and last
    local days. The CHECK keeps the two shapes apart. Rows change only through ORM objects.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_calendar_events_public_id"),
        CheckConstraint(_EVENT_TIMING_CHECK, name="timing_shape"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_calendar_events_starts_at_utc", "starts_at_utc"),
        Index("ix_calendar_events_start_date", "start_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    starts_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ends_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    start_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    deleted_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class GoogleCalendarTable(Base):
    """A calendar shown in the owner's Google Calendar, cached to show its events (ADR 0018).

    Provider data, not the owner's: replaced by every sync and removed on disconnect; one the
    owner unticks in Google goes with its events. The synced days and time are when a sync
    last dealt with it, in the owner's zone then: its events were read, or the read failed
    and it kept what it had.
    """

    __tablename__ = "google_calendars"
    __table_args__ = (UniqueConstraint("calendar_id", name="uq_google_calendars_calendar_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_from: Mapped[date | None] = mapped_column(Date)
    synced_until: Mapped[date | None] = mapped_column(Date)
    synced_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class GoogleEventTable(Base):
    """One cached Google event (ADR 0018): what the Calendar page shows, nothing more.

    Descriptions, locations, guests and conference details are never requested, so never
    stored.
    """

    __tablename__ = "google_events"
    __table_args__ = (
        UniqueConstraint("calendar_row_id", "event_id", name="uq_google_events_calendar_row_id"),
        CheckConstraint(_GOOGLE_TIMING_CHECK, name="timing_shape"),
        Index("ix_google_events_starts_at_utc", "starts_at_utc"),
        Index("ix_google_events_start_date", "start_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_row_id: Mapped[int] = mapped_column(
        ForeignKey("google_calendars.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    starts_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ends_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime())
    start_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    web_link: Mapped[str | None] = mapped_column(Text)
    fetched_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
