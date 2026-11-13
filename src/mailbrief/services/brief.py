"""Generate one consented daily brief: sync, bodies, analysis and the saved digest."""

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import replace
from datetime import date, datetime
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.config import Settings
from mailbrief.domain.analysis import AIUsage
from mailbrief.domain.briefs import (
    AnalysisOutcome,
    BriefRunResult,
    BriefStatus,
    TransmissionPreview,
)
from mailbrief.domain.common import utc_now
from mailbrief.domain.digests import DigestCoverage, SyncProgress, SyncResult, SyncStage, SyncStatus
from mailbrief.domain.preferences import SHORTLIST_LIMIT_MAX, AnalysisMode, OwnerPreferences
from mailbrief.errors import ConfigurationError
from mailbrief.infra.tasks import OperationStopped
from mailbrief.ports.ai_provider import AIProvider
from mailbrief.ports.email_provider import EmailProvider
from mailbrief.ports.threads import ThreadReader
from mailbrief.services.analysis import (
    KEY_MISSING,
    AnalysisPlan,
    AnalysisRun,
    AnalysisService,
    PlannedMessage,
    emit_progress,
)
from mailbrief.services.application import ApplicationService
from mailbrief.services.bodies import BodyService
from mailbrief.services.calendar import day_window, local_day_window, resolve_timezone
from mailbrief.services.consent import (
    LOCAL_CONSENT_PROVIDER,
    LOCAL_DISCLOSURE_VERSION,
    local_disclosure_lines,
)
from mailbrief.services.digest import IN_BRIEF, DigestService
from mailbrief.services.history import BriefDateError, check_brief_date
from mailbrief.services.preferences import PreferencesService
from mailbrief.services.proposals import ProposalService
from mailbrief.services.ranking import MAX_SHORTLIST_SIZE
from mailbrief.services.ranking import ShortlistGate as ShortlistGate
from mailbrief.services.threads import ThreadService
from mailbrief.storage.repositories import (
    AccountRepository,
    ConsentRepository,
    MessageRepository,
    SyncRunRepository,
)
from mailbrief.storage.tables import AccountTable

logger = logging.getLogger(__name__)

CONSENT_DISCLOSURE_VERSION: Final = "2"
# An automatic run stopped before sending because a carried message's body couldn't be read.
CARRIED_BODY_FAILED: Final = "CARRIED_BODY_FAILED"
_DISPLAY_NAMES: Final = {"openai": "OpenAI", "groq": "Groq", "local": "On this computer"}
LOCAL_AI_NOT_READY: Final = "LOCAL_AI_NOT_READY"
_AUTOMATIC_TODAY: Final = "Automatic runs brief today only."
# What the body step leaves on a message it couldn't read: unreadable, empty or deleted.
_UNREAD: Final = (AnalysisOutcome.FAILED, AnalysisOutcome.SKIPPED)


class ConsentGate(Protocol):
    """Asks the user whether the previewed transmission may happen."""

    async def confirm(self, preview: TransmissionPreview) -> bool: ...


class NeverAsks:
    """The consent gate of an automatic run, which has nobody to ask: BriefService never
    calls it, and if it ever did, nothing would be sent."""

    async def confirm(self, preview: TransmissionPreview) -> bool:
        return False


class MailSource(EmailProvider, ThreadReader, Protocol):
    """A mail provider that also reads threads, as Gmail's does: what a brief run needs."""


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def provider_display_name(name: str) -> str:
    """The provider's name as people write it; unknown names pass through unchanged."""
    return _DISPLAY_NAMES.get(name, name)


