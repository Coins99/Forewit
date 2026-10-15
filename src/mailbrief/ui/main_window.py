"""Responsive desktop workflow with explicit metadata review and cloud consent."""

import asyncio
import contextlib
import itertools
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Final, Protocol, assert_never
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import (
    Action,
    ActionEdit,
    ActionFilter,
    ActionProposal,
    StepEdit,
    ThreadLink,
)
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.backup import BackupMetadata
from mailbrief.domain.briefs import (
    AutoSendStatus,
    BriefRunResult,
    BriefStatus,
    TransmissionPreview,
)
from mailbrief.domain.cached_mail import CachedAccount, CachedMailPage, InboxPage
from mailbrief.domain.calendar_planning import (
    PLANNING_DAYS,
    PlanningOptions,
    PlanningOutcome,
    PlanningPart,
    PlanningPreview,
    PlanningStatus,
)
from mailbrief.domain.digests import (
    DailyDigest,
    DigestStatus,
    SavedBriefSummary,
    SyncProgress,
    SyncResult,
    SyncStatus,
)
from mailbrief.domain.drafting import (
    DraftContextPart,
    DraftingOptions,
    DraftingOutcome,
    DraftingPreview,
    DraftingStatus,
)
from mailbrief.domain.drafts import (
    KIND_NAMES,
    Draft,
    DraftEdit,
    DraftKind,
    DraftSummary,
    DraftVersion,
    DraftVersionInfo,
    placeholders,
    still_to_fill,
)
from mailbrief.domain.events import (
    AppliedBatch,
    CalendarEvent,
    CalendarRange,
    EventChange,
    EventEdit,
)
from mailbrief.domain.google_calendar import CalendarSyncResult, GoogleEvent, is_calendar_link
from mailbrief.domain.messages import RankedMessage
from mailbrief.domain.preferences import OwnerPreferences, PreferencesEdit
from mailbrief.domain.projects import AreaEdit, LifeArea, Project
from mailbrief.domain.search import SearchHit, SearchKind, SearchResults
from mailbrief.domain.today import DaySummary
from mailbrief.errors import ConfigurationError
from mailbrief.infra.files import write_text_atomically
from mailbrief.ports.calendar import CalendarNotAllowedError
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.services.actions import (
    COMPLETED_LIST_LIMIT,
    AcceptedInto,
    ActionConflictError,
    ActionNotFoundError,
    DecisionSnapshot,
    SuggestionNotFoundError,
)
from mailbrief.services.brief import (
    ConsentGate,
    ShortlistGate,
    disclosure_lines,
    needs_review_sentence,
)
from mailbrief.services.calendar import resolve_timezone
from mailbrief.services.calendar_planning import PlanningGate, PreparedPlan, overlaps_for
from mailbrief.services.consent import NO_CONSENT
from mailbrief.services.drafting import (
    DraftingContextError,
    DraftingGate,
    DraftingPlan,
)
from mailbrief.services.drafting import disclosure_lines as drafting_disclosure_lines
from mailbrief.services.drafts import DraftConflictError, DraftNotFoundError, SourceNotFoundError
from mailbrief.services.events import EventConflictError, EventNotFoundError
from mailbrief.services.google_calendar import needs_sync
from mailbrief.services.history import BriefDateError
from mailbrief.services.preferences import (
    PreferencesConflictError,
    PreferencesUnavailableError,
    owner_zone,
)
from mailbrief.services.projects import ProjectConflictError
from mailbrief.services.proposals import ProposalNotFoundError
from mailbrief.services.ranking import (
    DECLINED_TEXT,
    MAX_SHORTLIST_SIZE,
    OUTSIDE_REPLY_TEXT,
    reason_text,
)
from mailbrief.ui.action_editor import ActionEditor
from mailbrief.ui.actions_view import (
    COMPLETE,
    DELETE,
    EDIT,
    PROPOSALS,
    REOPEN,
    SEEN,
    ActionsPanel,
)
from mailbrief.ui.appearance import (
    Appearance,
    AppearanceStore,
    AppearanceWriter,
    ModeChoice,
    theme_mode,
)
from mailbrief.ui.auto_send_view import AutoSendDialog
from mailbrief.ui.brief_detail import ACCEPT, APPLY, DISMISS, is_gmail_link
from mailbrief.ui.cached_view import CachedMailDialog
from mailbrief.ui.calendar_layout import CalendarView, month_days, month_start
from mailbrief.ui.calendar_view import ALLOW as ALLOW_CALENDAR
from mailbrief.ui.calendar_view import DELETE as DELETE_EVENT
from mailbrief.ui.calendar_view import (
    GOOGLE_ASK,
    GOOGLE_OFF,
    GOOGLE_ON,
    OPEN_GOOGLE,
    CalendarPanel,
    week_start,
)
from mailbrief.ui.calendar_view import NEW as NEW_EVENT
from mailbrief.ui.calendar_view import OPEN as OPEN_EVENT
from mailbrief.ui.calendar_view import REFRESH as REFRESH_CALENDAR
from mailbrief.ui.data_view import DataDialog
from mailbrief.ui.deadline_text import day_text, moment_text, month_day
from mailbrief.ui.diagnostics import (
    configuration_guidance,
    error_guidance,
    log_automatic_run,
    log_failure,
)
from mailbrief.ui.dialog_guard import DialogGuard
from mailbrief.ui.draft_editor import DraftEditor
from mailbrief.ui.drafts_view import DELETE as DELETE_DRAFT
from mailbrief.ui.drafts_view import NEW as NEW_DRAFT
from mailbrief.ui.drafts_view import OPEN as OPEN_DRAFT
from mailbrief.ui.drafts_view import DraftsPanel
from mailbrief.ui.event_editor import EventEditor
from mailbrief.ui.glass.accessibility import GlassAccessibility
from mailbrief.ui.glass.preferences import Motion, Transparency
from mailbrief.ui.hairline import HairlineFrame
from mailbrief.ui.history_view import NEEDS_CONNECTION, BriefHistoryPanel
from mailbrief.ui.inbox_view import INBOX_PAGE_SIZE, InboxPanel
from mailbrief.ui.inbox_view import INCOMPLETE as INBOX_INCOMPLETE
from mailbrief.ui.labels import button_label, outline_button, plain_label, wrap_label
from mailbrief.ui.palette import CommandPalette
from mailbrief.ui.planning_dialog import PlanningDialog
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.preferences_view import AUTO_DISCONNECTED, region_zones
from mailbrief.ui.projects_view import ProjectsDialog
from mailbrief.ui.proposals_view import ProposalsDialog
from mailbrief.ui.run_view import ROW_ROLE, ShortlistDelegate, shortlist_row
from mailbrief.ui.scheduler import RefreshScheduler
from mailbrief.ui.settings_view import SIGN_IN_UNSAVED, SettingsDialog
from mailbrief.ui.theme import (
    CAPTION_PX,
    SMALL_PX,
    TITLE_PX,
    ColorScheme,
    ThemeMode,
    apply_theme,
    current_mode,
    current_scheme,
    system_mode,
)
from mailbrief.ui.today_view import DayPanel
from mailbrief.ui.workspace import PAGES, ThreePaneWorkspace


class DesktopBackend(Protocol):
    def reset_inbox_browsing(self) -> None: ...

    async def sync_inbox(
        self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
    ) -> SyncResult: ...
    async def inbox_messages(
        self, account_email: str, offset: int = 0, *, query: str = "", cached_page: bool = False
    ) -> InboxPage: ...

    async def browse_inbox(
        self,
        account_email: str,
        cancel: asyncio.Event,
        *,
        load_more: bool = False,
        query: str = "",
    ) -> InboxPage: ...
    async def search(self, text: str, account_email: str | None) -> SearchResults: ...
    async def inbox_position(self, account_email: str, provider_message_id: str) -> int | None: ...
    async def set_wanted(
        self, account_email: str, provider_message_id: str, wanted: bool
    ) -> None: ...
    async def cached_accounts(self) -> tuple[CachedAccount, ...]: ...
    async def cached_messages(
        self, account_id: int, day: date, offset: int = 0
    ) -> CachedMailPage: ...
    async def get_preferences(self) -> DesktopPreferences: ...
    async def save_preferences(self, preferences: DesktopPreferences) -> None: ...
    async def get_owner_preferences(self) -> OwnerPreferences: ...
    async def save_owner_preferences(
        self, edit: PreferencesEdit, revision: int
    ) -> OwnerPreferences: ...
    async def reset_owner_preferences(self) -> OwnerPreferences: ...
    async def save_key(self, key: SecretStr) -> None: ...
    async def remove_key(self) -> None: ...
    async def revoke_consent(self) -> int: ...
    async def load_saved(self) -> DailyDigest | None: ...
    async def connect(self, *, silent_only: bool) -> str: ...
    async def ai_status(self) -> str: ...
    async def disconnect(self) -> None: ...
    async def generate(
        self,
        gate: ConsentGate,
        review: ShortlistGate,
        cancel: asyncio.Event,
        progress: Callable[[SyncProgress], None],
        local_date: date | None = None,
    ) -> BriefRunResult: ...
    async def generate_automatic(
        self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
    ) -> BriefRunResult: ...
    async def auto_send_status(self, account_email: str | None) -> AutoSendStatus | None: ...
    async def set_auto_send(
        self, limit: int, account_email: str | None
    ) -> AutoSendStatus | None: ...
    async def list_briefs(self) -> tuple[SavedBriefSummary, ...]: ...
    async def load_brief(self, account_email: str, local_date: date) -> DailyDigest | None: ...
    async def missed_days(self, account_email: str) -> tuple[date, ...]: ...
    async def close(self) -> None: ...
    async def list_actions(self, view: ActionFilter) -> tuple[Action, ...]: ...
    async def accept_suggestion(self, suggestion_id: int) -> Action: ...
    async def brief_links(self, digest: DailyDigest) -> dict[str, tuple[ThreadLink, ...]]: ...
    async def brief_proposals(
        self, digest: DailyDigest
    ) -> dict[str, tuple[ActionProposal, ...]]: ...
    async def apply_proposal(self, proposal_id: int, revision: int) -> Action: ...
    async def undo_apply_proposal(self, proposal_id: int, revision: int) -> Action: ...
    async def dismiss_proposal(self, proposal_id: int) -> None: ...
    async def restore_proposal(self, proposal_id: int) -> None: ...
    async def accept_into(
        self, suggestion_id: int, public_id: str, revision: int
    ) -> AcceptedInto: ...
    async def undo_accept_into(
        self,
        suggestion_id: int,
        public_id: str,
        revision: int,
        remove_source: bool,
        *,
        previous: DecisionSnapshot,
    ) -> Action: ...
    async def mark_thread_seen(self, public_id: str, revision: int) -> Action: ...
    async def dismiss_suggestion(self, suggestion_id: int) -> None: ...
    async def restore_suggestion(self, suggestion_id: int) -> None: ...
    async def unaccept_action(self, public_id: str, revision: int) -> None: ...
    async def list_projects(self) -> tuple[Project, ...]: ...
    async def list_areas(self) -> tuple[LifeArea, ...]: ...
    async def list_organization(
        self,
    ) -> tuple[tuple[Project, ...], tuple[LifeArea, ...], tuple[str, ...]]: ...
    async def save_organization(
        self, kind: str, public_id: str | None, revision: int | None, edit: AreaEdit
    ) -> Project | LifeArea: ...
    async def archive_project(self, public_id: str, revision: int, archived: bool) -> Project: ...
    async def delete_area(self, public_id: str, revision: int) -> None: ...
    async def list_grouped_actions(
        self, view: ActionFilter, group: str, group_id: str | None
    ) -> tuple[Action, ...]: ...
    async def count_grouped_actions(
        self, view: ActionFilter, group: str, group_id: str | None
    ) -> int: ...
    async def create_action(self, edit: ActionEdit, steps: Sequence[StepEdit]) -> Action: ...
    async def undo_action_status(self, public_id: str, revision: int) -> Action: ...
    async def save_action(
        self,
        public_id: str,
        revision: int,
        edit: ActionEdit,
        steps: Sequence[StepEdit] | None = None,
    ) -> Action: ...
    async def complete_action(self, public_id: str, revision: int) -> Action: ...
    async def reopen_action(self, public_id: str, revision: int) -> Action: ...
    async def delete_action(self, public_id: str, revision: int) -> None: ...
    async def restore_action(self, public_id: str) -> Action: ...
    async def count_actions(self, view: ActionFilter) -> int: ...
    async def list_drafts(self) -> tuple[DraftSummary, ...]: ...
    async def get_draft(self, public_id: str) -> Draft: ...
    async def create_draft(self, kind: DraftKind) -> Draft: ...
    async def create_reply_draft(self, account_email: str, provider_message_id: str) -> Draft: ...
    async def create_draft_for_action(self, public_id: str, kind: DraftKind) -> Draft: ...
    async def autosave_draft(self, public_id: str, revision: int, edit: DraftEdit) -> Draft: ...
    async def checkpoint_draft(self, public_id: str, revision: int) -> DraftVersionInfo | None: ...
    async def draft_versions(self, public_id: str) -> tuple[DraftVersionInfo, ...]: ...
    async def draft_version(self, public_id: str, number: int) -> DraftVersion: ...
    async def restore_draft_version(self, public_id: str, revision: int, number: int) -> Draft: ...
    async def save_draft_as_new(self, public_id: str, edit: DraftEdit) -> Draft: ...
    async def delete_draft(self, public_id: str, revision: int) -> None: ...
    async def restore_draft(self, public_id: str) -> Draft: ...
    async def drafting_ready(self) -> bool: ...
    async def available_drafting_parts(self, public_id: str) -> frozenset[DraftContextPart]: ...
    async def prepare_drafting(self, public_id: str, options: DraftingOptions) -> DraftingPlan: ...
    async def generate_draft(
        self, plan: DraftingPlan, gate: DraftingGate, cancel: asyncio.Event
    ) -> DraftingOutcome: ...

    # The calendar (ADR 0018): local data only.
    async def calendar_range(
        self, first_day: date, last_day: date, zone: ZoneInfo
    ) -> CalendarRange: ...
    async def today_summary(self, day: date, zone: ZoneInfo, now: datetime) -> DaySummary: ...
    async def create_event(self, edit: EventEdit) -> CalendarEvent: ...
    async def save_event(self, public_id: str, revision: int, edit: EventEdit) -> CalendarEvent: ...
    async def delete_event(self, public_id: str, revision: int) -> None: ...
    async def restore_event(self, public_id: str) -> CalendarEvent: ...
    # Google Calendar, read-only, over the one Google sign-in (ADR 0019).
    async def calendar_allowed(self) -> bool | None: ...
    async def allow_calendar(self) -> bool: ...
    async def sync_calendar(self, around: date, zone: ZoneInfo) -> CalendarSyncResult: ...
    # Plan with AI (ADR 0018, phase 3b).
    async def prepare_planning(
        self, options: PlanningOptions, first_day: date, zone: ZoneInfo
    ) -> PreparedPlan: ...
    async def generate_planning(
        self, plan: PreparedPlan, gate: PlanningGate, cancel: asyncio.Event
    ) -> PlanningOutcome: ...
    async def apply_calendar_changes(self, changes: Sequence[EventChange]) -> AppliedBatch: ...
    async def undo_calendar_changes(self, batch: AppliedBatch) -> None: ...


# Raised when the brief, an action or a draft changed since it was shown; the window reloads.
_STALE = (
    ActionConflictError,
    ActionNotFoundError,
    SuggestionNotFoundError,
    ProposalNotFoundError,
    DraftConflictError,
    DraftNotFoundError,
    EventConflictError,
    EventNotFoundError,
)
# Raised when the open draft changed elsewhere; the editor keeps the text.
_DRAFT_STALE = (DraftConflictError, DraftNotFoundError)
_PICK_ONE = "Select at least one message to analyze, or Cancel."
_NOT_REFRESHED = "The view could not be refreshed; restart MailBrief to see the latest."
_BUSY = "MailBrief is busy; try again in a moment."
_CANCELLED = "Cancelled. The displayed saved brief is unchanged."
_BRIEFS_UNAVAILABLE = "Saved briefs open once local storage loads. Choose Retry loading saved data."
_EDITOR_STALE = (
    "This action changed since you opened it. Your edits are still here — copy what you "
    "need, then Cancel and reopen the action."
)
_EDITOR_FAILED = "Couldn't save. Your edits are still here; try again."
_CALENDAR_NOT_CONNECTED = "Connect Gmail to see your Google Calendar here."
_CALENDAR_ASK = "Google Calendar isn't allowed yet. Choose Allow Google Calendar."
_CALENDAR_ALLOWING = "Allow Google Calendar in your browser."
_CALENDAR_NOT_GRANTED = (
    "Google didn't grant calendar access. Choose Allow Google Calendar and tick both calendar "
    "boxes."
)
_CALENDAR_ALLOW_STOPPED = (
    "Google Calendar wasn't allowed; Gmail is still connected. Choose Allow Google Calendar "
    "and pick your Gmail account."
)
_CALENDAR_BUSY = "Google Calendar is busy; try again in a minute."
_CALENDAR_CANCELLED = "Cancelled. The Calendar page shows what was saved."
_CALENDAR_ALLOW_CANCELLED = (
    "Cancelled. Gmail is still connected; choose Allow Google Calendar to try again."
)
_CALENDAR_UNREACHABLE = "Couldn't reach Google Calendar; the Calendar page shows what was saved."
_CALENDAR_STOPPED = {
    "AUTH_REQUIRED": "Google Calendar stopped: connect Gmail again.",
    "PERMISSION_DENIED": "Google Calendar stopped: access was denied.",
    "RATE_LIMITED": "Google Calendar stopped: it is busy; try again in a minute.",
}
_EVENT_STALE = (
    "This event changed since you opened it. Your edits are still here — copy what you "
    "need, then Cancel and reopen the event."
)
_NO_SOURCE = "That email is no longer in local mail."
_DRAFT_CLOSE_FAILED = "Couldn't save. Your text is still here; try again."
_DRAFT_EXPORT_FAILED = "Couldn't export. Check the folder and try again."
_AI_CANCELLED = "Cancelled. Your text is unchanged."
_AI_DECLINED = "Nothing was sent: AI drafting needs your consent first."
_AI_FAILED = "Couldn't write with Groq. Your text is unchanged."
_REFRESH_DISCONNECTED = "Automatic refresh skipped: connect Gmail first."
_REFRESH_FAILED = "Refresh failed. The displayed saved brief is unchanged."
_AUTOMATIC = "Automatic refresh: "


