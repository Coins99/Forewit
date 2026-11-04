"""Local brief permission, bounded sequential analysis and saved-result preservation."""

import asyncio
from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.analysis import AnalysisRequest, AnalysisResponse
from mailbrief.domain.bodies import MessageBody
from mailbrief.domain.briefs import BriefStatus
from mailbrief.domain.digests import DigestStatus, SyncProgress
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.services.actions import ActionService
from mailbrief.services.consent import LOCAL_DISCLOSURE_VERSION
from mailbrief.services.preferences import PreferencesService
from mailbrief.storage.repositories import ConsentRepository, DigestRepository
from tests.unit.services.ai_fakes import FakeAIProvider, answer_all, good_action, good_candidate
from tests.unit.services.test_brief_service import (
    NOW,
    TODAY,
    ZONE,
    FakeBodyReader,
    RecordingGate,
    account_id_of,
    build,
    inbox,
    texts_for,
)
from tests.unit.services.test_manual_tasks import EDIT


class LocalAI(FakeAIProvider):
    @property
    def provider_name(self) -> str:
        return "local"


async def enable(session: AsyncSession, *, limit: int = 20) -> None:
    await PreferencesService(session).save(
        PreferencesEdit(analysis_mode="local", local_message_limit=limit), expected_revision=0
    )


async def test_local_twenty_messages_are_sequential_and_reused(session: AsyncSession) -> None:
    await enable(session)
    provider = LocalAI([answer_all() for _ in range(20)])
    gate = RecordingGate(answer=True)
    result = await build(session, provider, gate, messages=inbox(21), batch_size=1).generate(
        tz_key=ZONE
    )
    assert result.status is BriefStatus.SAVED
    assert provider.calls == 20
    assert all(len(batch) == 1 for batch in provider.batches)
    assert len(gate.previews) == 1
    assert gate.previews[0].message_count == 20
    assert gate.previews[0].account_email == "user@example.com"
    consent = await ConsentRepository(session).get_active(
        await account_id_of(session), "local", LOCAL_DISCLOSURE_VERSION
    )
    assert consent is not None
    again = await build(session, provider, gate, messages=inbox(21), batch_size=1).generate(
        tz_key=ZONE
    )
    assert again.status is BriefStatus.SAVED
    assert again.ai_calls == 0
    assert provider.calls == 20
    assert len(gate.previews) == 1


async def test_automatic_local_run_does_not_ask_without_grant(session: AsyncSession) -> None:
    await enable(session)
    provider = LocalAI()
    gate = RecordingGate(answer=True)
    result = await build(session, provider, gate).generate(tz_key=ZONE, automatic=True)
    assert result.status is BriefStatus.READY_FOR_REVIEW
    assert result.ready == 2
    assert provider.calls == 0
    assert not gate.previews


async def test_automatic_local_run_rechecks_cache_identity_without_new_mail(
    session: AsyncSession,
) -> None:
    await enable(session)

    class Versioned(LocalAI):
        version = "first"

        @property
        def prompt_version(self) -> str:
            return self.version

    provider = Versioned([answer_all(), answer_all()])
    gate = RecordingGate(answer=True)
    saved = await build(session, provider, gate, batch_size=1).generate(tz_key=ZONE)
    assert saved.status is BriefStatus.SAVED
    cached = await build(session, provider, gate, batch_size=1).generate(
        tz_key=ZONE, automatic=True
    )
    assert cached.status is BriefStatus.SAVED
    assert cached.ai_calls == 0
    provider.version = "second"
    provider.script.extend([answer_all(), answer_all()])
    refreshed = await build(session, provider, gate, batch_size=1).generate(
        tz_key=ZONE, automatic=True
    )
    assert refreshed.status is BriefStatus.SAVED
    assert refreshed.ai_calls == 2
    assert len(gate.previews) == 1


async def test_missing_model_preserves_the_last_saved_brief(session: AsyncSession) -> None:
    await enable(session, limit=1)
    provider = LocalAI([answer_all()])
    gate = RecordingGate(answer=True)
    saved = await build(session, provider, gate, batch_size=1).generate(tz_key=ZONE)
    assert saved.status is BriefStatus.SAVED
    provider.credentials = False
    result = await build(session, provider, gate, batch_size=1).generate(tz_key=ZONE)
    assert result.status is BriefStatus.SAVED
    assert result.ai_calls == 0
    assert result.coverage is not None and result.coverage.reused == 1
    assert (
        await DigestRepository(session).get_by_account_and_date(await account_id_of(session), TODAY)
        is not None
    )
    assert provider.calls == 1