def disclosure_lines(preview: TransmissionPreview) -> tuple[str, ...]:
    """Plain sentences describing what will be sent, shared by the CLI and the UI."""
    if preview.provider_name == LOCAL_CONSENT_PROVIDER:
        account = (
            () if preview.account_email is None else (f"Gmail account: {preview.account_email}",)
        )
        return account + local_disclosure_lines(preview.message_count)
    provider = provider_display_name(preview.provider_name)
    cut = preview.truncated_count
    lines = [
        f"Forewit will send {_count(preview.message_count, 'message')} to {provider} "
        f"({preview.model_name}) for analysis.",
        f"{_count(cut, 'message')} {'is' if cut == 1 else 'are'} cut to fit the length limit."
        if cut
        else "No message is cut to fit the length limit.",
        "For each message it sends: " + "; ".join(preview.fields) + ".",
        "It never sends attachments, recipients, message IDs, links, account IDs or credentials.",
        preview.privacy_notice,
    ]
    if preview.reused_count:
        lines.append(
            f"{_count(preview.reused_count, 'message')} already analyzed will not be sent again."
        )
    if preview.first_use:
        lines.append("Your consent is remembered for this account until you revoke it.")
    return tuple(lines)


def permission_preview(
    limit: int,
    *,
    provider_name: str,
    model_name: str,
    body_character_limit: int,
    privacy_notice: str,
) -> TransmissionPreview:
    """What an automatic run with permission for ``limit`` messages (at least 1) may send,
    as the transmission preview the disclosure lines describe (ADR 0017)."""
    return TransmissionPreview(
        provider_name=provider_name,
        model_name=model_name,
        message_count=limit,
        truncated_count=0,
        reused_count=0,
        first_use=False,
        body_character_limit=body_character_limit,
        privacy_notice=privacy_notice,
    )


def permission_sentence(limit: int, account_email: str, provider_name: str) -> str:
    """The plain sentence that says what the automatic-analysis permission allows."""
    noun = "message" if limit == 1 else "messages"
    return (
        f"Automatic runs may send up to {limit} {noun} from {account_email} to "
        f"{provider_display_name(provider_name)} without asking. "
        "Revoke consent in Settings to stop."
    )


def needs_review_sentence(unrefreshed: int, ready: int) -> str:
    """What an automatic run says when it saved nothing rather than lose a carried message
    (ADR 0017): how many of the day's brief's messages it didn't refresh, because they were
    over its send limit, unreadable or failed in analysis, and then how many new messages
    are ready as well, if any. Shared by the CLI and the UI."""
    sentence = (
        f"Today's brief needs your review: {_count(unrefreshed, 'message')} from it couldn't "
        "be refreshed automatically."
    )
    if ready == 0:
        return sentence
    return f"{sentence} {_count(ready, 'new message')} {'is' if ready == 1 else 'are'} also ready."


def _not_refreshed(
    messages: Iterable[PlannedMessage], carried: Collection[str]
) -> list[PlannedMessage]:
    """The carried messages that a brief saved from ``messages`` would leave out."""
    return [
        item
        for item in messages
        if item.outcome not in IN_BRIEF and item.ranked.message.provider_message_id in carried
    ]