def calendar_sync_text(result: CalendarSyncResult) -> str:
    """What a Google Calendar sync did, in one sentence of fixed words and counts."""
    if result.stopped_code is not None:
        return _CALENDAR_STOPPED.get(result.stopped_code, _CALENDAR_UNREACHABLE)
    if result.failed:
        return (
            f"Google Calendar updated {result.synced} of {result.calendars} calendars; "
            "the others show what was saved."
        )
    events = "1 event" if result.events == 1 else f"{result.events} events"
    calendars = "1 calendar" if result.calendars == 1 else f"{result.calendars} calendars"
    return f"Google Calendar updated: {events} from {calendars}."


def _review_hint(limit: int) -> str:
    noun = "message" if limit == 1 else "messages"
    return (
        "Review today's Inbox. Suggested messages are checked; add or remove any message, "
        f"up to {limit} {noun}. Only checked messages will have their bodies retrieved."
    )


def _too_many(limit: int) -> str:
    return f"Choose at most {limit} {'message' if limit == 1 else 'messages'} before continuing."


_EXCLUDED_TIP = (
    "This sender is excluded in Settings > Preferences, so this message is never analyzed. "
    "Change the rule there to include it."
)


def thread_check_text(sync: SyncResult) -> str:
    """The run's check of tracked threads in one sentence, never with its code; empty when
    no threads were tracked."""
    if sync.threads_stopped_code:
        return "Thread checks stopped early."
    tracked, checked = sync.threads_tracked, sync.threads_checked
    if not tracked:
        return ""
    noun = "thread" if tracked == 1 else "threads"
    if checked == tracked:
        return f"Checked {checked} tracked {noun}."
    return f"Checked {checked} of {tracked} tracked {noun}; {sync.threads_failed} failed."


def _count_messages(count: int, *, new: bool = False) -> str:
    noun = "new message" if new else "message"
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _ready_text(ready: int) -> str:
    """How many new messages an automatic run found ready to review (ADR 0017)."""
    if ready == 0:
        return "Nothing new to review."
    if ready == 1:
        return "1 new message is ready to review."
    return f"{ready} new messages are ready to review."


_RUN_WIDTH = 760
# Ctrl+1 to Ctrl+7 (Cmd on macOS) show the sidebar's pages, in its order.
# Ctrl+1 to Ctrl+7 follow the sidebar's order.
PAGE_SHORTCUTS = tuple(key for key, _label, _icon in PAGES)


def _step_heading(layout: QVBoxLayout, step: str, title: str) -> None:
    """A run step's caption ("Step 1 of 2") above its title."""
    caption = plain_label(step, tone="muted", px=CAPTION_PX)
    caption.setObjectName("stepCaption")
    layout.addWidget(caption)
    heading = plain_label(title, px=TITLE_PX, semibold=True)
    heading.setObjectName("stepTitle")
    layout.addWidget(heading)


def _page(widget: QWidget, name: str, accessible_name: str, *, margins: int = 12) -> QWidget:
    """A workspace page holding ``widget``, with ``margins`` px around it."""
    page = QWidget()
    page.setObjectName(name)
    page.setAccessibleName(accessible_name)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.addWidget(widget)
    return page


class _ApprovedGate:
    """The editor's preview already asked; first use also needs the ticked consent box."""

    def __init__(self, agreed: bool) -> None:
        self._agreed = agreed

    async def request_drafting_consent(self, preview: DraftingPreview) -> bool:
        return self._agreed or not preview.first_use


class _ApprovedPlanning:
    """The dialog's preview already asked; first use also needs the ticked consent box."""

    def __init__(self, agreed: bool) -> None:
        self._agreed = agreed

    async def request_planning_consent(self, preview: PlanningPreview) -> bool:
        return self._agreed or not preview.first_use


_PLAN_PROBLEMS: Final = {
    "AI_OUTPUT_INCOMPLETE": "Groq's answer was cut off. Your calendar is unchanged; try again.",
    "AI_INVALID_OUTPUT": "Groq's answer couldn't be used. Your calendar is unchanged; try again.",
    "AI_REFUSED": "Groq declined this request. Your calendar is unchanged.",
}
_PLAN_FAILED: Final = "Planning with AI failed. Your calendar is unchanged; try again."
_PLAN_DECLINED: Final = "Nothing was sent. Your calendar is unchanged."
_PLAN_CANCELLED: Final = "Cancelled. Nothing was changed."
_PLAN_SIGN_IN: Final = (
    "Google rejected the sign-in. Connect Gmail again, or untick Google Calendar times."
)
_PLAN_STALE: Final = (
    "Your calendar changed since you asked, so nothing was applied. Discard and ask again."
)


def planning_problem_text(code: str | None) -> str:
    """What a failed request means, in the calendar's words; other codes keep their own."""
    return _PLAN_PROBLEMS.get(code or "") or error_guidance(code or "AI_PROVIDER_ERROR")