async def test_local_cancelled_answer_cannot_be_published(session: AsyncSession) -> None:
    await enable(session)
    cancel = asyncio.Event()

    def answer_then_cancel(requests: Sequence[AnalysisRequest]) -> AnalysisResponse:
        cancel.set()
        return answer_all()(requests)

    provider = LocalAI([answer_then_cancel])
    result = await build(session, provider, RecordingGate(answer=True), batch_size=1).generate(
        tz_key=ZONE, cancel=cancel
    )
    assert result.status is BriefStatus.CANCELLED
    assert provider.calls == 1
    assert (
        await DigestRepository(session).get_by_account_and_date(await account_id_of(session), TODAY)
        is None
    )


async def test_local_revocation_during_call_discards_answer(session: AsyncSession) -> None:
    await enable(session)

    class RevokingAI(LocalAI):
        async def analyze(self, requests: Sequence[AnalysisRequest]) -> AnalysisResponse:
            response = await super().analyze(requests)
            async with AsyncSession(bind=session.bind) as writer:
                await ConsentRepository(writer).revoke_all(
                    await account_id_of(session), "local", NOW
                )
                await writer.commit()
            return response

    provider = RevokingAI([answer_all()])
    result = await build(session, provider, RecordingGate(answer=True), batch_size=1).generate(
        tz_key=ZONE
    )
    assert result.status is BriefStatus.CANCELLED
    assert provider.calls == 1


@pytest.mark.parametrize("answer", [False, True])
async def test_local_first_permission_precedes_any_body_read(
    session: AsyncSession, answer: bool
) -> None:
    await enable(session)
    provider = LocalAI(credentials=False)
    gate = RecordingGate(answer=answer)

    class Reader(FakeBodyReader):
        calls = 0

        async def fetch_message_body(self, provider_message_id: str) -> MessageBody:
            assert gate.previews
            assert gate.answer
            self.calls += 1
            return await super().fetch_message_body(provider_message_id)

    reader = Reader(texts_for(inbox()))
    result = await build(session, provider, gate, reader=reader).generate(tz_key=ZONE)
    assert len(gate.previews) == 1
    assert provider.calls == 0
    assert reader.calls == (2 if answer else 0)
    assert result.status is (
        BriefStatus.ANALYSIS_FAILED if answer else BriefStatus.CONSENT_DECLINED
    )


@pytest.mark.parametrize("stop", [False, True])
async def test_progressive_results_are_committed_before_the_canonical_brief(
    session: AsyncSession, stop: bool
) -> None:
    await enable(session)
    entered, release, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    updates: list[SyncProgress] = []

    class SlowSecond(LocalAI):
        async def analyze(self, requests: Sequence[AnalysisRequest]) -> AnalysisResponse:
            response = await super().analyze(requests)
            if self.calls == 2:
                entered.set()
                await release.wait()
            return response

    def with_actions(requests: Sequence[AnalysisRequest]) -> AnalysisResponse:
        return AnalysisResponse(
            candidates=tuple(
                good_candidate(
                    request,
                    actions=(good_action(request),),
                    action_required=True,
                    action_text="Approve the quarterly budget",
                    category="action",
                )
                for request in requests
            )
        )

    provider = SlowSecond([with_actions, with_actions])
    task = asyncio.create_task(
        build(session, provider, RecordingGate(answer=True), batch_size=1).generate(
            tz_key=ZONE, cancel=cancel, progress=updates.append
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        previews = [update for update in updates if update.ready_brief is not None]
        assert len(previews) == 1
        first = previews[0].ready_brief
        assert first is not None and len(first.items) == 1
        assert first.status is DigestStatus.PARTIAL
        assert previews[0].pending_count == 1
        assert first.items[0].suggestions
        async with AsyncSession(bind=session.bind) as observer:
            assert (
                await DigestRepository(observer).get_by_account_and_date(
                    await account_id_of(session), TODAY
                )
                is None
            )
            captured = await ActionService(observer, clock=lambda: NOW).create(
                EDIT.model_copy(update={"title": "Capture while AI runs"})
            )
            assert captured.title == "Capture while AI runs"
        if stop:
            cancel.set()
        else:
            release.set()
        result = await asyncio.wait_for(task, 5)
        assert result.status is (BriefStatus.CANCELLED if stop else BriefStatus.SAVED)
        if not stop:
            assert result.digest is not None
            saved_item = next(
                item
                for item in result.digest.items
                if item.message_key == first.items[0].message_key
            )
            assert (
                saved_item.suggestions[0].suggestion_id
                == first.items[0].suggestions[0].suggestion_id
            )
    finally:
        cancel.set()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