class BriefService:
    """Generate and save one local day's brief, asking consent before anything is sent."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        application: ApplicationService,
        bodies: BodyService,
        analysis: AnalysisService,
        digests: DigestService,
        consent_gate: ConsentGate,
        clock: Callable[[], datetime] = utc_now,
        proposals: ProposalService | None = None,
        preferences: OwnerPreferences | None = None,
    ) -> None:
        self._session = session
        self._application = application
        self._bodies = bodies
        self._analysis = analysis
        self._digests = digests
        self._consent_gate = consent_gate
        self._clock = clock
        self._consents = ConsentRepository(session)
        self._proposals = proposals or ProposalService(session, clock=clock)
        self._preferences = preferences

    async def generate(
        self,
        *,
        tz_key: str | None = None,
        include_ids: tuple[str, ...] = (),
        exclude_ids: tuple[str, ...] = (),
        cancel: asyncio.Event | None = None,
        progress: Callable[[SyncProgress], None] | None = None,
        shortlist_gate: ShortlistGate | None = None,
        shortlist_limit: int = MAX_SHORTLIST_SIZE,
        excluded_senders: tuple[str, ...] = (),
        local_date: date | None = None,
        automatic: bool = False,
        important_senders: tuple[str, ...] = (),
    ) -> BriefRunResult:
        """Sync one day's Inbox, prepare bodies, confirm consent, analyze and save the brief.

        The day is today in ``tz_key``'s zone, or ``local_date``: today or one of the
        previous seven days. Any other date raises BriefDateError before Gmail is contacted
        or anything is written. A past day's brief covers only the messages still in the
        Inbox now. Messages from ``excluded_senders`` are never selected, downloaded or
        sent; mail from ``important_senders`` ranks higher (ADR 0020). Once the brief is
        saved, its follow-up signals become proposals for the owner's actions (ADR 0016); a
        failure there never fails the saved brief.

        Runs through a day are cumulative: the messages of the day's saved brief are carried
        forward, so a later run, automatic or not, never drops one the owner chose unless it
        is archived, newly blocked or declined; the day still has one brief.

        An ``automatic`` run (ADR 0017) briefs today only, takes no review and never asks
        for consent. It syncs, checks threads and ranks, then reads the automatic-analysis
        permission on the active consent for the current disclosure version. Without one, or
        with nothing new selected, it returns READY_FOR_REVIEW with the number of new
        messages (the selection minus the carried ones) that are ready, before any body is
        downloaded and with nothing sent or saved. With permission it downloads the bodies
        and sends at most min(permission, ``shortlist_limit``) messages: the carried ones
        that need analysis again first, then the others, each in rank order. It defers the
        rest (never sent, never cached, counted in the coverage), then saves the brief and
        derives proposals as usual. It never saves a brief that would lose a carried message.
        When one is over that cap, or its body can't be read, the run sends and saves
        nothing; when one's analysis fails, it writes no brief and leaves the analyses that
        succeeded cached. Either way it returns READY_FOR_REVIEW with ``unrefreshed`` set to
        the number of carried messages it didn't refresh and ``ready`` to the number of new
        ones, and with CARRIED_BODY_FAILED as its error code when a body was the reason.
        """
        if automatic and (shortlist_gate is not None or include_ids or exclude_ids):
            raise ValueError("An automatic run has no review.")
        now = self._clock()
        local = self._analysis.provider_name == LOCAL_CONSENT_PROVIDER
        local_preferences: OwnerPreferences | None = None
        if local:
            local_preferences = await PreferencesService(self._session).get()
            if local_preferences.analysis_mode is not AnalysisMode.LOCAL or (
                self._preferences is not None
                and self._preferences.revision != local_preferences.revision
            ):
                raise ConfigurationError(
                    "Analysis settings changed. Retry with the current settings."
                )
            shortlist_limit = local_preferences.local_message_limit
            excluded_senders = local_preferences.excluded_senders
            important_senders = local_preferences.important_senders
            shortlist_gate = None  # One local opt-in replaces recurring review questions.
        elif not 1 <= shortlist_limit <= SHORTLIST_LIMIT_MAX:
            raise ValueError("The cloud shortlist limit must be 1 to 10.")
        zone = resolve_timezone(tz_key)
        today = local_day_window(now, zone).local_date
        if automatic and local_date not in (None, today):
            raise BriefDateError(_AUTOMATIC_TODAY)
        check_brief_date(local_date or today, today)
        window = day_window(local_date or today, zone)
        account = await self._application.get_or_restore_account()

        async def locally_allowed() -> bool:
            # The watcher can be cancelled while a query is in flight. Give it
            # its own short-lived transaction so it cannot invalidate the run's
            # session or keep reading an old permission snapshot.
            async with AsyncSession(bind=self._session.bind) as permission_session:
                current = await PreferencesService(permission_session).get()
                return (
                    local_preferences is not None
                    and current.analysis_mode is AnalysisMode.LOCAL
                    and current.revision == local_preferences.revision
                    and await ConsentRepository(permission_session).get_active(
                        account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION
                    )
                    is not None
                )

        # Runs through a day are cumulative: the day's saved brief is carried forward.
        carried, carried_outside = await self._digests.carried(account.id, window.local_date)
        sync, shortlist = await self._application.prepare_daily_shortlist(
            tz_key=window.timezone_name,
            now_utc=now,
            include_ids=include_ids,
            exclude_ids=exclude_ids,
            progress=progress,
            cancel=cancel,
            shortlist_gate=shortlist_gate,
            shortlist_limit=shortlist_limit,
            excluded_senders=excluded_senders,
            local_date=window.local_date,
            carried=carried,
            carried_outside=carried_outside,
            important_senders=important_senders,
        )
        if sync.status is SyncStatus.CANCELLED:
            return BriefRunResult(status=BriefStatus.CANCELLED, sync=sync)
        if sync.status is SyncStatus.FAILED:
            return BriefRunResult(
                status=BriefStatus.SYNC_FAILED, sync=sync, error_code=sync.error_code
            )

        if cancel is not None and cancel.is_set():
            return BriefRunResult(status=BriefStatus.CANCELLED, sync=sync)
        if local:
            current = await PreferencesService(self._session).get()
            if local_preferences is None or current.revision != local_preferences.revision:
                return BriefRunResult(status=BriefStatus.CANCELLED, sync=sync)
        new = sum(item.message.provider_message_id not in carried for item in shortlist)
        if local and not await locally_allowed():
            if automatic or not shortlist:
                return BriefRunResult(status=BriefStatus.READY_FOR_REVIEW, sync=sync, ready=new)
            preview = TransmissionPreview(
                provider_name=LOCAL_CONSENT_PROVIDER,
                account_email=account.email_address,
                model_name=self._analysis.model_name,
                message_count=shortlist_limit,
                truncated_count=0,
                reused_count=0,
                first_use=True,
                body_character_limit=self._bodies.limit,
                privacy_notice=self._analysis.privacy_notice,
            )
            if not await self._consent_gate.confirm(preview):
                return BriefRunResult(status=BriefStatus.CONSENT_DECLINED, sync=sync)
            await self._consents.grant(
                account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION, now
            )
            await self._session.commit()
        send_limit = (
            shortlist_limit
            if local
            else await self._auto_send_limit(account.id)
            if automatic
            else 0
        )
        if automatic and (send_limit == 0 or (new == 0 and not local)):
            # Nothing may be sent, or nothing is new: report, and leave the saved brief.
            logger.info("Automatic run: %d new messages ready, nothing sent", new)
            return BriefRunResult(status=BriefStatus.READY_FOR_REVIEW, sync=sync, ready=new)
        try:
            if local:
                prepared = await self._bodies.prepare(
                    shortlist, allowed=locally_allowed, cancel=cancel
                )
            else:
                prepared = await self._bodies.prepare(shortlist)
        except OperationStopped:
            return BriefRunResult(status=BriefStatus.CANCELLED, sync=sync)
        plan = await self._analysis.plan(
            account_id=account.id,
            shortlist=shortlist,
            bodies=prepared,
            timezone_name=window.timezone_name,
        )
        if cancel is not None and cancel.is_set():
            # Before the key check and consent, so a cancelled run writes no brief.
            return BriefRunResult(status=BriefStatus.CANCELLED, sync=sync)
        if automatic:
            # Carried messages that need analysis again take the places first. The shortlist
            # is sorted by rank, so the lowest-ranked of the others wait.
            plan.defer_after(min(send_limit, shortlist_limit), first=carried)
            stale = _not_refreshed(plan.messages, carried)
            if any(item.outcome is not None for item in stale):
                # A carried message is over the cap, or its body failed or was skipped, so a
                # saved brief would lose it: nothing is sent or saved, and the day's brief
                # waits for the owner's review. Only an unread body is a failure.
                unread = any(item.outcome in _UNREAD for item in stale)
                logger.info(
                    "Automatic run: %d carried messages not refreshed, nothing sent", len(stale)
                )
                return BriefRunResult(
                    status=BriefStatus.READY_FOR_REVIEW,
                    sync=sync,
                    error_code=CARRIED_BODY_FAILED if unread else None,
                    ready=new,
                    unrefreshed=len(stale),
                )

        async def publish_ready() -> None:
            if progress is None or (cancel is not None and cancel.is_set()):
                return
            if not await locally_allowed():
                return
            preview = await self._digests.preview(
                account_email=account.email_address,
                window=window,
                generated_at=now,
                messages=plan.messages,
                outside_ids=sync.outside_ids,
            )
            if preview is not None and await locally_allowed():
                pending = len(plan.to_send)
                emit_progress(
                    progress,
                    SyncProgress(
                        stage=SyncStage.ANALYZING,
                        ready_brief=preview,
                        pending_count=pending,
                        detail=(
                            f"Summarizing on this computer: {len(preview.items)} ready · "
                            f"{pending} remaining"
                        ),
                    ),
                )

        if local:
            await publish_ready()
        if plan.to_send and not await self._analysis.credentials_available():
            # Nothing can be sent, so there is nothing to consent to; cached and skipped
            # messages still make a brief.
            run = self._analysis.fail_unsent(plan, LOCAL_AI_NOT_READY if local else KEY_MISSING)
        else:
            # An automatic run never asks: its permission is the owner's answer (ADR 0017).
            if (
                not local
                and not automatic
                and plan.to_send
                and not await self._consented(account.id, plan, now)
            ):
                return BriefRunResult(status=BriefStatus.CONSENT_DECLINED, sync=sync)

            def local_progress(update: SyncProgress) -> None:
                completed = sum(item.outcome in IN_BRIEF for item in plan.messages)
                detail = f"Analyzing on this computer: {completed} of {len(plan.messages)}"
                emit_progress(progress, update.model_copy(update={"detail": detail}))

            if local and plan.to_send:
                emit_progress(
                    progress,
                    SyncProgress(stage=SyncStage.ANALYZING, detail="Starting the local model…"),
                )
            run = await self._analysis.execute(
                plan,
                cancel=cancel,
                progress=local_progress if local else progress,
                allowed=locally_allowed if local else None,
                result_ready=publish_ready if local else None,
            )
            if run.cancelled:
                return BriefRunResult(
                    status=BriefStatus.CANCELLED, sync=sync, ai_calls=run.requests_sent
                )

        if local:
            if not await locally_allowed():
                return BriefRunResult(
                    status=BriefStatus.CANCELLED, sync=sync, ai_calls=run.requests_sent
                )
            if run.error_code is not None and run.error_code not in (
                LOCAL_AI_NOT_READY,
                "AI_USAGE_LIMIT",
            ):
                run = replace(run, error_code="LOCAL_AI_FAILED")
        coverage = self._coverage(sync, len(shortlist), run)
        lost = _not_refreshed(run.messages, carried) if automatic or local else []
        if lost:
            # A carried message's analysis failed, so a saved brief would lose it: no brief
            # is written. The analyses that succeeded are cached already (execute commits
            # each call), so the owner's review reuses them.
            logger.info(
                "Automatic run: %d carried messages not refreshed, no brief saved", len(lost)
            )
            return BriefRunResult(
                status=BriefStatus.READY_FOR_REVIEW,
                sync=sync,
                coverage=coverage,
                error_code=run.error_code,
                ai_calls=run.requests_sent,
                provider_detail=run.provider_detail,
                deferred=coverage.deferred,
                ready=new,
                unrefreshed=len(lost),
            )
        emit_progress(progress, SyncProgress(stage=SyncStage.ASSEMBLING))
        digest = await self._digests.save(
            account_id=account.id,
            account_email=account.email_address,
            window=window,
            messages=run.messages,
            coverage=coverage,
            outside_ids=sync.outside_ids,
        )
        if digest is None:
            return BriefRunResult(
                status=BriefStatus.ANALYSIS_FAILED,
                sync=sync,
                coverage=coverage,
                error_code=run.error_code or "ANALYSIS_FAILED",
                ai_calls=run.requests_sent,
                provider_detail=run.provider_detail,
                deferred=coverage.deferred,
            )
        return BriefRunResult(
            status=BriefStatus.SAVED,
            sync=sync,
            digest=digest,
            coverage=coverage,
            error_code=run.error_code,
            ai_calls=run.requests_sent,
            provider_detail=run.provider_detail,
            proposals_created=await self._derive(account, run, window.timezone_name),
            deferred=coverage.deferred,
        )

    async def _auto_send_limit(self, account_id: int) -> int:
        """How many messages an automatic run may send without asking: the permission on the
        active consent for the current disclosure version, 0 when there is none."""
        consent = await self._consents.get_active(
            account_id, self._analysis.provider_name, CONSENT_DISCLOSURE_VERSION
        )
        return 0 if consent is None else consent.auto_send_limit

    async def _derive(self, account: AccountTable, run: AnalysisRun, timezone_name: str) -> int:
        """Proposals from the saved brief's analyses; a failure is logged by type only."""
        try:
            return await self._proposals.derive(account, run.messages, timezone_name)
        except Exception as exc:
            logger.warning("Follow-up proposals failed: %s", type(exc).__name__)
            return 0

    async def _consented(self, account_id: int, plan: AnalysisPlan, now: datetime) -> bool:
        """Ask the gate; record first-use consent before any provider call."""
        provider = self._analysis.provider_name
        active = await self._consents.get_active(account_id, provider, CONSENT_DISCLOSURE_VERSION)
        to_send = plan.to_send
        preview = TransmissionPreview(
            provider_name=provider,
            model_name=self._analysis.model_name,
            message_count=len(to_send),
            truncated_count=sum(
                item.request is not None and item.request.body_truncated for item in to_send
            ),
            reused_count=sum(item.outcome is AnalysisOutcome.REUSED for item in plan.messages),
            first_use=active is None,
            body_character_limit=self._bodies.limit,
            privacy_notice=self._analysis.privacy_notice,
        )
        if not await self._consent_gate.confirm(preview):
            logger.info("AI consent declined for %d messages", preview.message_count)
            return False
        if preview.first_use:
            await self._consents.grant(account_id, provider, CONSENT_DISCLOSURE_VERSION, now)
            await self._session.commit()
        return True

    def _coverage(self, sync: SyncResult, shortlisted: int, run: AnalysisRun) -> DigestCoverage:
        counts = Counter(item.outcome for item in run.messages)
        used = counts[AnalysisOutcome.ANALYZED] + counts[AnalysisOutcome.REUSED]
        usage = run.usage or AIUsage()
        return DigestCoverage(
            sync_complete=sync.status is SyncStatus.COMPLETE,
            shortlisted=shortlisted,
            analyzed=counts[AnalysisOutcome.ANALYZED],
            reused=counts[AnalysisOutcome.REUSED],
            failed=counts[AnalysisOutcome.FAILED],
            skipped=counts[AnalysisOutcome.SKIPPED],
            deferred=counts[AnalysisOutcome.DEFERRED],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            ai_provider=self._analysis.provider_name if used else None,
            ai_model=self._analysis.model_name if used else None,
        )


def compose_brief(
    session: AsyncSession,
    provider: MailSource,
    ai: AIProvider,
    settings: Settings,
    *,
    consent_gate: ConsentGate,
    clock: Callable[[], datetime] = utc_now,
    preferences: OwnerPreferences | None = None,
) -> BriefService:
    """BriefService as the desktop and the CLI both run it: over ``provider`` and ``ai``,
    following tracked threads, with the body and batch limits of ``settings``."""
    return BriefService(
        session=session,
        application=ApplicationService(
            provider,
            MessageRepository(session),
            SyncRunRepository(session),
            AccountRepository(session),
            threads=ThreadService(session, provider),
        ),
        bodies=BodyService(
            provider,
            limit=settings.ai_body_character_limit,
            concurrency=1 if ai.provider_name == LOCAL_CONSENT_PROVIDER else 5,
        ),
        analysis=AnalysisService(
            session,
            ai,
            batch_size=1 if ai.provider_name == LOCAL_CONSENT_PROVIDER else settings.ai_batch_size,
        ),
        digests=DigestService(session),
        consent_gate=consent_gate,
        clock=clock,
        preferences=preferences,
    )