class DraftWrites:
    """One serialized, coalescing queue for the open draft's writes.

    The owner's typing never makes the window busy, so draft writes do not go through
    MainWindow.start(). Autosaves coalesce: only the newest waiting snapshot is saved, and
    it is read when its turn comes. Every other operation waits for waiting snapshots
    first. drain() waits for everything queued, so shutdown can finish it before the
    database is closed. Operations handle their own failures; anything left is logged.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot: Callable[[], Awaitable[object]] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def idle(self) -> bool:
        return all(task.done() for task in self._tasks)

    def autosave(self, save: Callable[[], Awaitable[object]]) -> None:
        """Queue a save; it replaces any save still waiting."""
        self._snapshot = save
        self._spawn(self._flush())

    def run(self, operation: Callable[[], Awaitable[object]]) -> None:
        """Queue an operation to run once every earlier write, and any waiting save, is done."""
        self._spawn(self._then(operation))

    async def drain(self) -> None:
        # Finished tasks leave the set only once the loop runs their callbacks, and gather()
        # returns without yielding when every task is done, so wait only for unfinished ones.
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn(self, work: Coroutine[object, object, None]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError as exc:
            # Only possible once the event loop has stopped, when nothing can be saved.
            work.close()
            log_failure(exc)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _save_waiting(self) -> None:
        save, self._snapshot = self._snapshot, None
        if save is not None:
            await save()

    async def _flush(self) -> None:
        async with self._lock:
            await self._guard(self._save_waiting)

    async def _then(self, operation: Callable[[], Awaitable[object]]) -> None:
        async with self._lock:
            await self._guard(self._save_waiting)
            await self._guard(operation)

    @staticmethod
    async def _guard(operation: Callable[[], Awaitable[object]]) -> None:
        try:
            await operation()
        except Exception as exc:
            log_failure(exc)


class MainWindow(QMainWindow):
    """Keep the last saved brief visible throughout refresh, failure and cancellation."""

    closing = Signal()

    def __init__(
        self,
        backend: DesktopBackend,
        *,
        database_path: Path | None = None,
        appearance: AppearanceStore | None = None,
        saved_appearance: Appearance | None = None,
    ) -> None:
        super().__init__()
        self.backend = backend
        # Without a store (in tests), an appearance change applies but isn't saved.
        self.appearance_writes = (
            None if appearance is None else AppearanceWriter(appearance, self._appearance_not_saved)
        )
        # The colour scheme and glass choices in effect, saved whole on every change: as
        # startup already read them, or read here.
        if saved_appearance is not None:
            self.appearance = saved_appearance
        else:
            self.appearance = Appearance() if appearance is None else appearance.load()
        self.pending_restore: tuple[Path, BackupMetadata] | None = None
        self._unsaved_drafts: set[str] = set()
        self.data_dialog = (
            None
            if database_path is None
            else DataDialog(
                self, database_path, self._run_data, self._request_restore, self._refresh_data
            )
        )
        self.task: asyncio.Task[None] | None = None
        self._cancel = asyncio.Event()
        self._review: asyncio.Future[tuple[str, ...] | None] | None = None
        # The review in progress: its message limit and the messages it can't select.
        self._review_limit = MAX_SHORTLIST_SIZE
        self._review_blocked: frozenset[str] = frozenset()
        self._consent: asyncio.Future[bool] | None = None
        self._ready = False
        self._closing = False
        self._shutdown_complete = False
        self._cancellable = True
        # The one change that Undo reverses: its button label and the operation that undoes it.
        self._undo: tuple[str, Callable[[], Awaitable[None]]] | None = None
        # The connected Gmail account, known once a connection succeeds.
        self._account_email: str | None = None
        # The brief shown: None for the latest, else a past brief's account and date.
        self._shown: tuple[str, date] | None = None
        # Whether the running operation is an automatic refresh (ADR 0017): its failures are
        # reported as such, and it never opens a panel or dialog.
        self._automatic_active = False
        # When the running sync finished, once its review opens: the review and consent may
        # follow it by many minutes. The header shows the last such time, ``_checked``, with
        # its date once it isn't today; ``_day`` is the owner's day the views were built
        # for, then at each minute tick.
        self._synced_at: datetime | None = None
        self._checked: datetime | None = None
        self._day: date | None = None
        # A new day's reload of the actions and Briefs, waiting for the window to be free.
        self._day_refresh_pending = False
        # Carryover and overdue labels use the owner's local day.
        self.now: Callable[[], datetime] = lambda: datetime.now(UTC)
        self.zone = resolve_timezone(None)
        if self.data_dialog is not None:
            self.data_dialog.today = self._local_today
        self._projects: tuple[Project, ...] = ()
        self._areas: tuple[LifeArea, ...] = ()
        self.projects_dialog = ProjectsDialog(self)
        self.projects_dialog.save_requested.connect(self._request_save_organization)
        self.projects_dialog.archive_requested.connect(self._request_archive_project)
        self.projects_dialog.delete_area_requested.connect(self._request_delete_area)
        self.action_editor = ActionEditor(self)
        self.action_editor.save_requested.connect(self._request_save_action)
        self.event_editor = EventEditor(self)
        self.event_editor.save_requested.connect(self._request_save_event)
        self.planning_dialog = PlanningDialog(self)
        self.planning_dialog.preview_requested.connect(self._request_planning_preview)
        self.planning_dialog.ask_requested.connect(self._request_planning_ask)
        self.planning_dialog.apply_requested.connect(self._request_apply_planning)
        self.planning_dialog.cancel_requested.connect(self._cancel_planning)
        self._planning_plan: PreparedPlan | None = None
        self._planning_cancel: asyncio.Event | None = None
        self._planning_task: asyncio.Task[PlanningOutcome] | None = None
        self._calendar_week: date | None = None  # The Monday shown; None follows today.
        # The 1st of the month the Month view shows; None follows today.
        self._calendar_month: date | None = None
        self._calendar_allowed: bool | None = None  # The sign-in allows Calendar; None: unknown.
        self._calendar_shown: CalendarRange | None = None
        # Reads run beside start()'s operations, since they change nothing: the week shown,
        # a search, and Settings opening while an operation runs. The newest read of the
        # week wins, and shutdown waits for them all before closing the database.
        self._calendar_reads: set[asyncio.Task[None]] = set()
        self._settings_opening: asyncio.Task[None] | None = None  # Until Settings shows.
        self._calendar_read = 0
        self._day_read = 0  # Like the calendar's: the newest read of "Your day" wins.

        self.action_editor.create_requested.connect(self._request_create_action)
        self.draft_writes = DraftWrites()
        # The generation in progress: its plan, its cancel flag and its task.
        self._drafting_plan: DraftingPlan | None = None
        self._drafting_cancel: asyncio.Event | None = None
        self._drafting_task: asyncio.Task[DraftingOutcome] | None = None
        self.draft_editor = DraftEditor(self)
        self._connect_draft_editor(self.draft_editor)
        self.cached_dialog = CachedMailDialog(self)
        self.proposals_dialog = ProposalsDialog(self)
        self.proposals_dialog.apply_requested.connect(self._request_apply_from_dialog)
        self.proposals_dialog.dismiss_requested.connect(
            lambda proposal_id: self._start_from_link(lambda: self._dismiss_proposal(proposal_id))
        )
        self.history_panel = BriefHistoryPanel()
        self.history_panel.open_requested.connect(self._request_open_brief)
        self.history_panel.generate_requested.connect(self._request_brief_day)
        self.cached_dialog.page_requested.connect(self._request_cached_page)
        self.settings_dialog = SettingsDialog(self)
        self.auto_send_dialog = AutoSendDialog(self.settings_dialog)
        self.auto_send_dialog.save_requested.connect(self._request_set_auto_send)
        # When an automatic run is due, by the owner's schedule; it stops with the window.
        self.scheduler = RefreshScheduler(self, clock=lambda: self.now())
        self.scheduler.due.connect(self._refresh_due)
        # Once a minute, so a new day dates the check time and recounts the deadline chips.
        self.day_timer = QTimer(self)
        self.day_timer.setInterval(60_000)
        self.day_timer.timeout.connect(self._minute_tick)
        self.day_timer.start()
        self.settings_dialog.save_requested.connect(self._request_save_preferences)
        self.settings_dialog.key_requested.connect(self._request_save_key)
        self.settings_dialog.remove_key_requested.connect(
            lambda: self.start(self._remove_key, cancellable=False)
        )
        self.settings_dialog.revoke_requested.connect(
            lambda: self.start(self._revoke_consent, cancellable=False)
        )
        appearance_panel = self.settings_dialog.appearance_panel
        appearance_panel.scheme_chosen.connect(self._change_scheme)
        appearance_panel.mode_chosen.connect(self._change_mode)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            # A slot of the window: Qt drops the connection when it is destroyed.
            application.styleHints().colorSchemeChanged.connect(self._system_mode_changed)
        appearance_panel.transparency_chosen.connect(self._change_transparency)
        appearance_panel.motion_chosen.connect(self._change_motion)
        # How glass is drawn: the system's accessibility settings and the owner's choices.
        self.glass_accessibility = GlassAccessibility(
            self, transparency=self.appearance.transparency, motion=self.appearance.motion
        )
        self.glass_accessibility.changed.connect(appearance_panel.set_system)
        appearance_panel.set_system(self.glass_accessibility.system)
        preferences_panel = self.settings_dialog.preferences_panel
        preferences_panel.save_requested.connect(self._request_save_owner_preferences)
        preferences_panel.reset_requested.connect(
            lambda: self.start(self._reset_owner_preferences, cancellable=False)
        )
        preferences_panel.auto_send_requested.connect(
            lambda: self.start(self._open_auto_send, cancellable=False)
        )
        self.setWindowTitle("MailBrief")
        self.resize(1100, 720)
        workspace = self.workspace = ThreePaneWorkspace()
        sidebar = workspace.sidebar
        # The header: when Gmail was last checked, then Sync and review and Cancel.
        self.generate_button = outline_button(
            "&Sync and review",
            "generateButton",
            mnemonic=True,
            glass=True,
            primary=True,
            icon_name="refresh",
        )
        self.generate_button.setToolTip("Refresh today's Inbox or retry an incomplete run.")
        self.cancel_button = outline_button("&Cancel", "cancelButton", mnemonic=True, glass=True)
        workspace.header.add_widget(self.generate_button)
        workspace.header.add_widget(self.cancel_button)
        # The sidebar's rows, under the names the rest of the window uses.
        self.settings_button = sidebar.settings
        self.cached_button = sidebar.saved_mail
        self.data_button = sidebar.data
        self.data_button.clicked.connect(lambda: self.start(self._open_data, cancellable=False))
        # Who is signed in and what the AI may do live in Settings' Account card, with
        # Disconnect; the sidebar offers only Google sign-in, and only while signed out.
        self.connection = self.settings_dialog.gmail_status
        self.ai = self.settings_dialog.ai_status
        self.disconnect_button = self.settings_dialog.disconnect_button
        self.connect_button = outline_button(
            "&Google sign-in",
            "connectButton",
            mnemonic=True,
            px=SMALL_PX,
            accessible_name="Sign in with Google",
            glass=True,
        )
        sidebar.footer.addWidget(self.connect_button)
        # Today: the banner for a past brief and the retry row, above the brief.
        self.viewing = QWidget()
        self.viewing.setObjectName("viewingBanner")
        self.viewing.setAccessibleName("Brief shown")
        viewing = QHBoxLayout(self.viewing)
        viewing.setContentsMargins(12, 8, 12, 8)
        viewing.setSpacing(8)
        self.viewing_label = wrap_label("", tone="secondary", px=SMALL_PX)
        self.viewing_label.setObjectName("viewingLabel")
        self.latest_button = outline_button("Back to &latest", "latestButton", mnemonic=True)
        viewing.addWidget(self.viewing_label, 1)
        viewing.addWidget(self.latest_button)
        workspace.today_top.addWidget(self.viewing)
        self.viewing.hide()
        self.retry_button = outline_button(
            "&Retry loading saved data", "retryButton", mnemonic=True
        )
        self.retry_button.clicked.connect(lambda: self.start(self.initialize))
        retry = QHBoxLayout()
        retry.setContentsMargins(12, 8, 12, 8)
        retry.addWidget(self.retry_button)
        retry.addStretch(1)
        workspace.today_top.addLayout(retry)
        # "Your day" above the brief: local data only, so it shows without Gmail or AI.
        self.day_panel = DayPanel()
        self.day_panel.action_requested.connect(self._open_day_action)
        self.day_panel.event_requested.connect(self._open_day_event)
        workspace.today_top.addWidget(self.day_panel)
        # The run page: step 1 reviews the shortlist, step 2 approves sending.
        self.review_panel = QWidget()
        self.review_panel.setObjectName("reviewPanel")
        self.review_panel.setAccessibleName("Review the shortlist")
        review_layout = QVBoxLayout(self.review_panel)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(8)
        _step_heading(review_layout, "Step 1 of 2", "Choose what MailBrief reads")
        self.review_hint = wrap_label(_review_hint(MAX_SHORTLIST_SIZE), tone="secondary")
        self.review_hint.setObjectName("reviewHint")
        review_layout.addWidget(self.review_hint)
        self.shortlist = QListWidget()
        self.shortlist.setObjectName("shortlist")
        self.shortlist.setAccessibleName("Messages selected for analysis")
        self.shortlist.setItemDelegate(ShortlistDelegate(self.shortlist))
        # Rows take the viewport's width, so nothing scrolls sideways.
        self.shortlist.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shortlist.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.shortlist.itemChanged.connect(self._selection_changed)
        # Up to its rows' height (review() sets it), then it fills the page and scrolls.
        review_layout.addWidget(self.shortlist, 1)
        self.review_button = QPushButton("Co&ntinue with selected messages")
        self.review_button.setObjectName("reviewButton")
        self.review_button.setProperty("variant", "primary")
        self.review_button.setAutoDefault(False)
        review_layout.addWidget(self.review_button, 0, Qt.AlignmentFlag.AlignLeft)
        # Height the list can't use goes below Continue, never between the labels.
        review_layout.addStretch(0)
        self.review_panel.hide()
        self.consent_panel = QWidget()
        self.consent_panel.setObjectName("consentPanel")
        self.consent_panel.setAccessibleName("Consent to send")
        consent_layout = QVBoxLayout(self.consent_panel)
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(8)
        _step_heading(consent_layout, "Step 2 of 2", "Approve sending to Groq")
        disclosure_card = HairlineFrame()
        disclosure_card.setObjectName("disclosureCard")
        disclosure_card.setAccessibleName("What will be sent")
        QVBoxLayout(disclosure_card).setContentsMargins(0, 0, 0, 0)
        self.disclosure = wrap_label("")
        self.disclosure.setObjectName("disclosure")
        card_layout = disclosure_card.layout()
        assert card_layout is not None
        card_layout.addWidget(self.disclosure)
        consent_layout.addWidget(disclosure_card)
        self.approve_button = outline_button(
            "&Approve transmission to Groq", "approveButton", mnemonic=True
        )
        self.decline_button = outline_button("&Decline", "declineButton", mnemonic=True)
        answers = QHBoxLayout()
        answers.setSpacing(8)
        for answer in (self.approve_button, self.decline_button):
            answers.addWidget(answer)
        answers.addStretch(1)
        consent_layout.addLayout(answers)
        self.consent_panel.hide()
        run_page = QScrollArea()
        run_page.setObjectName("runPage")
        run_page.setAccessibleName("Review and consent")
        run_page.setWidgetResizable(True)
        run_page.setFrameShape(QFrame.Shape.NoFrame)
        run_content = QWidget()
        run_content.setObjectName("runContent")
        # A centred column, at most _RUN_WIDTH wide.
        centred = QHBoxLayout(run_content)
        centred.setContentsMargins(24, 20, 24, 20)
        column = QWidget()
        column.setObjectName("runColumn")
        column.setMaximumWidth(_RUN_WIDTH)
        run_layout = QVBoxLayout(column)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.addWidget(self.review_panel, 1)
        run_layout.addWidget(self.consent_panel)
        run_layout.addStretch(0)
        centred.addStretch(1)
        centred.addWidget(column, 1000)
        centred.addStretch(1)
        run_page.setWidget(run_content)
        workspace.add_page("run", run_page)
        self._set_dialog_zones()
        workspace.detail.suggestion_requested.connect(self._request_suggestion)
        workspace.detail.accept_into_requested.connect(self._request_accept_into)
        workspace.detail.proposal_requested.connect(self._request_proposal)
        workspace.detail.reply_requested.connect(self._request_reply)
        workspace.detail.source_requested.connect(self._open_source)
        self.actions_panel = ActionsPanel()
        self.actions_panel.organize_requested.connect(self._open_organization)
        self.actions_panel.group_changed.connect(
            lambda: self.start(self._refresh_actions, cancellable=False)
        )
        self.actions_panel.new_task_requested.connect(self._request_new_task)
        self.new_task_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_task_shortcut.activated.connect(self._request_new_task)
        self.actions_panel.action_requested.connect(self._request_action)
        self.actions_panel.draft_requested.connect(self._request_action_draft)
        self.actions_panel.tabs.currentChanged.connect(self._actions_tab_changed)
        # Edge to edge, like Today: the list and the detail meet at a hairline.
        workspace.add_page(
            "actions", _page(self.actions_panel, "actionsPage", "Actions", margins=0)
        )
        self.drafts_panel = DraftsPanel()
        self.drafts_panel.draft_requested.connect(self._request_draft)
        workspace.add_page("drafts", _page(self.drafts_panel, "draftsPage", "Drafts"))
        workspace.add_page("briefs", _page(self.history_panel, "briefsPage", "Briefs"))
        self.calendar_panel = CalendarPanel()
        self.inbox_panel = InboxPanel(clock=lambda: self.now())
        self.inbox_panel.refresh_requested.connect(lambda: self.start(self._refresh_inbox))
        self.inbox_panel.load_more_requested.connect(lambda: self.start(self._load_more_inbox))
        self.inbox_panel.mail_search_requested.connect(
            lambda query: self.start(lambda: self._refresh_inbox(query=query))
        )
        self.inbox_panel.search_requested.connect(self._open_palette)
        self.inbox_panel.page_requested.connect(
            lambda offset: self.start(lambda: self._load_inbox(offset), cancellable=False)
        )
        self.inbox_panel.source_requested.connect(self._open_source)
        self.inbox_panel.wanted_requested.connect(
            lambda message_id, wanted: self.start(
                lambda: self._set_wanted(message_id, wanted), cancellable=False
            )
        )
        workspace.add_page("inbox", self.inbox_panel)
        self.calendar_panel.set_view(self.appearance.calendar_view)  # As the owner last chose.
        self.calendar_panel.set_clock(lambda: self.now())
        self.calendar_panel.month_requested.connect(self._request_calendar_month)
        self.calendar_panel.view_requested.connect(self._request_calendar_view)
        self.calendar_panel.event_requested.connect(self._request_event)
        self.calendar_panel.week_requested.connect(self._request_calendar_week)
        self.calendar_panel.google_requested.connect(self._request_google)
        self.calendar_panel.plan_requested.connect(self._request_planning)
        workspace.add_page("calendar", _page(self.calendar_panel, "calendarPage", "Calendar"))
        # Sidebar counts; each refresh replaces only its own, and a failure keeps them.
        self._counts: dict[str, int | None] = {"actions": None, "waiting": None, "drafts": None}
        # The status strip under the workspace.
        strip = QWidget()
        strip.setObjectName("statusStrip")
        strip.setAccessibleName("Status")
        strip_layout = QHBoxLayout(strip)
        # On the glass under the sheet: in line with the header's title and the sheet's
        # right edge.
        strip_layout.setContentsMargins(16, 6, 8, 6)
        strip_layout.setSpacing(8)
        self.status = wrap_label("Loading saved brief…", tone="secondary", px=SMALL_PX)
        self.status.setObjectName("statusText")
        strip_layout.addWidget(self.status, 1)
        self.undo_button = outline_button("&Undo", "undoButton", mnemonic=True)
        self.undo_button.hide()
        self.undo_button.clicked.connect(lambda: self.start(self._undo_last, cancellable=False))
        strip_layout.addWidget(self.undo_button)
        central = QWidget()
        central.setObjectName("mainWindowContent")
        central.setAccessibleName("MailBrief")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(workspace, 1)
        workspace.footer_bar.set_strip(strip)
        self.setCentralWidget(central)
        # Dialogs open non-modal behind this cover, so Cmd+Q and the Dock's Quit still reach
        # closeEvent on macOS (a modal dialog's AppKit session drops them).
        self.dialog_guard = DialogGuard(self)
        # Ctrl+K (Cmd+K on macOS): go to a page, start something, or search local records.
        self.command_palette = CommandPalette(self)
        self.command_palette.query_changed.connect(self._search)
        self.command_palette.command_chosen.connect(self._run_command)
        self.command_palette.hit_chosen.connect(self._open_hit)
        self.palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.palette_shortcut.activated.connect(self._open_palette)
        self._search_read = 0  # The newest search wins; an older one's results are dropped.
        self._quit_confirmed = False  # The owner chose to discard unsaved input and quit.
        self._quit_box: QMessageBox | None = None
        sidebar.page_requested.connect(self._request_page)
        self._set_account(None)
        # Google sign-in waits for initialize() to check the saved session, so it never
        # shows for a moment at launch to an owner who is signed in.
        self.connect_button.hide()
        sidebar.settings_requested.connect(self._request_settings)
        self.connect_button.clicked.connect(lambda: self.start(self._connect))
        self.settings_dialog.sign_in_button.clicked.connect(self._sign_in_from_settings)
        self.disconnect_button.clicked.connect(
            lambda: self.start(self._disconnect, cancellable=False)
        )
        self.cached_button.clicked.connect(lambda: self.start(self._open_cached))
        self.latest_button.clicked.connect(
            lambda: self.start(self._back_to_latest, cancellable=False)
        )
        self.generate_button.clicked.connect(lambda: self.start(self._generate))
        self.cancel_button.clicked.connect(self.cancel)
        self.review_button.clicked.connect(self._accept_review)
        self.approve_button.clicked.connect(lambda: self._answer_consent(True))
        self.decline_button.clicked.connect(lambda: self._answer_consent(False))
        self.page_shortcuts: dict[str, QShortcut] = {}
        for number, key in enumerate(PAGE_SHORTCUTS, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(partial(self._request_page, key))
            self.page_shortcuts[key] = shortcut
        # Each email's detail has new buttons; they join the Tab order after the list.
        workspace.detail.rebuilt.connect(self._link_tab_order)
        self._link_tab_order()
        self._set_busy(False)

    def _link_tab_order(self) -> None:
        """Tab runs from Sync and review through Cancel, the sidebar's pages, Saved mail,
        Data, Settings, Google sign-in (while signed out), the page shown, then Undo and back to the
        start. Hidden and disabled widgets are skipped.

        The chain starts at Undo, so every widget not named here (the other pages', in
        their own order) falls between Today's last button and Undo. Never walk the focus
        chain from Python: PySide re-parents each wrapper ``nextInFocusChain()`` returns
        under the widget it was called on, and deleting that widget then invalidates it."""
        workspace = self.workspace
        chain: list[QWidget] = [
            self.undo_button,
            self.generate_button,
            self.cancel_button,
            workspace.sidebar.nav,
            self.cached_button,
            self.data_button,
            self.settings_button,
            self.connect_button,
            self.latest_button,
            self.retry_button,
            self.day_panel.attention.list,
            self.day_panel.agenda.list,
            self.day_panel.replies.list,
            workspace.brief_list,
            *workspace.detail.buttons(),
        ]
        for first, second in itertools.pairwise(chain):
            QWidget.setTabOrder(first, second)

    def _set_account(self, email: str | None) -> None:
        """The connected Gmail account, or None; Connect shows only without one and
        Disconnect only with one, and the Briefs page follows it."""
        self._account_email = email
        self.connect_button.setVisible(email is None)
        self.settings_dialog.set_signed_in(email is not None)
        self.history_panel.set_account(email)

    def _set_busy(self, busy: bool) -> None:
        self.retry_button.setVisible(not self._ready)
        self.retry_button.setEnabled(not busy)
        self.connect_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(not busy)
        self.settings_dialog.sign_in_button.setEnabled(not busy)
        self.generate_button.setEnabled(not busy and self._ready)
        self.cancel_button.setEnabled(busy and self._cancellable)
        self.cancel_button.setVisible(busy)
        self.settings_dialog.set_busy(busy)
        self.cached_button.setEnabled(not busy and self._ready)
        self.data_button.setEnabled(not busy and self.data_dialog is not None)
        if self.data_dialog is not None:
            self.data_dialog.set_busy(busy)
        self.history_panel.set_busy(busy)
        self.latest_button.setEnabled(not busy)
        self.cached_dialog.set_busy(busy)
        self.proposals_dialog.set_busy(busy)
        self.undo_button.setEnabled(not busy)
        self.actions_panel.set_busy(busy)
        self.action_editor.set_busy(busy)
        self.projects_dialog.set_busy(busy)
        self.drafts_panel.set_busy(busy)
        self.calendar_panel.set_busy(busy)
        self.inbox_panel.set_busy(busy)
        self.event_editor.set_busy(busy)

    # Pages. The sidebar shows Today while the review or consent is up on the run page, and
    # Actions or Waiting by the actions page's tab.

    def _gate_pending(self) -> bool:
        """Whether the review or the consent question is waiting for the owner."""
        return any(
            future is not None and not future.done() for future in (self._review, self._consent)
        )

    def _show_page(self, key: str) -> None:
        if key == "today":
            self.workspace.show_page("run" if self._gate_pending() else "today")
        elif key in ("actions", "waiting"):
            self.workspace.show_page("actions")
            self.actions_panel.show_view(
                ActionFilter.WAITING if key == "waiting" else ActionFilter.OPEN
            )
        else:
            self.workspace.show_page(key)
        self.workspace.sidebar.set_current(self._sidebar_key())

    def _sidebar_key(self) -> str:
        """The sidebar row for the page shown."""
        page = self.workspace.current_page()
        if page == "run":
            return "today"
        if page == "actions":
            return "waiting" if self.actions_panel.view() is ActionFilter.WAITING else "actions"
        return page

    def _actions_tab_changed(self, _index: int) -> None:
        if self.workspace.current_page() == "actions":
            self.workspace.sidebar.set_current(self._sidebar_key())

    def _request_page(self, key: str) -> None:
        if key == "inbox":
            # The Inbox opens on its list, as Gmail's does; a message opens on a click.
            self.inbox_panel.close_message()
            self._show_page(key)
            self.start(self._load_inbox, cancellable=False)
            return
        if key == "briefs":
            # Briefs loads first; the sidebar moves once its page shows.
            self.workspace.sidebar.set_current(self._sidebar_key())
            if not self._ready:
                self.status.setText(_BRIEFS_UNAVAILABLE)
            elif not self.start(self._open_history) and not self._closing:
                self.status.setText(_BUSY)
            return
        self._show_page(key)
        if key == "calendar" and self._calendar_sync_due():
            self._start_calendar_sync()

    def _leave_run_page(self) -> None:
        """Back to Today once neither the review nor the consent is waiting."""
        if not self._gate_pending() and self.workspace.current_page() == "run":
            self._show_page("today")

    def _open_source(self, url: str) -> None:
        if is_gmail_link(url):
            QDesktopServices.openUrl(QUrl(url))

    def _local_today(self) -> date:
        return self.now().astimezone(self.zone).date()

    def _stamp_checked(self) -> None:
        """When the run's sync finished: when its review opened, else (no review) now."""
        self._checked = self.now() if self._synced_at is None else self._synced_at
        self._render_checked()

    def _render_checked(self) -> None:
        """The header's check time: "Checked Gmail at 09:14" today in the owner's zone,
        "Checked Gmail Oct 7 at 23:50" on any other day."""
        if self._checked is None:
            return
        local = self._checked.astimezone(self.zone)
        if local.date() == self._local_today():
            text = f"Checked Gmail at {local:%H:%M}"
        else:
            day = month_day(local.date(), today=self._local_today())
            text = f"Checked Gmail {day} at {local:%H:%M}"
        self.workspace.header.set_status(text)

    def _minute_tick(self) -> None:
        """When the owner's day has changed since the views were built: the check time
        gains its date and the brief's chips recount in place at once, and the actions and
        the Briefs page reload through ``start()``. While another operation runs, that
        reload is held and retried on each tick until it starts."""
        today = self._local_today()
        if self._day is not None and today != self._day:
            self._render_checked()
            self.workspace.set_today(today, self.zone)
            self._day_refresh_pending = True
        self._day = today
        if self._day_refresh_pending:
            self._day_refresh_pending = not self.start(self._refresh_for_new_day, cancellable=False)

    async def _refresh_for_new_day(self) -> None:
        """What counts from the day: the actions' due labels, the Briefs page's missed days
        and the days it can brief, and the calendar's Today and default week. Each keeps
        what it showed if it can't be read."""
        await self._refresh_actions()
        await self._reload_history()
        if self._calendar_week is not None:
            self._show_calendar_week(self._calendar_week)  # Now this week: follow today.
        if self._calendar_month is not None:
            self._show_calendar_month(self._calendar_month)  # Now this month: follow today.
        await self._refresh_calendar()

    def _set_counts(self, **counts: int | None) -> None:
        self._counts.update(counts)
        self.workspace.sidebar.set_counts(
            self._counts["actions"], self._counts["waiting"], self._counts["drafts"]
        )

    async def _open_data(self) -> None:
        if self._unsaved_drafts:
            self.status.setText("Resolve the failed draft save before managing data.")
            return
        if (
            self.action_editor.isVisible()
            or self.draft_editor.isVisible()
            or self.event_editor.isVisible()
            or self.planning_dialog.isVisible()
            or not self.draft_writes.idle
        ):
            self.status.setText(
                "Close the editors and let pending saves finish before managing data."
            )
            return
        if self.data_dialog is not None:
            accounts = await self.backend.cached_accounts() if self._ready else ()
            if not self._closing:
                self.data_dialog.configure(accounts, available=self._ready)
                self.dialog_guard.show_dialog(self.data_dialog)

    def _run_data(self, operation: Callable[[], Awaitable[None]]) -> bool:
        if self.data_dialog is None:
            return False
        dialog = self.data_dialog
        return self.start(lambda: dialog.guarded(operation), cancellable=False)

    def _request_restore(self, path: Path, metadata: BackupMetadata) -> None:
        self.pending_restore = (path, metadata)
        self.close()

    async def _refresh_data(self) -> None:
        self.backend.reset_inbox_browsing()
        if self._closing:
            return
        self.inbox_panel.clear()
        self._offer_undo()
        self._shown = None
        self.viewing.hide()
        saved = await self.backend.load_saved()
        if saved is None:
            self.workspace.clear("")
        else:
            await self._show_digest(saved)
        await self._refresh_actions()
        await self._refresh_drafts()
        self.cached_dialog.reject()
        await self._reload_history()
        self.proposals_dialog.reject()
        if self.data_dialog is not None:
            self.data_dialog.configure(await self.backend.cached_accounts(), available=True)

    def _offer_undo(
        self, label: str | None = None, operation: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        """Offer to undo the latest change, or clear the offer when called without one."""
        self._undo = None if label is None or operation is None else (label, operation)
        self.undo_button.setText("&Undo" if self._undo is None else f"&{self._undo[0]}")
        self.undo_button.setVisible(self._undo is not None)

    def _note_not_refreshed(self, exc: Exception) -> None:
        """The change itself was saved; only the view is out of date."""
        log_failure(exc)
        if _NOT_REFRESHED not in self.status.text():
            self.status.setText(f"{self.status.text()} {_NOT_REFRESHED}".strip())

    def _set_shown(self, shown: tuple[str, date] | None) -> None:
        """Show the latest brief (None), or say which past brief is shown."""
        self._shown = shown
        self.viewing.setVisible(shown is not None)
        if shown is not None:
            self.viewing_label.setText(
                f"Viewing the brief for {day_text(shown[1], today=self._local_today())}."
            )

    async def _reload_brief(self) -> None:
        """Reload the brief shown: the latest, or the past brief being viewed."""
        shown = self._shown
        try:
            saved = await (
                self.backend.load_saved() if shown is None else self.backend.load_brief(*shown)
            )
        except Exception as exc:
            self._note_not_refreshed(exc)
            return
        if saved is None and shown is not None:
            self._set_shown(None)  # That brief is gone; show the latest instead.
            await self._reload_brief()
            return
        if saved is not None:
            await self._show_digest(saved)

    async def _show_digest(self, digest: DailyDigest) -> None:
        """Every brief is shown here, with the actions that continue its threads and the
        updates its emails propose; when either can't be read, the brief is shown without it."""
        links: dict[str, tuple[ThreadLink, ...]] | None
        try:
            links = await self.backend.brief_links(digest)
        except Exception as exc:
            log_failure(exc)
            links = None
        proposals: dict[str, tuple[ActionProposal, ...]] | None
        try:
            proposals = await self.backend.brief_proposals(digest)
        except Exception as exc:
            log_failure(exc)
            proposals = None
        self.workspace.show_digest(
            digest,
            links=links,
            proposals=proposals,
            owner_zone=self.zone,
            today=self._local_today(),
        )

    async def _refresh_actions(self) -> None:
        now = self.now()
        today = now.astimezone(self.zone).date()
        try:
            projects, areas, tags = await self.backend.list_organization()
            self._projects, self._areas = projects, areas
            self.action_editor.set_projects(self._projects)
            self.action_editor.set_tags(tags)
            self.actions_panel.show_groups(self._projects, self._areas, tags)
            group, group_id = self.actions_panel.grouping()
            for view in ActionFilter:
                actions = (
                    await self.backend.list_actions(view)
                    if group == "all"
                    else await self.backend.list_grouped_actions(view, group, group_id)
                )
                # Only the completed list is capped; say so when more exist.
                total = (
                    (
                        await self.backend.count_actions(view)
                        if group == "all"
                        else await self.backend.count_grouped_actions(view, group, group_id)
                    )
                    if view is ActionFilter.COMPLETED and len(actions) >= COMPLETED_LIST_LIMIT
                    else None
                )
                self.actions_panel.show_actions(
                    view, actions, today=today, zone=self.zone, now=now, total=total
                )
                if view is ActionFilter.OPEN:
                    self._set_counts(
                        actions=len(actions)
                        if group == "all"
                        else await self.backend.count_actions(view)
                    )
                elif view is ActionFilter.WAITING:
                    self._set_counts(
                        waiting=len(actions)
                        if group == "all"
                        else await self.backend.count_actions(view)
                    )
        except Exception as exc:
            self._note_not_refreshed(exc)
        await self._refresh_day()

    async def _refresh_drafts(self) -> None:
        try:
            drafts = await self.backend.list_drafts()
            self.drafts_panel.show_drafts(drafts, self.zone, self._local_today())
            self._set_counts(drafts=len(drafts))
        except Exception as exc:
            self._note_not_refreshed(exc)

    def _refresh_proposals_dialog(self) -> None:
        """An open Proposals dialog follows the action's latest state."""
        dialog = self.proposals_dialog
        public_id = dialog.action_public_id
        if dialog.isVisible() and public_id is not None:
            dialog.show_proposals(
                self.actions_panel.action_with_id(public_id), self.zone, self._local_today()
            )

    async def _refresh_views(self) -> None:
        await self._reload_brief()
        await self._refresh_actions()
        await self._refresh_drafts()
        await self._refresh_calendar()
        self._refresh_proposals_dialog()

    async def _stale(self, exc: Exception) -> None:
        log_failure(exc)
        self.status.setText("That changed or is no longer available; the view was reloaded.")
        await self._refresh_views()

    def _start_from_link(self, operation: Callable[[], Awaitable[None]]) -> None:
        """Brief links stay clickable while busy, so a refused click says why."""
        if not self.start(operation, cancellable=False) and not self._closing:
            self.status.setText(_BUSY)

    def _request_suggestion(self, kind: str, suggestion_id: int) -> None:
        if kind == ACCEPT:
            self._start_from_link(lambda: self._accept_suggestion(suggestion_id))
        elif kind == DISMISS:
            self._start_from_link(lambda: self._dismiss_suggestion(suggestion_id))

    async def _accept_suggestion(self, suggestion_id: int) -> None:
        try:
            action = await self.backend.accept_suggestion(suggestion_id)
        except _STALE as exc:
            await self._stale(exc)
            return
        public_id, revision = action.public_id, action.revision
        self._offer_undo("Undo accept", lambda: self.backend.unaccept_action(public_id, revision))
        self.status.setText(f"Accepted: {action.title}. It stays open until you complete it.")
        await self._refresh_views()

    def _request_accept_into(self, suggestion_id: int, public_id: str, revision: int) -> None:
        self._start_from_link(lambda: self._accept_into(suggestion_id, public_id, revision))

    async def _accept_into(self, suggestion_id: int, public_id: str, revision: int) -> None:
        """Add the suggestion's email to an action that continues its thread, with Undo when
        that changed anything."""
        try:
            result = await self.backend.accept_into(suggestion_id, public_id, revision)
        except ActionConflictError as exc:
            log_failure(exc)
            self.status.setText(str(exc))  # Static; names no action or mail.
            await self._refresh_views()
            return
        except _STALE as exc:
            await self._stale(exc)
            return
        action = result.action
        if not result.changed:  # Already there: nothing to undo.
            self.status.setText(f"Already added to: {action.title}.")
            await self._refresh_views()
            return

        async def undo() -> None:
            await self.backend.undo_accept_into(
                suggestion_id,
                action.public_id,
                action.revision,
                result.source_added,
                previous=result.previous,
            )

        self._offer_undo("Undo add", undo)
        self.status.setText(f"Added to: {action.title}.")
        await self._refresh_views()

    def _request_proposal(self, kind: str, proposal_id: int, revision: int) -> None:
        if kind == APPLY:
            self._start_from_link(lambda: self._apply_proposal(proposal_id, revision))
        elif kind == DISMISS:
            self._start_from_link(lambda: self._dismiss_proposal(proposal_id))

    def _request_apply_from_dialog(self, proposal_id: int) -> None:
        proposal = self.proposals_dialog.proposal(proposal_id)
        if proposal is not None:
            revision = proposal.action_revision
            self._start_from_link(lambda: self._apply_proposal(proposal_id, revision))

    async def _apply_proposal(self, proposal_id: int, revision: int) -> None:
        """Apply a proposal the owner chose, at the action revision they saw, with Undo."""
        try:
            action = await self.backend.apply_proposal(proposal_id, revision)
        except ActionConflictError as exc:
            log_failure(exc)
            self.status.setText(str(exc))  # Static; names no action or mail.
            await self._refresh_views()
            return
        except _STALE as exc:
            await self._stale(exc)
            return
        applied_revision = action.revision

        async def undo() -> None:
            await self.backend.undo_apply_proposal(proposal_id, applied_revision)

        self._offer_undo("Undo apply", undo)
        self.status.setText(f"Applied to: {action.title}.")
        await self._refresh_views()

    async def _dismiss_proposal(self, proposal_id: int) -> None:
        try:
            await self.backend.dismiss_proposal(proposal_id)
        except ActionConflictError as exc:
            log_failure(exc)
            self.status.setText(str(exc))
            await self._refresh_views()
            return
        except _STALE as exc:
            await self._stale(exc)
            return
        self._offer_undo("Undo dismiss", lambda: self.backend.restore_proposal(proposal_id))
        self.status.setText("Proposal dismissed. It won't be proposed again.")
        await self._refresh_views()

    async def _dismiss_suggestion(self, suggestion_id: int) -> None:
        try:
            await self.backend.dismiss_suggestion(suggestion_id)
        except _STALE as exc:
            await self._stale(exc)
            return
        self._offer_undo("Undo dismiss", lambda: self.backend.restore_suggestion(suggestion_id))
        self.status.setText("Suggestion dismissed. It won't be suggested again for this email.")
        await self._refresh_views()

    async def _undo_last(self) -> None:
        if self._undo is None:
            return
        operation = self._undo[1]
        self._offer_undo()
        try:
            await operation()
        except _STALE as exc:
            log_failure(exc)
            self.status.setText("Can't undo: it has changed since then.")
            await self._refresh_views()
            return
        self.status.setText("Undone.")
        await self._refresh_views()

    def _request_action(self, kind: str, action: Action) -> None:
        if kind == EDIT:
            self.action_editor.edit(action, self.now().astimezone(self.zone).date(), self.zone)
            self.dialog_guard.show_dialog(self.action_editor)
        elif kind == COMPLETE:
            self.start(lambda: self._complete_action(action), cancellable=False)
        elif kind == REOPEN:
            self.start(lambda: self._reopen_action(action), cancellable=False)
        elif kind == DELETE:
            self.start(lambda: self._delete_action(action), cancellable=False)
        elif kind == SEEN:
            self.start(lambda: self._mark_seen(action), cancellable=False)
        elif kind == PROPOSALS:
            self.proposals_dialog.show_proposals(action, self.zone, self._local_today())
            self.dialog_guard.show_dialog(self.proposals_dialog)

    def _open_organization(self) -> None:
        if self._closing or self._dialog_open() or (self.task is not None and not self.task.done()):
            return
        self.projects_dialog.show_records(self._projects, self._areas)
        self.projects_dialog.open()
        self.start(self._load_organization, cancellable=False)

    async def _load_organization(self) -> None:
        try:
            projects = await self.backend.list_projects()
            areas = await self.backend.list_areas()
        except Exception as exc:
            log_failure(exc)
            self.projects_dialog.status.setText("Could not reload. Close and reopen to retry.")
            return
        self._projects, self._areas = projects, areas
        self.projects_dialog.show_records(projects, areas)

    def _cache_organization(self, saved: Project | LifeArea) -> None:
        if isinstance(saved, Project):
            self._projects = tuple(
                item for item in self._projects if item.public_id != saved.public_id
            ) + (saved,)
        else:
            self._areas = tuple(
                item for item in self._areas if item.public_id != saved.public_id
            ) + (saved,)

    def _request_save_organization(
        self, kind: str, public_id: str | None, revision: int | None, edit: AreaEdit
    ) -> None:
        self.start(
            lambda: self._save_organization(kind, public_id, revision, edit), cancellable=False
        )

    async def _save_organization(
        self, kind: str, public_id: str | None, revision: int | None, edit: AreaEdit
    ) -> None:
        try:
            saved = await self.backend.save_organization(kind, public_id, revision, edit)
        except Exception as exc:
            log_failure(exc)
            self.projects_dialog.status.setText(
                "Could not save. Your input is still here. "
                "Retry, or discard edits and reopen to reload."
            )
            return
        self._offer_undo()
        self._cache_organization(saved)
        await self._refresh_views()
        self.projects_dialog.show_records(self._projects, self._areas, saved.public_id)
        self.projects_dialog.status.setText("Saved.")
        self.status.setText("Organization saved.")

    def _request_archive_project(self, project: Project, archived: bool) -> None:
        self.start(lambda: self._archive_project(project, archived), cancellable=False)

    async def _archive_project(self, project: Project, archived: bool) -> None:
        try:
            saved = await self.backend.archive_project(
                project.public_id, project.revision, archived
            )
        except ProjectConflictError:
            self.projects_dialog.status.setText(
                "That project changed. Close and reopen to reload it."
            )
            return
        self._offer_undo()
        self._cache_organization(saved)
        await self._refresh_views()
        self.projects_dialog.show_records(self._projects, self._areas, project.public_id)
        self.status.setText(
            "Project archived. Its tasks remain." if archived else "Project reopened."
        )

    def _request_delete_area(self, area: LifeArea) -> None:
        self.start(lambda: self._delete_area(area), cancellable=False)

    async def _delete_area(self, area: LifeArea) -> None:
        try:
            await self.backend.delete_area(area.public_id, area.revision)
        except ProjectConflictError:
            self.projects_dialog.status.setText("That area changed. Close and reopen to reload it.")
            return
        self._offer_undo()
        self._areas = tuple(item for item in self._areas if item.public_id != area.public_id)
        self._projects = tuple(
            item.model_copy(
                update={"area_public_id": None, "area_name": None, "revision": item.revision + 1}
            )
            if item.area_public_id == area.public_id
            else item
            for item in self._projects
        )
        await self._refresh_views()
        self.projects_dialog.show_records(self._projects, self._areas)
        self.status.setText("Life area removed. Its projects and tasks remain.")

    def _request_new_task(self) -> None:
        if self._closing or (self.task is not None and not self.task.done()) or self._dialog_open():
            return
        self._show_page("actions")
        self.action_editor.new_task(self._local_today())
        self.dialog_guard.show_dialog(self.action_editor)

    def _request_create_action(self, edit: ActionEdit, steps: Sequence[StepEdit]) -> None:
        started = self.start(lambda: self._create_action(edit, steps), cancellable=False)
        if not started and not self._closing:
            self.action_editor.keep_open(_BUSY)

    async def _create_action(self, edit: ActionEdit, steps: Sequence[StepEdit]) -> None:
        try:
            created = await self.backend.create_action(edit, steps)
        except Exception:
            if not self._closing:
                self.action_editor.keep_open(_EDITOR_FAILED)
            raise
        self.action_editor.finish_saved()
        self.actions_panel.clear_group()
        self._offer_undo()
        self.status.setText(f"Created: {created.title}.")
        self.actions_panel.show_view(
            ActionFilter.OPEN if created.ownership is ActionOwnership.MINE else ActionFilter.WAITING
        )
        await self._refresh_views()
        self.actions_panel.select_action(created.public_id)

    def _request_save_action(
        self, action: Action, edit: ActionEdit, steps: Sequence[StepEdit] | None
    ) -> None:
        started = self.start(lambda: self._save_action(action, edit, steps), cancellable=False)
        if not started and not self._closing:
            self.action_editor.keep_open(_BUSY)

    async def _complete_action(self, action: Action) -> None:
        try:
            done = await self.backend.complete_action(action.public_id, action.revision)
        except _STALE as exc:
            await self._stale(exc)
            return

        async def undo() -> None:
            await self.backend.undo_action_status(done.public_id, done.revision)

        self._offer_undo("Undo complete", undo)
        self.status.setText(f"Completed: {done.title}.")
        await self._refresh_views()

    async def _reopen_action(self, action: Action) -> None:
        try:
            opened = await self.backend.reopen_action(action.public_id, action.revision)
        except _STALE as exc:
            await self._stale(exc)
            return

        async def undo() -> None:
            await self.backend.undo_action_status(opened.public_id, opened.revision)

        self._offer_undo("Undo reopen", undo)
        self.status.setText(f"Reopened: {opened.title}.")
        await self._refresh_views()

    async def _delete_action(self, action: Action) -> None:
        try:
            await self.backend.delete_action(action.public_id, action.revision)
        except _STALE as exc:
            await self._stale(exc)
            return
        public_id = action.public_id

        async def undo() -> None:
            await self.backend.restore_action(public_id)

        self._offer_undo("Undo delete", undo)
        self.status.setText(f"Deleted: {action.title}.")
        await self._refresh_views()

    async def _mark_seen(self, action: Action) -> None:
        try:
            marked = await self.backend.mark_thread_seen(action.public_id, action.revision)
        except _STALE as exc:
            await self._stale(exc)
            return
        self._offer_undo()  # Like an edit, it has no undo; an older offer could be stale.
        self.status.setText(f"Marked seen: {marked.title}.")
        await self._refresh_views()

    async def _save_action(
        self, action: Action, edit: ActionEdit, steps: Sequence[StepEdit] | None
    ) -> None:
        """The editor closes only once the save succeeds; otherwise it keeps the edits."""
        try:
            saved = await self.backend.save_action(action.public_id, action.revision, edit, steps)
        except _STALE as exc:
            if not self._closing:
                self.action_editor.keep_open(_EDITOR_STALE)
            await self._stale(exc)
            return
        except Exception:
            if not self._closing:
                self.action_editor.keep_open(_EDITOR_FAILED)
            raise  # _run logs it and reports the failure.
        self.action_editor.finish_saved()
        self._offer_undo()  # An edit has no undo; an older offer would refer to a past revision.
        self.status.setText(f"Saved: {saved.title}.")
        await self._refresh_views()

    # Calendar (ADR 0018). Saving and deleting use start(); reading a week doesn't, so the
    # page can move between weeks and open the editor while another operation runs. The
    # page shows local data only, so it never needs Gmail or an AI provider.

    async def _refresh_calendar(self) -> None:
        self._calendar_read += 1
        read = self._calendar_read
        first, last = self._calendar_bounds()
        try:
            shown = await self.backend.calendar_range(first, last, self.zone)
        except Exception as exc:
            if read == self._calendar_read:
                self._note_not_refreshed(exc)
            return
        if read != self._calendar_read:
            return  # A newer read, perhaps of another week, shows the page.
        self.calendar_panel.show_range(shown, self.zone, self._local_today())
        self._calendar_shown = shown
        self.calendar_panel.set_google(self._google_mode(), self._google_note(shown))
        await self._refresh_day()

    async def _refresh_day(self) -> None:
        """The Today page's "Your day", from local data; it keeps what it showed when it
        can't be read."""
        self._day_read += 1
        read = self._day_read
        now = self.now()
        try:
            summary = await self.backend.today_summary(
                now.astimezone(self.zone).date(), self.zone, now
            )
        except Exception as exc:
            if read == self._day_read:
                self._note_not_refreshed(exc)
            return
        if read == self._day_read:
            self.day_panel.show_summary(summary, self.zone)

    def _open_day_action(self, public_id: str, waiting: bool) -> None:
        """Show an action from "Your day" on the Actions page (Waiting when someone else
        does it); see _open_action."""
        self._open_action(public_id, ActionFilter.WAITING if waiting else ActionFilter.OPEN)

    def _open_day_event(self, key: object) -> None:
        """Show an event from "Your day" on the Calendar page, in this week, selected."""
        if not isinstance(key, tuple):
            return
        today = self._local_today()
        self._show_calendar_day(today)
        self._show_page("calendar")
        self._read_calendar(select=key, day=today)

    def _calendar_bounds(self) -> tuple[date, date]:
        """The days the Calendar page shows: a week (Week and List views), or the six weeks
        of the month's grid (Month view)."""
        today = self._local_today()
        if self.calendar_panel.view() is CalendarView.MONTH:
            return month_days(self._calendar_month or today)
        first = self._calendar_week or week_start(today)
        return first, first + timedelta(days=6)

    def _show_calendar_week(self, monday: date) -> None:
        """Show the week of ``monday`` from now on; this week's follows today, so the page
        moves on by itself when a new week starts."""
        self._calendar_week = None if monday == week_start(self._local_today()) else monday

    def _show_calendar_month(self, first: date) -> None:
        """Show the month of ``first`` (its 1st) from now on; this month's follows today."""
        self._calendar_month = None if first == month_start(self._local_today()) else first

    def _show_calendar_day(self, day: date) -> None:
        """Show ``day``: its week in the Week and List views, its month in the Month view."""
        self._show_calendar_week(week_start(day))
        self._show_calendar_month(month_start(day))

    def _request_calendar_week(self, first_day: object) -> None:
        if isinstance(first_day, date):
            self._show_calendar_week(first_day)
            self._read_calendar(sync=True)

    def _request_calendar_month(self, first: object) -> None:
        if isinstance(first, date):
            self._show_calendar_month(month_start(first))
            self._read_calendar(sync=True)

    def _request_calendar_view(self, view: object, keep: object = True) -> None:
        """The view switched: show it around the day in focus, and, with ``keep`` (the
        owner chose it with the tabs, not through "+N more"), keep it on this device."""
        chosen = CalendarView(str(view))
        self._show_calendar_day(self._calendar_focus())
        if keep is True and self.appearance.calendar_view is not chosen:
            self._save_appearance(self.appearance.model_copy(update={"calendar_view": chosen}))
        self._read_calendar(sync=True)

    def _calendar_focus(self) -> date:
        """The Calendar page's day in focus; the owner's today before it shows any days."""
        return self.calendar_panel.focus_day() or self._local_today()

    def _read_calendar(
        self,
        *,
        sync: bool = False,
        select: tuple[str, ...] | None = None,
        day: date | None = None,
    ) -> None:
        """Read the week shown beside any running operation; then select the entry
        ``select`` (calendar_view.entry_key) on ``day``, when given; then, with ``sync``,
        read Google Calendar for it when that is due and nothing else is running."""
        if self._closing:
            return

        async def read() -> None:
            await self._refresh_calendar()
            if select is not None:
                self.calendar_panel.select_entry(select, day)
            if sync and self._calendar_sync_due():
                self._start_calendar_sync()

        task = asyncio.get_running_loop().create_task(read())
        self._calendar_reads.add(task)
        task.add_done_callback(self._calendar_reads.discard)

    async def drain_calendar_reads(self) -> None:
        """Wait for the reads of the week in progress."""
        while pending := [task for task in self._calendar_reads if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    def _start_calendar_sync(self) -> bool:
        async def sync() -> None:
            await self._sync_calendar(quiet=True)

        return self.start(self._calendar_work(sync), cancelled=_CALENDAR_CANCELLED)

    def _calendar_work(
        self, operation: Callable[[], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]]:
        """``operation``, which Cancel and quitting can stop: the cache keeps each calendar
        a stopped sync finished, so the page is read again to show them."""

        async def work() -> None:
            try:
                await operation()
            except asyncio.CancelledError:
                self._read_calendar()
                raise

        return work

    def _request_event(self, kind: str, value: object) -> None:
        if kind == NEW_EVENT and isinstance(value, date):
            self.event_editor.edit_new(value, self.now(), self.zone)
            self.dialog_guard.show_dialog(self.event_editor)
        elif kind == OPEN_GOOGLE and isinstance(value, GoogleEvent):
            self._open_calendar_link(value)
        elif isinstance(value, CalendarEvent):
            event = value
            if kind == OPEN_EVENT:
                self.event_editor.edit_event(event, self.zone)
                self.dialog_guard.show_dialog(self.event_editor)
            elif kind == DELETE_EVENT:
                started = self.start(lambda: self._delete_event(event), cancellable=False)
                if not started and not self._closing:
                    self.status.setText(_BUSY)

    # The command palette (Ctrl+K). A search reads local records beside any running
    # operation, like the calendar's reads; what it opens goes through the usual paths.

    def _open_palette(self) -> None:
        if self._closing or self._dialog_open():
            return
        self.command_palette.open_fresh(self.zone, self._local_today())
        self.dialog_guard.show_dialog(self.command_palette)

    def _search(self, text: str) -> None:
        if self._closing:
            return
        self._search_read += 1
        read = self._search_read

        async def search() -> None:
            try:
                results = await self.backend.search(text, self._account_email)
            except Exception as exc:
                log_failure(exc)
                if read == self._search_read:
                    self.command_palette.show_failure(text)
                return
            if read == self._search_read:
                self.command_palette.show_results(text, results)

        task = asyncio.get_running_loop().create_task(search())
        # Shutdown cancels and waits for these reads before it closes the database.
        self._calendar_reads.add(task)
        task.add_done_callback(self._calendar_reads.discard)

    def _run_command(self, key: str) -> None:
        kind, _, page = key.partition(":")
        if kind == "page":
            self._request_page(page)
        elif key == "new:task":
            self._request_new_task()
        elif key == "new:event":
            self._show_page("calendar")
            self._request_event(NEW_EVENT, self._local_today())
        elif key == "new:note":
            self._show_page("drafts")
            self._request_draft(NEW_DRAFT, DraftKind.NOTE)
        else:
            button = {
                "sync": self.generate_button,
                "saved_mail": self.cached_button,
                "data": self.data_button,
                "settings": self.settings_button,
            }.get(key)
            if button is not None:
                button.click()  # A disabled button does nothing, as it would when clicked.

    def _open_hit(self, hit: object) -> None:
        """Open a search hit where it lives: an action on Actions, writing in its editor,
        an event in its week of the Calendar page, mail on the Inbox page (or in Gmail when
        it has left the Inbox)."""
        if not isinstance(hit, SearchHit):
            return
        if hit.kind is SearchKind.ACTION and hit.public_id is not None:
            view = (
                ActionFilter.COMPLETED
                if not hit.open
                else ActionFilter.WAITING
                if hit.waiting
                else ActionFilter.OPEN
            )
            self._open_action(hit.public_id, view)
        elif hit.kind is SearchKind.WRITING and hit.public_id is not None:
            public_id = hit.public_id
            self._show_page("drafts")
            self._start_or_busy(lambda: self._open_draft_by_id(public_id))
        elif hit.kind in (SearchKind.EVENT, SearchKind.GOOGLE_EVENT):
            self._open_event_hit(hit)
        elif hit.kind is SearchKind.MAIL and hit.message_id is not None:
            message_id, link = hit.message_id, hit.web_link
            if hit.open and self._account_email is not None:
                self._start_or_busy(lambda: self._open_mail(message_id, link))
            elif link is not None:
                self._open_source(link)

    def _start_or_busy(self, operation: Callable[[], Awaitable[None]]) -> None:
        if not self.start(operation, cancellable=False) and not self._closing:
            self.status.setText(_BUSY)

    def _open_action(self, public_id: str, view: ActionFilter) -> None:
        """Show an action selected in ``view``; when a project or area filter (or the
        completed list's cap) hides it, show every task first."""
        self._show_page("actions")
        self.actions_panel.show_view(view)
        self.workspace.sidebar.set_current(self._sidebar_key())
        self.actions_panel.select_action(public_id)
        selected = self.actions_panel.selected()
        if selected is not None and selected.public_id == public_id:
            return

        async def unfiltered() -> None:
            self.actions_panel.clear_group()
            await self._refresh_actions()
            self.actions_panel.select_action(public_id)

        self._start_or_busy(unfiltered)

    async def _open_draft_by_id(self, public_id: str) -> None:
        try:
            draft = await self.backend.get_draft(public_id)
        except _STALE as exc:
            await self._stale(exc)
            return
        self._edit_draft(draft)

    def _open_event_hit(self, hit: SearchHit) -> None:
        day = hit.when.astimezone(self.zone).date() if hit.when is not None else hit.day
        if day is None:
            return
        key: tuple[str, ...] = (
            ("event", hit.public_id or "")
            if hit.kind is SearchKind.EVENT
            else ("google", hit.calendar_id or "", hit.event_id or "")
        )
        self._show_calendar_day(day)
        self._show_page("calendar")
        self._read_calendar(select=key, day=day)

    async def _open_mail(self, message_id: str, link: str | None) -> None:
        """The Inbox page holding the message, with it selected; Gmail when the cached
        Inbox no longer has it."""
        email = self._account_email
        position = None if email is None else await self.backend.inbox_position(email, message_id)
        if position is None or email is None:
            if link is not None:
                self._open_source(link)
            return
        self._show_page("inbox")
        self.inbox_panel.zone = self.zone
        self.inbox_panel.show_page(
            await self.backend.inbox_messages(
                email, position - position % INBOX_PAGE_SIZE, cached_page=True
            ),
            select=message_id,
        )

    def _request_save_event(self, edit: object) -> None:
        if not isinstance(edit, EventEdit):
            return
        chosen = edit
        started = self.start(lambda: self._save_event(chosen), cancellable=False)
        if not started and not self._closing:
            self.event_editor.keep_open(_BUSY)

    async def _save_event(self, edit: EventEdit) -> None:
        """The editor closes only once the save succeeds; otherwise it keeps the edits."""
        editing = self.event_editor.editing()
        try:
            if editing is None:
                saved = await self.backend.create_event(edit)
            else:
                saved = await self.backend.save_event(editing.public_id, editing.revision, edit)
        except _STALE as exc:
            if not self._closing:
                self.event_editor.keep_open(_EVENT_STALE)
            await self._stale(exc)
            return
        except Exception:
            if not self._closing:
                self.event_editor.keep_open(_EDITOR_FAILED)
            raise  # _run logs it and reports the failure.
        self.event_editor.finish_saved()
        self._offer_undo()  # An edit has no undo; an older offer would refer to a past revision.
        self._show_calendar_day(saved.days(self.zone)[0])  # Show where it went.
        self.status.setText(f"Saved: {saved.title}.")
        await self._refresh_views()
        self.calendar_panel.select_event(saved.public_id)

    async def _delete_event(self, event: CalendarEvent) -> None:
        try:
            await self.backend.delete_event(event.public_id, event.revision)
        except _STALE as exc:
            await self._stale(exc)
            return
        public_id = event.public_id

        async def undo() -> None:
            await self.backend.restore_event(public_id)

        self._offer_undo("Undo delete", undo)
        self.status.setText(f"Deleted: {event.title}.")
        await self._refresh_views()

    # Google Calendar (ADR 0018, phase 2): read-only. Its failures are handled here, never
    # by _run, whose sign-in handling belongs to Gmail: a calendar problem must never show
    # Gmail as disconnected.

    async def _load_calendar_access(self) -> None:
        try:
            self._calendar_allowed = await self.backend.calendar_allowed()
        except Exception as exc:
            log_failure(exc)
            self._calendar_allowed = None

    def _google_mode(self) -> str:
        if self._account_email is None:
            return GOOGLE_OFF
        return GOOGLE_ASK if self._calendar_allowed is False else GOOGLE_ON

    def _google_note(self, shown: CalendarRange) -> str:
        account = self._account_email
        if account is None:
            return _CALENDAR_NOT_CONNECTED
        if self._calendar_allowed is False:
            return _CALENDAR_ASK
        coverage = shown.google_coverage
        if coverage is None:
            return f"Google Calendar: {account} · not loaded yet"
        if not coverage.covers(shown.first_day, shown.last_day):
            week = shown.last_day - shown.first_day < timedelta(days=7)  # Else a month's grid.
            days = "this week isn't" if week else "these six weeks aren't"
            return f"Google Calendar: {account} · {days} loaded yet"
        today = self._local_today()
        synced = coverage.synced_at_utc.astimezone(self.zone)
        when = (
            f"{synced:%H:%M}"
            if synced.date() == today
            else moment_text(coverage.synced_at_utc, self.zone, today=today)
        )
        return f"Google Calendar: {account} · updated {when}"

    def _calendar_sync_due(self) -> bool:
        shown = self._calendar_shown
        return (
            self._calendar_allowed is True
            and shown is not None
            and needs_sync(shown.google_coverage, shown.first_day, shown.last_day, self.now())
        )

    def _request_google(self, kind: str) -> None:
        async def refresh() -> None:
            await self._sync_calendar(quiet=False)

        if kind == ALLOW_CALENDAR:
            operation: Callable[[], Awaitable[None]] = self._allow_calendar
            cancelled = _CALENDAR_ALLOW_CANCELLED
        elif kind == REFRESH_CALENDAR:
            operation, cancelled = refresh, _CALENDAR_CANCELLED
        else:
            return
        started = self.start(self._calendar_work(operation), cancelled=cancelled)
        if not started and not self._closing:
            self.status.setText(_BUSY)

    def _calendar_failed(self, exc: Exception, *, quiet: bool = False) -> None:
        """Say what went wrong in fixed words; a quiet sync adds it to what the line says."""
        log_failure(exc)
        if isinstance(exc, CalendarNotAllowedError):
            self._calendar_allowed = False
            message = _CALENDAR_ASK
        elif isinstance(exc, ProviderPermissionError):
            message = str(exc)  # Static guidance by contract.
        elif isinstance(exc, ConfigurationError):
            message = configuration_guidance(exc)
        elif isinstance(exc, ProviderRateLimitError):
            message = _CALENDAR_BUSY
        else:
            message = _CALENDAR_UNREACHABLE
        self.status.setText(f"{self.status.text()} {message}" if quiet else message)

    async def _allow_calendar(self) -> None:
        """Ask Google, in the browser, to add Calendar to the one Google sign-in."""
        self.status.setText(_CALENDAR_ALLOWING)
        try:
            allowed = await self.backend.allow_calendar()
        except asyncio.CancelledError:
            raise
        except AuthenticationRequiredError as exc:
            # Cancelled, timed out or another account in the browser. Asking again never
            # touches the saved sign-in, so Gmail stays connected.
            log_failure(exc)
            self.status.setText(_CALENDAR_ALLOW_STOPPED)
            await self._refresh_calendar()
            return
        except Exception as exc:
            self._calendar_failed(exc)
            await self._refresh_calendar()
            return
        self._calendar_allowed = allowed
        if not allowed:
            self.status.setText(_CALENDAR_NOT_GRANTED)
            await self._refresh_calendar()
            return
        await self._sync_calendar(quiet=False)

    async def _sync_calendar(self, *, quiet: bool) -> None:
        """Read Google Calendar around the week shown; a quiet sync speaks only of trouble."""
        around = self._calendar_bounds()[0]
        try:
            result = await self.backend.sync_calendar(around, self.zone)
        except (asyncio.CancelledError, AuthenticationRequiredError):
            raise  # A sign-in problem is the one Google sign-in's: _run reports it.
        except Exception as exc:
            self._calendar_failed(exc, quiet=quiet)
        else:
            if result.stopped_code == "AUTH_REQUIRED":
                # Google rejected the one sign-in partway through: _run reports it as Gmail's.
                await self._refresh_calendar()
                raise AuthenticationRequiredError("Google rejected the sign-in.")
            if not quiet:
                self.status.setText(calendar_sync_text(result))
            elif result.failed or result.stopped_code:
                self.status.setText(f"{self.status.text()} {calendar_sync_text(result)}")
        await self._refresh_calendar()

    # Plan with AI (ADR 0018, phase 3b). Preparing and asking use start(); the dialog's
    # Cancel stops the request itself, like AI drafting's.

    def _request_planning(self) -> None:
        """Open the dialog; preparing and asking wait their turn through start()."""
        self._planning_plan = None
        self.planning_dialog.start_new(google_available=self._calendar_allowed is True)
        self.dialog_guard.show_dialog(self.planning_dialog)

    def _request_planning_preview(self, options: object) -> None:
        if isinstance(options, PlanningOptions):
            chosen = options
            started = self.start(lambda: self._prepare_planning(chosen), cancelled=_PLAN_CANCELLED)
            if not started:
                self.planning_dialog.show_problem(_BUSY)

    def _planning_google_due(self, options: PlanningOptions, first: date) -> bool:
        """Whether Google's saved copy misses any of the four weeks, or is old, when its
        times are to be sent."""
        shown = self._calendar_shown
        return (
            PlanningPart.GOOGLE_BUSY in options.parts
            and self._calendar_allowed is True
            and needs_sync(
                None if shown is None else shown.google_coverage,
                first,
                first + timedelta(days=PLANNING_DAYS - 1),
                self.now(),
            )
        )

    def _planning_first(self) -> date:
        """The Monday Plan with AI starts on: the week shown, or in the Month view the week
        of the day in focus."""
        if self.calendar_panel.view() is CalendarView.MONTH:
            return week_start(self._calendar_focus())
        return self._calendar_week or week_start(self._local_today())

    async def _prepare_planning(self, options: PlanningOptions) -> None:
        """Read Google first when its times are to be sent and the saved copy misses some of
        the four weeks; a failed read leaves what was saved, and the preview says which
        days it holds. Quitting stops it."""
        first = self._planning_first()
        if self._planning_google_due(options, first):
            self.planning_dialog.show_reading()
            try:
                await self._sync_calendar(quiet=True)
            except AuthenticationRequiredError:
                self.planning_dialog.show_problem(_PLAN_SIGN_IN)
                raise  # _run reports it as Gmail's, as for any calendar sync.
        try:
            plan = await self.backend.prepare_planning(options, first, self.zone)
        except ConfigurationError as exc:
            log_failure(exc)
            self.planning_dialog.show_problem(configuration_guidance(exc))
            return
        except Exception as exc:
            log_failure(exc)
            self.planning_dialog.show_problem(_PLAN_FAILED)
            return
        self._planning_plan = plan
        self.planning_dialog.show_preview(plan.preview)

    def _request_planning_ask(self, agreed: bool) -> None:
        if not self.start(lambda: self._ask_planning(agreed), cancellable=False):
            self.planning_dialog.show_problem(_BUSY)

    async def _ask_planning(self, agreed: bool) -> None:
        plan, self._planning_plan = self._planning_plan, None
        if plan is None:
            return
        self.planning_dialog.show_working()
        cancel = asyncio.Event()
        task = asyncio.create_task(
            self.backend.generate_planning(plan, _ApprovedPlanning(agreed), cancel)
        )
        self._planning_cancel, self._planning_task = cancel, task
        try:
            outcome = await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # This operation itself was cancelled.
            self.planning_dialog.show_problem(_PLAN_CANCELLED)
            return
        except Exception as exc:
            log_failure(exc)
            self.planning_dialog.show_problem(_PLAN_FAILED)
            return
        finally:
            self._planning_cancel = self._planning_task = None
        if outcome.status is PlanningStatus.PROPOSED:
            assert outcome.plan is not None
            prepared = plan

            def overlaps(
                changes: Sequence[EventChange], ticked: Sequence[bool]
            ) -> tuple[tuple[str, ...], ...]:
                return overlaps_for(changes, ticked, prepared)

            self.planning_dialog.show_proposals(
                outcome.plan, self.zone, self._local_today(), overlaps
            )
        elif outcome.status is PlanningStatus.DECLINED:
            self.planning_dialog.show_problem(_PLAN_DECLINED)
        elif outcome.status is PlanningStatus.CANCELLED:
            self.planning_dialog.show_problem(_PLAN_CANCELLED)
        else:
            self.planning_dialog.show_problem(planning_problem_text(outcome.error_code))

    def _cancel_planning(self) -> None:
        if self._planning_cancel is not None:
            self._planning_cancel.set()
        if self._planning_task is not None:
            self._planning_task.cancel()

    def _request_apply_planning(self, changes: object) -> None:
        if isinstance(changes, tuple) and changes:
            kept: tuple[EventChange, ...] = changes
            if not self.start(lambda: self._apply_planning(kept), cancellable=False):
                self.planning_dialog.keep_reviewing(_BUSY)

    async def _apply_planning(self, changes: tuple[EventChange, ...]) -> None:
        try:
            batch = await self.backend.apply_calendar_changes(changes)
        except _STALE as exc:
            log_failure(exc)
            self.planning_dialog.keep_reviewing(_PLAN_STALE)
            await self._refresh_calendar()
            return
        except Exception as exc:
            log_failure(exc)
            self.planning_dialog.keep_reviewing(_PLAN_FAILED)
            return
        self.planning_dialog.accept()
        self.planning_dialog.forget_instruction()

        async def undo() -> None:
            await self.backend.undo_calendar_changes(batch)

        self._offer_undo("Undo AI changes", undo)
        count = len(batch.changes)
        self.status.setText(f"Applied {count} change{'s' if count != 1 else ''} from Groq.")
        await self._refresh_views()

    def _open_calendar_link(self, event: GoogleEvent) -> None:
        link = None if event.web_link is None else str(event.web_link)
        if link is not None and is_calendar_link(link):
            QDesktopServices.openUrl(QUrl(link))

    # Drafts. Creating, opening and deleting use start(); the open draft's writes go
    # through draft_writes, so typing never makes the window busy.

    def _request_reply(self, account_email: str, message_id: str) -> None:
        self._start_from_link(
            lambda: self._open_new_draft(
                lambda: self.backend.create_reply_draft(account_email, message_id)
            )
        )

    def _request_action_draft(self, kind: DraftKind, action: Action) -> None:
        public_id = action.public_id
        self.start(
            lambda: self._open_new_draft(
                lambda: self.backend.create_draft_for_action(public_id, kind)
            ),
            cancellable=False,
        )

    def _request_draft(self, kind: str, value: object) -> None:
        if kind == NEW_DRAFT and isinstance(value, DraftKind):
            new_kind = value
            self.start(
                lambda: self._open_new_draft(lambda: self.backend.create_draft(new_kind)),
                cancellable=False,
            )
        elif isinstance(value, DraftSummary):
            summary = value
            if kind == OPEN_DRAFT:
                self.start(lambda: self._open_draft(summary), cancellable=False)
            elif kind == DELETE_DRAFT:
                self.start(lambda: self._delete_draft(summary), cancellable=False)

    async def _open_new_draft(self, create: Callable[[], Awaitable[Draft]]) -> None:
        try:
            draft = await create()
        except SourceNotFoundError as exc:
            log_failure(exc)
            self.status.setText(_NO_SOURCE)
            return
        except _STALE as exc:
            await self._stale(exc)
            return
        self._edit_draft(draft)
        self.status.setText(f"{KIND_NAMES[draft.kind]} draft started. It saves as you type.")
        await self._refresh_drafts()

    async def _open_draft(self, summary: DraftSummary) -> None:
        try:
            draft = await self.backend.get_draft(summary.public_id)
        except _STALE as exc:
            await self._stale(exc)
            return
        self._edit_draft(draft)

    def _edit_draft(self, draft: Draft) -> None:
        self.draft_editor.load(draft, self.zone)
        self.dialog_guard.show_dialog(self.draft_editor)

    # Writing with AI. Every step runs in draft_writes after pending autosaves; the
    # generation itself is a task of its own, so Cancel and quitting can stop it.

    async def _offer_drafting(self) -> None:
        editor = self.draft_editor
        draft = editor.draft
        if draft is None:
            return
        try:
            parts = await self.backend.available_drafting_parts(draft.public_id)
        except ConfigurationError as exc:
            log_failure(exc)
            editor.ai_failed(configuration_guidance(exc))
            return
        except Exception as exc:
            log_failure(exc)
            editor.ai_failed(_AI_FAILED)
            return
        editor.offer_ai(parts)

    async def _prepare_drafting(self, options: DraftingOptions) -> None:
        editor = self.draft_editor
        draft = editor.draft
        if draft is None or not editor.ai_active():
            return
        try:
            plan = await self.backend.prepare_drafting(draft.public_id, options)
        except DraftingContextError as exc:
            log_failure(exc)
            editor.ai_failed(str(exc))  # Static messages that name no mail content.
            return
        except _DRAFT_STALE as exc:
            log_failure(exc)
            editor.show_conflict()
            editor.ai_failed("")
            return
        except AuthenticationRequiredError as exc:
            log_failure(exc)
            editor.ai_failed("Connect Gmail to download the email, then try again.")
            return
        except ConfigurationError as exc:
            log_failure(exc)
            editor.ai_failed(configuration_guidance(exc))
            return
        except ProviderError as exc:
            log_failure(exc)
            editor.ai_failed("Gmail is offline or unavailable, so the email couldn't be read.")
            return
        except Exception as exc:
            log_failure(exc)
            editor.ai_failed(_AI_FAILED)
            return
        if not editor.ai_active():
            return  # Cancelled while preparing.
        self._drafting_plan = plan
        editor.show_ai_preview(plan.preview, drafting_disclosure_lines(plan.preview))

    async def _generate_draft(self, agreed: bool) -> None:
        editor = self.draft_editor
        plan, self._drafting_plan = self._drafting_plan, None
        if plan is None or not editor.ai_active():
            return
        cancel = asyncio.Event()
        task = asyncio.create_task(self.backend.generate_draft(plan, _ApprovedGate(agreed), cancel))
        self._drafting_cancel, self._drafting_task = cancel, task
        try:
            outcome = await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # This queue task itself was cancelled.
            await self._after_cancelled_generation(plan)
            return
        except Exception as exc:
            log_failure(exc)
            editor.ai_failed(_AI_FAILED)
            return
        finally:
            self._drafting_cancel = self._drafting_task = None
        if outcome.status is DraftingStatus.GENERATED:
            assert outcome.draft is not None and outcome.version_number is not None
            editor.ai_generated(
                outcome.draft,
                outcome.version_number,
                outcome.previous_version,
                outcome.missing_context,
            )
            await self._refresh_drafts()
        elif outcome.status is DraftingStatus.DECLINED:
            editor.ai_failed(_AI_DECLINED)
        elif outcome.status is DraftingStatus.CANCELLED:
            editor.ai_failed(_AI_CANCELLED)
        else:
            editor.ai_failed(error_guidance(outcome.error_code or "AI_PROVIDER_ERROR"))

    async def _after_cancelled_generation(self, plan: DraftingPlan) -> None:
        """Cancel can arrive just as Groq's text is saved; show whatever was stored."""
        editor = self.draft_editor
        try:
            stored = await self.backend.get_draft(plan.public_id)
        except Exception as exc:
            log_failure(exc)
            return
        current = editor.draft
        if (
            current is not None
            and current.public_id == stored.public_id
            and stored.revision != current.revision
            and not self._closing
        ):
            editor.restored(stored, 0)
            editor.set_status(
                "Groq finished before Cancel took effect; Versions has your earlier text."
            )

    def _cancel_drafting(self) -> None:
        """Drop a previewed plan and stop a generation; nothing is written after this."""
        self._drafting_plan = None
        if self._drafting_cancel is not None:
            self._drafting_cancel.set()
        if self._drafting_task is not None and not self._drafting_task.done():
            self._drafting_task.cancel()

    async def _delete_draft(self, summary: DraftSummary) -> None:
        try:
            await self.backend.delete_draft(summary.public_id, summary.revision)
        except _STALE as exc:
            await self._stale(exc)
            return
        public_id = summary.public_id

        async def undo() -> None:
            await self.backend.restore_draft(public_id)

        self._offer_undo("Undo delete", undo)
        self.status.setText(f"Deleted: {summary.display_title}.")
        await self._refresh_views()

    def _connect_draft_editor(self, editor: DraftEditor) -> None:
        writes = self.draft_writes
        editor.autosave_requested.connect(self._queue_autosave)
        editor.checkpoint_requested.connect(lambda: writes.run(self._checkpoint_draft))
        editor.versions_requested.connect(lambda: writes.run(self._list_draft_versions))
        editor.version_requested.connect(
            lambda number: writes.run(lambda: self._show_draft_version(number))
        )
        editor.restore_requested.connect(
            lambda number: writes.run(lambda: self._restore_draft_version(number))
        )
        editor.save_as_new_requested.connect(
            lambda edit: writes.run(lambda: self._save_draft_as_new(edit))
        )
        editor.export_requested.connect(
            lambda path, text: writes.run(lambda: self._export_draft(Path(path), text))
        )
        editor.close_requested.connect(lambda edit: writes.run(lambda: self._close_draft(edit)))
        editor.ai_parts_requested.connect(lambda: writes.run(self._offer_drafting))
        editor.ai_prepare_requested.connect(
            lambda options: writes.run(lambda: self._prepare_drafting(options))
        )
        editor.ai_generate_requested.connect(
            lambda agreed: writes.run(lambda: self._generate_draft(agreed))
        )
        editor.ai_cancel_requested.connect(self._cancel_drafting)

    def _queue_autosave(self, edit: DraftEdit) -> None:
        draft = self.draft_editor.draft
        if draft is not None:
            public_id = draft.public_id
            self.draft_writes.autosave(lambda: self._autosave_draft(public_id, edit))

    async def _autosave_draft(self, public_id: str, edit: DraftEdit) -> bool:
        """Save ``edit`` at the editor's latest revision; False when it was not saved."""
        editor = self.draft_editor
        draft = editor.draft
        if draft is None or draft.public_id != public_id or editor.in_conflict:
            return False
        try:
            saved = await self.backend.autosave_draft(public_id, draft.revision, edit)
        except _DRAFT_STALE as exc:
            self._unsaved_drafts.add(public_id)
            log_failure(exc)
            editor.show_conflict()
            return False
        except Exception as exc:
            self._unsaved_drafts.add(public_id)
            log_failure(exc)
            editor.save_failed()
            return False
        editor.saved(saved, edit)
        self._unsaved_drafts.discard(public_id)
        return True

    def _writable_draft(self) -> Draft | None:
        editor = self.draft_editor
        return None if editor.in_conflict else editor.draft

    async def _checkpoint_draft(self) -> None:
        editor = self.draft_editor
        draft = self._writable_draft()
        if draft is None:
            return
        try:
            info = await self.backend.checkpoint_draft(draft.public_id, draft.revision)
        except _DRAFT_STALE as exc:
            log_failure(exc)
            editor.show_conflict()
            return
        except Exception as exc:
            log_failure(exc)
            editor.set_status("Couldn't save a version; try again.")
            return
        editor.checkpointed(info)

    async def _list_draft_versions(self) -> None:
        editor = self.draft_editor
        draft = editor.draft
        if draft is None:
            return
        try:
            versions = await self.backend.draft_versions(draft.public_id)
        except Exception as exc:
            log_failure(exc)
            editor.set_status("Couldn't load the saved versions; try again.")
            return
        editor.show_versions(versions, self._local_today())

    async def _show_draft_version(self, number: int) -> None:
        editor = self.draft_editor
        draft = editor.draft
        if draft is None:
            return
        try:
            version = await self.backend.draft_version(draft.public_id, number)
        except Exception as exc:
            log_failure(exc)
            editor.set_status("Couldn't load that version; try again.")
            return
        editor.show_version(version)

    async def _restore_draft_version(self, number: int) -> None:
        editor = self.draft_editor
        draft = self._writable_draft()
        if draft is None:
            editor.restore_failed("Save your text as a new draft first.")
            return
        try:
            restored = await self.backend.restore_draft_version(
                draft.public_id, draft.revision, number
            )
        except _DRAFT_STALE as exc:
            log_failure(exc)
            editor.show_conflict()
            editor.restore_failed("")
            return
        except Exception as exc:
            log_failure(exc)
            editor.restore_failed("Couldn't restore that version; try again.")
            return
        editor.restored(restored, number)

    async def _save_draft_as_new(self, edit: DraftEdit) -> None:
        editor = self.draft_editor
        draft = editor.draft
        if draft is None:
            return
        try:
            copy = await self.backend.save_draft_as_new(draft.public_id, edit)
            self._unsaved_drafts.discard(draft.public_id)
        except Exception as exc:
            log_failure(exc)
            editor.set_status("Couldn't save a new draft; try again.")
            return
        editor.continue_as(copy)
        await self._refresh_drafts()

    async def _export_draft(self, path: Path, text: str) -> None:
        """Write the owner's chosen file in a worker thread, replacing it atomically."""
        editor = self.draft_editor
        try:
            await asyncio.to_thread(write_text_atomically, path, text, overwrite=True)
        except Exception as exc:
            log_failure(exc)
            editor.set_status(_DRAFT_EXPORT_FAILED)
            return
        count = len(placeholders(text))
        editor.set_status(f"Exported {path.name}." + (f" {still_to_fill(count)}" if count else ""))

    async def _close_draft(self, edit: DraftEdit | None) -> None:
        """Save any unsaved text and a version, then let the editor close."""
        editor = self.draft_editor
        draft = editor.draft
        if draft is None:
            editor.finish_closed()
            return
        if edit is not None and not await self._autosave_draft(draft.public_id, edit):
            editor.cancel_close(None if editor.in_conflict else _DRAFT_CLOSE_FAILED)
            return
        draft = editor.draft
        assert draft is not None
        try:
            await self.backend.checkpoint_draft(draft.public_id, draft.revision)
        except _DRAFT_STALE as exc:
            log_failure(exc)
            editor.show_conflict()
            editor.cancel_close()
            return
        except Exception as exc:
            log_failure(exc)
            editor.cancel_close(_DRAFT_CLOSE_FAILED)
            return
        editor.finish_closed()
        if not self._closing:
            self.status.setText(f"Saved: {draft.display_title}.")
            await self._refresh_drafts()

    def start(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        cancellable: bool = True,
        cancelled: str = _CANCELLED,
    ) -> bool:
        """Only one operation may own the providers and workflow at a time.

        Returns whether this operation started; it does not while closing or busy.
        ``cancelled`` is what the status line says when Cancel or quitting stops it.
        """
        if self._closing or (self.task is not None and not self.task.done()):
            return False
        self._cancel = asyncio.Event()
        self._cancellable = cancellable
        self._set_busy(True)
        self.task = asyncio.create_task(self._run(operation, cancelled))
        return True

    async def _run(self, operation: Callable[[], Awaitable[None]], cancelled: str) -> None:
        failed = True
        try:
            await operation()
            failed = False
        except asyncio.CancelledError:
            failed = False
            self.status.setText(cancelled)
        except AuthenticationRequiredError as exc:
            log_failure(exc)
            self._set_account(None)
            # The one Google sign-in went with it: no Google times for Plan with AI, and the
            # page reads again without the saved Google events the backend forgot.
            self._calendar_allowed = None
            self._read_calendar()
            self.connection.setText("Gmail: session expired or missing. Connect Gmail to continue.")
            self.status.setText("Sign in to Gmail, then retry. The saved brief is still available.")
        except ConfigurationError as exc:
            log_failure(exc)
            self.status.setText(configuration_guidance(exc))
        except BriefDateError as exc:
            log_failure(exc)
            self.status.setText(str(exc))  # Static: the dates that can be briefed.
        except ProviderError as exc:
            log_failure(exc)
            self.status.setText("Provider unavailable. Check your connection and retry.")
        except Exception as exc:
            log_failure(exc)
            # Validation and HTTP exceptions can contain secret or mail-derived values.
            self.status.setText("Operation failed. The displayed saved brief is unchanged.")
        finally:
            if self._automatic_active:
                self._automatic_active = False
                if failed:
                    self.status.setText(_AUTOMATIC + self.status.text())
            if self.cached_dialog.isVisible() and not self.cached_dialog.has_page:
                self.cached_dialog.status.setText(self.status.text())
            if self.settings_dialog.isVisible():
                self.settings_dialog.status.setText(self.status.text())
            self.review_panel.hide()
            self.consent_panel.hide()
            self.shortlist.clear()
            self.disclosure.setText("")
            self._review = None
            self._consent = None
            self._leave_run_page()
            self._set_busy(False)

    async def _open_cached(self) -> None:
        self.cached_dialog.show_today()  # Today in the owner's zone, each time it opens.
        self.cached_dialog.configure(await self.backend.cached_accounts())
        self.dialog_guard.show_dialog(self.cached_dialog)
        selected = self.cached_dialog.selection()
        if selected is not None:
            await self._load_cached_page(*selected, 0)
        else:
            self.status.setText("No cached Gmail accounts. Connect and sync to save metadata.")

    def _request_cached_page(self, account_id: int, day: date, offset: int) -> None:
        self.start(lambda: self._load_cached_page(account_id, day, offset))

    async def _load_cached_page(self, account_id: int, day: date, offset: int) -> None:
        self.cached_dialog.begin_load()
        self.cached_dialog.show_page(await self.backend.cached_messages(account_id, day, offset))
        self.status.setText("Showing saved metadata. No mailbox connection or body retrieval used.")

    def _request_settings(self) -> None:
        """Settings opens at once, even while an operation (a sync, say) runs: opening only
        reads, and what changes settings waits in the dialog until the operation ends. A
        second request while it is still opening (a double-click) does nothing."""
        opening = self._settings_opening
        if (
            self._closing
            or self.settings_dialog.isVisible()
            or (opening is not None and not opening.done())
        ):
            return
        if self.start(self._open_settings):
            self._settings_opening = self.task
            return

        async def beside() -> None:
            try:
                await self._open_settings(beside=True)
            except Exception as exc:  # Nothing else reports a read beside an operation.
                log_failure(exc)
                self.status.setText("Settings couldn't be opened just now. Try again.")

        task = asyncio.get_running_loop().create_task(beside())
        self._settings_opening = task
        self._calendar_reads.add(task)
        task.add_done_callback(self._calendar_reads.discard)

    async def _open_settings(self, *, beside: bool = False) -> None:
        """Read what Settings shows, then show it. ``beside`` another operation, the status
        line keeps that operation's words, and the dialog waits for it to finish."""
        try:
            preferences = await self.backend.get_preferences()
        except ConfigurationError as exc:
            log_failure(exc)
            # A corrupt settings file must remain repairable from the editor.
            preferences = DesktopPreferences()
        self.settings_dialog.set_preferences(preferences)
        appearance_panel = self.settings_dialog.appearance_panel
        appearance_panel.set_scheme(current_scheme())
        appearance_panel.set_mode(self.appearance.mode)
        appearance_panel.set_choices(self.appearance.transparency, self.appearance.motion)
        panel = self.settings_dialog.preferences_panel
        panel.set_zones(str(resolve_timezone(None)), await asyncio.to_thread(region_zones))
        await self._show_auto_send()
        if not beside:
            self.status.setText("Edit connection and AI settings, and your preferences.")
        unavailable: str | None = None
        try:
            panel.set_preferences(await self.backend.get_owner_preferences())
        except PreferencesUnavailableError as exc:
            # Unreadable preferences stay repairable: the panel offers a reset.
            log_failure(exc)
            panel.set_unavailable()
            unavailable = str(exc)
            if not beside:
                self.status.setText(unavailable)
        if beside:  # Loading cleared its status line; the operation may still be running.
            self.settings_dialog.set_busy(self.task is not None and not self.task.done())
            if unavailable is not None:  # In the dialog: the status line is the operation's.
                self.settings_dialog.status.setText(unavailable)
        if self._closing:
            return  # Quitting while it read: there is nothing left to show it over.
        self.dialog_guard.show_dialog(self.settings_dialog)

    def _set_dialog_zones(self) -> None:
        self.cached_dialog.zone = self.zone
        if self.data_dialog is not None:
            self.data_dialog.zone = self.zone

    def _apply_owner_preferences(self, preferences: OwnerPreferences) -> None:
        """The owner's zone for every local day and time shown, and the drafting defaults."""
        self.zone = owner_zone(preferences)
        self.inbox_panel.set_zone(self.zone)
        self._set_dialog_zones()
        self._render_checked()  # In the new zone.
        self.draft_editor.ai_panel.set_defaults(preferences.draft_tone, preferences.draft_length)
        self.scheduler.configure(
            preferences.refresh_on_launch, preferences.refresh_interval_minutes
        )

    async def _load_owner_preferences(self) -> None:
        """Views only display local data, so unreadable preferences fall back to the system
        zone here; a brief or AI drafting still refuses to run until they are reset."""
        try:
            preferences = await self.backend.get_owner_preferences()
        except Exception as exc:
            log_failure(exc)
            self._apply_owner_preferences(OwnerPreferences.defaults())
            if isinstance(exc, PreferencesUnavailableError):
                self.status.setText(str(exc))
            return
        self._apply_owner_preferences(preferences)

    def _request_save_owner_preferences(self, edit: PreferencesEdit, revision: int) -> None:
        self.start(lambda: self._save_owner_preferences(edit, revision), cancellable=False)

    async def _save_owner_preferences(self, edit: PreferencesEdit, revision: int) -> None:
        try:
            saved = await self.backend.save_owner_preferences(edit, revision)
        except PreferencesConflictError as exc:
            log_failure(exc)
            self.status.setText(str(exc))  # Static; _run shows it in the dialog too.
            return
        await self._owner_preferences_changed(saved, "Preferences saved.")

    async def _reset_owner_preferences(self) -> None:
        saved = await self.backend.reset_owner_preferences()
        await self._owner_preferences_changed(saved, "Preferences reset to the defaults.")

    async def _owner_preferences_changed(self, saved: OwnerPreferences, message: str) -> None:
        self._apply_owner_preferences(saved)
        self.settings_dialog.preferences_panel.set_preferences(saved)
        self.status.setText(message)
        await self._refresh_views()

    def _change_scheme(self, scheme: ColorScheme) -> None:
        """Apply the owner's colours at once, whatever else is running, in the mode in
        effect, then save them."""
        if self._retheme(scheme, current_mode()):
            self._save_appearance(self.appearance.model_copy(update={"color_scheme": scheme}))

    def _change_mode(self, choice: ModeChoice) -> None:
        """Apply light, dark or the system's mode at once, then save the choice."""
        if self._retheme(current_scheme(), self._theme_mode(choice)):
            self._save_appearance(self.appearance.model_copy(update={"mode": choice}))

    def _system_mode_changed(self, scheme: Qt.ColorScheme) -> None:
        """The system went light or dark: follow it, unless the owner keeps one mode."""
        if self.appearance.mode is ModeChoice.SYSTEM:
            self._retheme(current_scheme(), self._theme_mode(ModeChoice.SYSTEM))

    def _theme_mode(self, choice: ModeChoice) -> ThemeMode:
        application = QApplication.instance()
        system = (
            system_mode(application) if isinstance(application, QApplication) else current_mode()
        )
        return theme_mode(choice, system)

    def _retheme(self, scheme: ColorScheme, mode: ThemeMode) -> bool:
        """Apply ``scheme`` in ``mode``; False, with the panel showing what is in effect,
        when it fails (apply_theme has put back what was there)."""
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            return False
        if scheme is current_scheme() and mode is current_mode():
            return True
        try:
            apply_theme(application, mode, scheme)
        except Exception as exc:
            log_failure(exc)
            panel = self.settings_dialog.appearance_panel
            panel.set_scheme(current_scheme())
            panel.set_mode(self.appearance.mode)
            self.settings_dialog.status.setText("The colours couldn't be changed.")
            return False
        return True

    def _change_transparency(self, transparency: Transparency) -> None:
        self.glass_accessibility.set_choices(transparency, self.appearance.motion)
        self._save_appearance(self.appearance.model_copy(update={"transparency": transparency}))

    def _change_motion(self, motion: Motion) -> None:
        self.glass_accessibility.set_choices(self.appearance.transparency, motion)
        self._save_appearance(self.appearance.model_copy(update={"motion": motion}))

    def _save_appearance(self, appearance: Appearance) -> None:
        self.appearance = appearance
        if self.appearance_writes is not None:
            self.appearance_writes.save(appearance)

    def _appearance_not_saved(self) -> None:
        """Said in Settings (opening it clears its line), and on the status line when
        Settings is closed: a switch of the Calendar's view saves too."""
        self.settings_dialog.status.setText(
            "The appearance couldn't be saved. It stays until MailBrief closes."
        )
        if not self.settings_dialog.isVisible():
            self.status.setText(
                "Your appearance and Calendar view couldn't be saved on this computer. They "
                "stay until MailBrief closes."
            )

    def _request_save_preferences(self, preferences: DesktopPreferences) -> None:
        self.start(lambda: self._save_preferences(preferences), cancellable=False)

    async def _save_preferences(self, preferences: DesktopPreferences) -> None:
        await self.backend.save_preferences(preferences)
        self.status.setText("Settings saved. Connect Gmail to verify the selected OAuth client.")
        self.connection.setText("Gmail: settings updated; connect to verify.")
        await self._refresh_ai_status()

    def _request_save_key(self, key: SecretStr) -> None:
        self.start(lambda: self._save_key(key), cancellable=False)

    async def _save_key(self, key: SecretStr) -> None:
        await self.backend.save_key(key)
        self.status.setText("Groq key saved in the OS credential store.")
        await self._refresh_ai_status()

    async def _remove_key(self) -> None:
        await self.backend.remove_key()
        self.status.setText("Groq key removed from the OS credential store.")
        await self._refresh_ai_status()

    async def _revoke_consent(self) -> None:
        count = await self.backend.revoke_consent()
        self.status.setText(
            f"AI consent revoked for briefs and drafting ({count} consent records). "
            "MailBrief asks again before sending anything."
        )

    async def _refresh_ai_status(self) -> None:
        try:
            self.ai.setText(await self.backend.ai_status())
            self.draft_editor.set_ai_ready(await self.backend.drafting_ready())
        except Exception as exc:
            log_failure(exc)
            self.ai.setText("AI: configuration or secure key store unavailable.")
            self.draft_editor.set_ai_ready(False)
            if not self.settings_dialog.isVisible():  # The AI line is in Settings.
                self.status.setText(self.ai.text())

    async def initialize(self) -> None:
        """Load what is saved, then check the saved Google session. Google sign-in shows in
        the sidebar once that check is over, however it ended."""
        try:
            await self._load_and_connect()
        finally:
            self.connect_button.setVisible(self._account_email is None)

    async def _load_and_connect(self) -> None:
        try:
            saved = await self.backend.load_saved()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_failure(exc)
            self.connection.setText("Gmail: waiting for local storage.")
            self.ai.setText("AI: waiting for local storage.")
            self.status.setText(
                "Saved data could not be opened. The database may be newer than this app or "
                "unreadable. Use the latest MailBrief, check disk access, then Retry loading "
                "saved data. Do not delete your database."
            )
            self.workspace.clear("Saved brief unavailable until local storage can be opened.")
            return
        self._ready = True
        self.status.setText("Ready. Sync to review today's messages.")
        await self._load_owner_preferences()
        # The day the views below are built for, in the owner's zone (just applied): in the
        # system's zone it can already be tomorrow, and that midnight would go unnoticed.
        self._day = self._local_today()
        if saved is not None:
            await self._show_digest(saved)
        else:
            self.workspace.clear(
                "No saved brief yet. Connect Gmail, then sync and review your shortlist."
            )
        await self._refresh_actions()
        await self._refresh_drafts()
        await self._refresh_calendar()
        await self._refresh_ai_status()
        try:
            email = await self.backend.connect(silent_only=True)
        except AuthenticationRequiredError as exc:
            log_failure(exc)
            self.connection.setText("Gmail: session expired or missing. Connect Gmail to continue.")
        except ConfigurationError as exc:
            log_failure(exc)
            self.connection.setText("Gmail: " + configuration_guidance(exc))
        except Exception as exc:
            log_failure(exc)
            self.connection.setText("Gmail: offline or unavailable. Saved brief available locally.")
        else:
            self._set_account(email)
            self.connection.setText(f"Gmail: connected as {email}")
            await self._refresh_inbox(quiet=True)
            if self._account_email != email or self._cancel.is_set():
                return
            await self._load_calendar_access()
            await self._refresh_calendar()
            self._launch_soon()
            return
        # The Gmail line is in Settings: the status line says why Gmail isn't connected.
        self.status.setText(self.connection.text())

    def _launch_soon(self) -> None:
        """MailBrief has started and Gmail is connected: let the scheduler run the refresh the
        owner asked for on launch, once this operation has finished, so it isn't turned away
        as busy."""
        asyncio.get_running_loop().call_soon(self.scheduler.launched)

    def _sign_in_from_settings(self) -> None:
        """Settings' Sign in with Google signs in with the saved settings and closes
        Settings, so the window's status line and Cancel follow the sign-in. A change not
        yet saved there stops it, rather than being ignored or lost with the dialog."""
        if self.settings_dialog.has_unsaved_input():
            self.settings_dialog.status.setText(SIGN_IN_UNSAVED)
            return
        if self.start(self._connect):
            self.settings_dialog.reject()

    async def _connect(self) -> None:
        origin = self.workspace.current_page()
        self.status.setText("Connecting to Gmail. Complete sign-in in your browser.")
        email = await self.backend.connect(silent_only=False)
        self._set_account(email)
        self.connection.setText(f"Gmail: connected as {email}")
        self.inbox_panel.clear()
        if origin == "today":
            self._show_page_unless_moved(origin, "inbox")
        await self._refresh_inbox()
        if self._account_email != email or self._cancel.is_set():
            return
        await self._load_calendar_access()
        await self._refresh_calendar()
        if self._calendar_allowed:
            await self._sync_calendar(quiet=True)
        await self._show_auto_send()  # The permission shown is the connected account's.
        await self._reload_history()  # The new account's missed days.
        self._launch_soon()  # Only the first connection of a start counts as the launch.

    async def _disconnect(self) -> None:
        await self.backend.disconnect()
        self._set_account(None)
        self.inbox_panel.clear()
        self.workspace.header.set_alert("")
        self.connection.setText("Gmail: disconnected")
        self.status.setText("Local credentials removed. Saved briefs remain on this device.")
        self._calendar_allowed = None
        await self._refresh_calendar()  # Its Google events went with the sign-in.
        await self._show_auto_send()

    async def _load_inbox(self, offset: int = 0, *, select: str | None = None) -> None:
        if self._account_email is None:
            self.inbox_panel.clear()
            return
        self.inbox_panel.zone = self.zone
        self.inbox_panel.show_page(
            await self.backend.inbox_messages(self._account_email, offset), select=select
        )

    async def _set_wanted(self, message_id: str, wanted: bool) -> None:
        """Mark or unmark an Inbox message as one that should have been in a brief (ADR 0020),
        then show the same page with it still selected."""
        if self._account_email is None:
            self.inbox_panel.clear()
            return
        await self.backend.set_wanted(self._account_email, message_id, wanted)
        page = self.inbox_panel.page
        self.inbox_panel.show_page(
            await self.backend.inbox_messages(
                self._account_email,
                0 if page is None else page.offset,
                query=page.query if page else "",
            ),
            select=message_id,
        )
        self.status.setText(
            "Marked: MailBrief counts this when ranking its sender's mail."
            if wanted
            else "Mark removed."
        )

    async def _load_more_inbox(self) -> None:
        await self._refresh_inbox(load_more=True)

    async def _refresh_inbox(
        self,
        *,
        quiet: bool = False,
        load_more: bool = False,
        query: str | None = None,
    ) -> None:
        previous_status = self.status.text()
        email = self._account_email
        if email is None:
            self.status.setText("Sign in with Google to refresh your Inbox.")
            return
        current = self.inbox_panel.page
        if query is None:
            query = current.query if current is not None and not quiet else ""
        show = not (quiet and current is not None and current.query)
        if show and not load_more and not query:
            await self._load_inbox()
        self.inbox_panel.set_busy(True)
        self.status.setText(
            "Loading more mail…"
            if load_more
            else "Searching all mail…"
            if query
            else "Loading recent mail…"
        )
        try:
            page = await self.backend.browse_inbox(
                email, self._cancel, load_more=load_more, query=query
            )
        except (ConfigurationError, AuthenticationRequiredError) as exc:
            self._set_account(None)
            self._calendar_allowed = None
            self._read_calendar()
            guidance = (
                configuration_guidance(exc)
                if isinstance(exc, ConfigurationError)
                else ("Sign in with Google to continue.")
            )
            self.inbox_panel.clear(guidance)
            self.workspace.header.set_alert("")
            self.connection.setText("Gmail: " + guidance)
            self.status.setText(guidance)
            return
        except ProviderError as exc:
            log_failure(exc)
            self.status.setText(
                "Couldn't load mail. Showing saved messages. Retry, or Refresh to restart."
            )
            self.workspace.header.set_alert(INBOX_INCOMPLETE, self.status.text())
            return
        if email != self._account_email or self._cancel.is_set():
            return
        if show:
            self.inbox_panel.show_page(page)
        if page.error_code:
            text = "Some messages couldn't be read. Retry this page."
            self.workspace.header.set_alert(INBOX_INCOMPLETE, text)
            self.status.setText(previous_status if quiet else text)
        else:
            self.workspace.header.set_alert("")
            self.status.setText(
                previous_status if quiet else f"{len(page.messages)} messages loaded."
            )

    # Saved briefs by day. Opening one needs no connection; briefing a past day is the
    # owner's explicit choice, one day at a time, for the connected account.

    async def _load_history(self) -> None:
        briefs = await self.backend.list_briefs()
        email = self._account_email
        missed = await self.backend.missed_days(email) if email is not None else ()
        today = self.now().astimezone(self.zone).date()
        self.history_panel.configure(briefs, missed, email, today)

    async def _reload_history(self) -> None:
        """Reload the Briefs page when it shows. The page is only a view: when it can't be
        read, it stays as it was and the status line says so."""
        if self.workspace.current_page() != "briefs":
            return
        try:
            await self._load_history()
        except Exception as exc:
            self._note_not_refreshed(exc)

    def _show_page_unless_moved(self, origin: str, key: str) -> bool:
        """Show ``key`` once a load finishes, unless the owner moved from ``origin``, the
        page shown when it was requested, in the meantime; returns whether it did."""
        if self.workspace.current_page() != origin:
            return False
        self._show_page(key)
        return True

    async def _open_history(self) -> None:
        origin = self.workspace.current_page()
        await self._load_history()
        if self._show_page_unless_moved(origin, "briefs"):
            self.status.setText("Open a saved brief, or brief a missed day.")

    def _request_open_brief(self, account_email: str, local_date: date) -> None:
        self.start(lambda: self._show_brief(account_email, local_date), cancellable=False)

    async def _show_brief(self, account_email: str, local_date: date) -> None:
        origin = self.workspace.current_page()
        digest = await self.backend.load_brief(account_email, local_date)
        if digest is None:
            self.status.setText("That brief is no longer saved.")
            return
        await self._view(digest, origin)  # Back on Today.
        self.status.setText(
            f"Showing the brief for {day_text(local_date, today=self._local_today())}."
        )

    async def _view(self, digest: DailyDigest, origin: str | None = None) -> None:
        """Show a brief; the banner appears unless it is the latest. Today shows it unless
        the owner moved from ``origin`` (by default, the page shown now) while it loaded."""
        origin = self.workspace.current_page() if origin is None else origin
        latest = await self.backend.load_saved()
        identity = (digest.account_id, digest.local_date)
        is_latest = latest is not None and (latest.account_id, latest.local_date) == identity
        self._set_shown(None if is_latest else identity)
        await self._show_digest(digest)
        self._show_page_unless_moved(origin, "today")

    async def _back_to_latest(self) -> None:
        origin = self.workspace.current_page()
        self._set_shown(None)
        await self._reload_brief()
        self._show_page_unless_moved(origin, "today")
        self.status.setText("Showing the latest brief.")

    def _request_brief_day(self, local_date: date) -> None:
        if self._account_email is None:
            self.history_panel.status.setText(NEEDS_CONNECTION)
            return
        self.start(lambda: self._generate(local_date))

    async def _generate(self, local_date: date | None = None) -> None:
        """Brief today (Sync and review), or a past day chosen on the Briefs page. However
        it ends, the next automatic run is an interval later (ADR 0017)."""
        self._synced_at = None  # Until this run's review opens.
        try:
            await self._generate_brief(local_date)
        finally:
            self.scheduler.note_run(self.now())

    async def _generate_brief(self, local_date: date | None = None) -> None:
        self._offer_undo()  # A new brief replaces the suggestions that Undo would refer to.
        if local_date is None and self._shown is not None:
            self._set_shown(None)  # Sync and review returns to the latest brief.
            await self._reload_brief()
        day = (
            "today's Inbox"
            if local_date is None
            else f"the Inbox for {day_text(local_date, today=self._local_today())}"
        )
        self.status.setText(f"Syncing {day}…")
        try:
            result = await self.backend.generate(
                self, self, self._cancel, self._progress, local_date=local_date
            )
        except ConfigurationError as exc:
            if "MAILBRIEF_GROQ_MODEL" not in str(exc):
                raise
            log_failure(exc)
            guidance = configuration_guidance(exc)
            self.ai.setText("AI: " + guidance)
            self.status.setText(
                "No new task suggestions were generated. The brief's Inbox check did not run. "
                "The displayed saved brief is unchanged. "
                + guidance
                + " Then retry Sync and review."
            )
            return
        if result.sync.status in (SyncStatus.COMPLETE, SyncStatus.PARTIAL):
            self._stamp_checked()
        match result.status:
            case BriefStatus.SAVED:
                digest = result.digest
                assert digest is not None  # A saved result always carries its digest.
                if local_date is None:
                    await self._show_digest(digest)
                else:
                    await self._view(digest)
                self.status.setText(f"Brief saved ({digest.status.value.capitalize()}).")
                if digest.status is DigestStatus.EMPTY:
                    if result.sync.message_count == 0 and result.sync.status is SyncStatus.COMPLETE:
                        self.status.setText(f"No messages in {day}. Empty brief saved.")
                    else:
                        self.status.setText(
                            "No analyzed messages in this selection. Empty brief saved."
                        )
            case BriefStatus.CANCELLED:
                self.status.setText(_CANCELLED)
            case BriefStatus.CONSENT_DECLINED:
                self.status.setText("Transmission declined. No messages sent to AI in this run.")
            case BriefStatus.SYNC_FAILED:
                self.status.setText(_REFRESH_FAILED)
            case BriefStatus.ANALYSIS_FAILED:
                self.status.setText(_REFRESH_FAILED)
            case BriefStatus.READY_FOR_REVIEW:
                # Only an automatic run ends this way; from here it is reported as a failure.
                self.status.setText(_REFRESH_FAILED)
            case _:
                assert_never(result.status)
        # A cancelled run's news is the cancellation; a partial count would invite misreading.
        threads = "" if result.status is BriefStatus.CANCELLED else thread_check_text(result.sync)
        if threads:
            self.status.setText(f"{self.status.text()} {threads}")
        if result.proposals_created > 0:
            count = result.proposals_created
            noun = "update" if count == 1 else "updates"
            self.status.setText(f"{self.status.text()} Proposed {count} {noun} to your actions.")
        if result.error_code == "AUTH_REQUIRED" or result.sync.error_code == "AUTH_REQUIRED":
            self.connection.setText("Gmail: session expired. Connect Gmail, then retry.")
        for code in dict.fromkeys((result.error_code, result.sync.error_code)):
            if code:
                guidance = error_guidance(code)
                self.status.setText(self.status.text() + " " + guidance)
                if code.startswith("AI_"):
                    self.ai.setText("AI: " + guidance)
        if result.status is not BriefStatus.CANCELLED and self._calendar_allowed:
            await self._sync_calendar(quiet=True)  # Calendar follows Gmail (ADR 0019).
        if result.status is not BriefStatus.CANCELLED:
            await self._refresh_day()  # The thread check may have brought new replies.

    # Automatic refresh (ADR 0017): while MailBrief is open, on the owner's schedule. It has
    # no review, no consent question and no dialog; everything it says goes to the status line.

    def _dialog_open(self) -> bool:
        """Whether the owner is working in one of the window's dialogs or editors, or is
        confirming a replacement on the Briefs page."""
        if (
            self.workspace.current_page() == "briefs"
            and not self.history_panel.confirm_panel.isHidden()
        ):
            return True
        return (self.data_dialog is not None and self.data_dialog.isVisible()) or any(
            widget.isVisible()
            for widget in (
                self.settings_dialog,
                self.auto_send_dialog,
                self.cached_dialog,
                self.proposals_dialog,
                self.action_editor,
                self.projects_dialog,
                self.draft_editor,
                self.event_editor,
                self.planning_dialog,
                self.command_palette,
            )
        )

    def _refresh_due(self) -> None:
        """The scheduler says a run is due: start one unless something is in the way.

        Another operation, or a dialog the owner is using, means try again a minute later; a
        disconnected Gmail means skip this one, and say so. Nothing opens.
        """
        if self._closing or not self._ready:
            return
        busy = self._dialog_open() or (self.task is not None and not self.task.done())
        if busy:
            self.scheduler.retry_soon()
            return
        if self._account_email is None:
            self.status.setText(_REFRESH_DISCONNECTED)
            return
        if not self.start(self._automatic_refresh):
            self.scheduler.retry_soon()

    async def _automatic_refresh(self) -> None:
        """One automatic run. However it ends, the next is an interval later."""
        self._automatic_active = True
        self._synced_at = None  # No review: its sync has just finished when it returns.
        self.status.setText(_AUTOMATIC + "checking Gmail…")
        try:
            await self._refresh_inbox(quiet=True)
            if self._cancel.is_set() or self._account_email is None:
                return
            result = await self.backend.generate_automatic(self._cancel, self._progress)
            await self._automatic_finished(result)
        finally:
            self.scheduler.note_run(self.now())

    async def _automatic_finished(self, result: BriefRunResult) -> None:
        """Say what the run did, in the status line, and show what it saved."""
        log_automatic_run(result)
        stamp = f"{self.now().astimezone(self.zone):%H:%M}"
        if result.sync.status in (SyncStatus.COMPLETE, SyncStatus.PARTIAL):
            self._stamp_checked()
        match result.status:
            case BriefStatus.READY_FOR_REVIEW:
                # Either it only checked, because nothing may be sent or nothing is new, or it
                # saved nothing because the brief would have lost a carried message. When that
                # was a failure, its guidance follows below.
                found = (
                    needs_review_sentence(result.unrefreshed, result.ready)
                    if result.needs_review
                    else _ready_text(result.ready)
                )
                text = f"Checked Gmail at {stamp}. {found}"
            case BriefStatus.SAVED:
                digest = result.digest
                assert digest is not None  # A saved result always carries its digest.
                analyzed = 0 if result.coverage is None else result.coverage.analyzed
                text = f"Automatic brief at {stamp}: " + (
                    f"analyzed {_count_messages(analyzed, new=True)}"
                    if analyzed
                    else "nothing new to analyze"
                )
                if result.deferred:
                    waiting = "waits" if result.deferred == 1 else "wait"
                    text += f"; {result.deferred} {waiting} for your review."
                else:
                    text += "."
                if result.proposals_created > 0:
                    count = result.proposals_created
                    noun = "update" if count == 1 else "updates"
                    text += f" Proposed {count} {noun} to your actions."
                self._offer_undo()  # A new brief replaces the suggestions an Undo would refer to.
                # Never pull the owner away from a past brief they're reading.
                if self._shown is None:
                    await self._show_digest(digest)
            case BriefStatus.CANCELLED:
                text = "Automatic refresh cancelled."
            case BriefStatus.SYNC_FAILED:
                text = ""  # The guidance for its code, below, says what failed.
            case BriefStatus.ANALYSIS_FAILED:
                text = ""  # As above.
            case BriefStatus.CONSENT_DECLINED:
                text = ""  # An automatic run never asks, so this never ends one.
            case _:
                assert_never(result.status)
        if result.status is not BriefStatus.CANCELLED:
            for code in dict.fromkeys((result.error_code, result.sync.error_code)):
                if code:
                    guidance = error_guidance(code)
                    text = f"{text} {_AUTOMATIC}{guidance}".strip()
                    if code.startswith("AI_"):
                        self.ai.setText("AI: " + guidance)
            if "AUTH_REQUIRED" in (result.error_code, result.sync.error_code):
                self._set_account(None)  # Nothing more runs until the owner signs in again.
                self.connection.setText("Gmail: session expired. Connect Gmail, then retry.")
        self.status.setText(text)
        if result.status is not BriefStatus.CANCELLED:
            await self._refresh_actions()  # Thread activity and proposals may have changed.
        await self._reload_history()  # Today's brief may be new or changed.
        if (
            result.status is not BriefStatus.CANCELLED
            and self._calendar_allowed
            and self._account_email is not None
        ):
            await self._sync_calendar(quiet=True)  # Calendar follows Gmail (ADR 0019).

    async def _show_auto_send(self) -> None:
        """The automatic-analysis line in Settings, from the connected account's active
        consent; local data only."""
        panel = self.settings_dialog.preferences_panel
        if self._account_email is None:
            panel.set_auto_send(None, self.zone, connected=False, today=self._local_today())
            return
        try:
            status = await self.backend.auto_send_status(self._account_email)
        except Exception as exc:
            log_failure(exc)
            panel.set_auto_send(None, self.zone, unreadable=True, today=self._local_today())
            return
        panel.set_auto_send(status, self.zone, today=self._local_today())

    async def _open_auto_send(self) -> None:
        """Open the dialog on the connected account's permission; without an account or a
        consent there is none to change."""
        if self._account_email is None:
            self.settings_dialog.preferences_panel.set_auto_send(
                None, self.zone, connected=False, today=self._local_today()
            )
            self.status.setText(AUTO_DISCONNECTED)
            return
        status = await self.backend.auto_send_status(self._account_email)
        self.settings_dialog.preferences_panel.set_auto_send(
            status, self.zone, today=self._local_today()
        )
        if status is None:
            self.status.setText(NO_CONSENT)
            return
        self.auto_send_dialog.configure(status)
        self.dialog_guard.show_dialog(self.auto_send_dialog)

    def _request_set_auto_send(self, limit: int) -> None:
        if not self.start(lambda: self._set_auto_send(limit), cancellable=False):
            self.status.setText(_BUSY)

    async def _set_auto_send(self, limit: int) -> None:
        status = await self.backend.set_auto_send(limit, self._account_email)
        self.settings_dialog.preferences_panel.set_auto_send(
            status, self.zone, connected=self._account_email is not None, today=self._local_today()
        )
        if status is None or status.limit == 0:
            self.status.setText("Automatic analysis is off. Every run asks you first.")
        else:
            self.status.setText(
                f"Automatic runs may now send up to {_count_messages(status.limit)} without asking."
            )

    def _progress(self, progress: SyncProgress) -> None:
        self.status.setText(
            f"{progress.stage.value.capitalize()} · {progress.messages_fetched} messages · "
            f"{progress.ai_batches_completed} AI batches"
        )

    async def review(
        self,
        candidates: tuple[RankedMessage, ...],
        selected_ids: tuple[str, ...],
        *,
        blocked_ids: frozenset[str],
        outside_ids: frozenset[str],
        declined_ids: frozenset[str],
        limit: int,
    ) -> tuple[str, ...] | None:
        """Blocked messages are listed but can't be checked; at most ``limit`` can be.

        Replies in tracked threads that aren't in today's Inbox (``outside_ids``) are labelled
        as such and are otherwise like any other message. Messages the owner left out of an
        earlier review (``declined_ids``) start unchecked, with "you left this out earlier"."""
        self._synced_at = self.now()  # The sync has finished; the owner may take a while.
        if not candidates:
            return ()
        self._review = asyncio.get_running_loop().create_future()
        self._show_page("today")  # The run page, while the review waits.
        self._review_limit, self._review_blocked = limit, blocked_ids
        self.review_hint.setText(_review_hint(limit))
        self.shortlist.clear()
        for ranked in candidates:
            message = ranked.message
            label = f"{message.sender.address} — {message.subject}"
            # What the shortlist's delegate paints; the item's text stays what is read.
            row = shortlist_row(
                ranked,
                blocked=message.provider_message_id in blocked_ids,
                outside=message.provider_message_id in outside_ids,
                declined=message.provider_message_id in declined_ids,
            )
            if message.provider_message_id in blocked_ids:
                # Not user-checkable and never given a check box, so neither a click nor
                # Space can select it.
                item = QListWidgetItem(f"{label} — excluded in Settings")
                item.setData(Qt.ItemDataRole.UserRole, message.provider_message_id)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setToolTip(_EXCLUDED_TIP)
                item.setData(ROW_ROLE, row)
                self.shortlist.addItem(item)
                continue
            if message.provider_message_id in outside_ids:
                label = f"{label} — {OUTSIDE_REPLY_TEXT}"
            if message.provider_message_id in declined_ids:
                label = f"{label} — {DECLINED_TEXT}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, message.provider_message_id)
            item.setToolTip(
                f"Rank score: {ranked.score}\n"
                + ", ".join(reason_text(reason) for reason in ranked.reasons)
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if message.provider_message_id in selected_ids
                else Qt.CheckState.Unchecked
            )
            item.setData(ROW_ROLE, row)
            self.shortlist.addItem(item)
        self._fit_shortlist()
        self.review_panel.show()
        self.shortlist.setCurrentRow(0)
        self.shortlist.setFocus()
        self.status.setText("Review the shortlist before any message bodies are retrieved.")
        self._selection_changed()
        try:
            return await self._review
        finally:
            self.review_panel.hide()
            self._leave_run_page()

    def _fit_shortlist(self) -> None:
        """The shortlist is never taller than its rows, so a short list shows no empty box."""
        shortlist = self.shortlist
        rows = sum(shortlist.sizeHintForRow(number) for number in range(shortlist.count()))
        shortlist.setMaximumHeight(rows + 2 * shortlist.frameWidth())

    def _checked_ids(self) -> list[str]:
        """The checked messages; a blocked message never counts, even if checked."""
        selected: list[str] = []
        for index in range(self.shortlist.count()):
            item = self.shortlist.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                key = str(item.data(Qt.ItemDataRole.UserRole))
                if key not in self._review_blocked:
                    selected.append(key)
        return selected

    def _accept_review(self) -> None:
        if self._review is None or self._review.done():
            return
        selected = self._checked_ids()
        if not selected:
            # Continuing with nothing would save an empty brief over today's saved one.
            self.status.setText(_PICK_ONE)
            return
        if len(selected) > self._review_limit:
            self.status.setText(_too_many(self._review_limit))
            return
        self._review.set_result(tuple(selected))

    def _selection_changed(self) -> None:
        count = len(self._checked_ids())
        noun = "message" if count == 1 else "messages"
        self.review_button.setText(f"Co&ntinue with {count} selected {noun}")
        self.review_button.setEnabled(1 <= count <= self._review_limit)
        if count == 0:
            self.status.setText(_PICK_ONE)
        elif count > self._review_limit:
            self.status.setText(_too_many(self._review_limit))

    async def confirm(self, preview: TransmissionPreview) -> bool:
        self._consent = asyncio.get_running_loop().create_future()
        self._show_page("today")  # The run page, while the consent waits.
        self.disclosure.setText("\n\n".join(disclosure_lines(preview)))
        self.consent_panel.show()
        self.decline_button.setFocus()
        self.status.setText("Review what will be sent to Groq. Enable Zero Data Retention first.")
        try:
            return await self._consent
        finally:
            self.consent_panel.hide()
            self._leave_run_page()

    def _answer_consent(self, answer: bool) -> None:
        if self._consent is not None and not self._consent.done():
            self._consent.set_result(answer)

    def cancel(self) -> None:
        if not self._cancellable:
            return
        self._cancel.set()
        if self.task is not None and not self.task.done() and not self.task.cancelling():
            self.task.cancel()

    def _unsaved_input(self) -> str | None:
        """What quitting now would throw away: the open dialog holding unsaved input. The
        draft editor isn't asked: closing saves its text."""
        for dialog, what in (
            (self.action_editor, "this action"),
            (self.event_editor, "this event"),
            (self.settings_dialog, "Settings"),
        ):
            if dialog.isVisible() and dialog.has_unsaved_input():
                return what
        return None

    def ask_before_quitting(self) -> bool:
        """When quitting would lose unsaved input, ask first and return True: the quit
        waits for the answer. MailBriefApplication calls this for Cmd+Q and the Dock's
        Quit, and closeEvent for the window's own close button."""
        if self._closing or self._quit_confirmed:
            return False
        unsaved = self._unsaved_input()
        if unsaved is None:
            return False
        box = self._quit_box
        if box is None:
            box = QMessageBox(self)
            box.setObjectName("quitConfirmation")
            box.setWindowTitle("Quit MailBrief")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.addButton(
                button_label("Discard changes and quit"), QMessageBox.ButtonRole.DestructiveRole
            )
            box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
            box.buttonClicked.connect(
                lambda button: self._quit_answered(
                    box.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole
                )
            )
            self._quit_box = box
        box.setText(f"Quit and discard your unsaved changes to {unsaved}?")
        self.dialog_guard.show_dialog(box)  # Not modal: another quit only brings it back.
        return True

    def _quit_answered(self, discard: bool) -> None:
        if discard:
            self._quit_confirmed = True
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutdown_complete:
            event.accept()
            return
        event.ignore()  # Also defers QApplication.quit(), including Cmd+Q and Dock Quit.
        if self._closing or self.ask_before_quitting():
            return
        self._closing = True
        self.scheduler.stop()
        self.day_timer.stop()
        self.auto_send_dialog.reject()
        self.settings_dialog.reject()
        self.cached_dialog.reject()
        self.proposals_dialog.reject()
        if self.data_dialog is not None:
            self.data_dialog.reject()
        self.projects_dialog.force_close()
        self.action_editor.force_close()  # Even mid-save; a Save during shutdown is refused.
        self.event_editor.force_close()
        self._cancel_planning()
        self.planning_dialog.reject()
        self.command_palette.reject()
        self._cancel_drafting()
        final = self.draft_editor.final_edit() if self.draft_editor.isVisible() else None
        if final is not None:
            self._queue_autosave(final)  # shutdown() drains it before closing the database.
        self.draft_editor.force_close()
        self.cancel()
        self.closing.emit()

    async def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self.scheduler.stop()  # No timer outlives the window.
        self.cancel()
        try:
            if self.task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
            for read in self._calendar_reads:
                read.cancel()
            await self.drain_calendar_reads()
            await self.draft_writes.drain()
            if self.appearance_writes is not None:
                await self.appearance_writes.drain()
            await self.workspace.backdrop_renders.drain()
        finally:
            try:
                await self.backend.close()
            finally:
                self._shutdown_complete = True
                self.close()
