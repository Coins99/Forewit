"""Unit tests for the synchronization service."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mailbrief.domain.bodies import BodySource, MessageBody
from mailbrief.domain.digests import (
    SyncProgress,
    SyncStage,
    SyncStatus,
)
from mailbrief.domain.inbox import MailboxPage
from mailbrief.domain.messages import (
    AccountIdentity,
    MessagePage,
    NormalizedMessage,
    ProviderKind,
    RankReason,
)
from mailbrief.ports.email_provider import EmailProvider
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from mailbrief.services import sync as sync_module
from mailbrief.services.calendar import DayWindow
from mailbrief.services.inbox import InboxService
from mailbrief.services.sync import SyncService
from mailbrief.storage.repositories import (
    AccountRepository,
    MessageRepository,
    SyncRunRepository,
)
from mailbrief.storage.tables import Base
from tests.factories import make_message

TEST_WINDOW = DayWindow(
    local_date=date(2026, 8, 31),
    timezone_name="UTC",
    start_utc=datetime(2026, 8, 31, 0, 0, 0, tzinfo=UTC),
    end_utc=datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC),
)


@pytest.mark.parametrize("outcome", ["complete", "failed", "cancelled", "malformed"])
async def test_full_inbox_reconciles_only_complete_scan(
    async_session: AsyncSession, test_account: int, outcome: str
) -> None:
    from datetime import timedelta

    old = make_message(
        provider_message_id="old", received_at_utc=TEST_WINDOW.start_utc - timedelta(days=400)
    )
    missing = make_message(
        provider_message_id="missing", received_at_utc=TEST_WINDOW.start_utc - timedelta(days=200)
    )
    repo = MessageRepository(async_session)
    await repo.upsert_messages(test_account, [missing])
    await async_session.commit()
    cancel = asyncio.Event()

    class Reader:
        async def iter_inbox_pages(
            self, continuation: str | None = None
        ) -> AsyncIterator[MessagePage]:
            yield MessagePage(page_number=1, messages=(old,), continuation="next")
            if outcome == "failed":
                raise ProviderResponseError("synthetic failure")
            if outcome == "cancelled":
                cancel.set()
            yield MessagePage(
                page_number=2, messages=(), failed_message_count=int(outcome == "malformed")
            )

    provider = FakeEmailProvider()
    result = await SyncService(
        provider=provider,
        message_repo=repo,
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    ).sync_inbox(
        reader=Reader(),
        account_id=test_account,
        account_identity="user@example.com",
        cancel=cancel,
    )
    rows, _ = await repo.get_inbox_page(test_account)
    ids = {r.provider_message_id for r in rows}
    assert "old" in ids
    assert ("missing" not in ids) == (outcome == "complete")
    assert (result.status is SyncStatus.COMPLETE) == (outcome == "complete")
    account = await AccountRepository(async_session).get_by_id(test_account)
    assert account is not None and account.last_sync_at_utc is None
    assert not provider.body_fetch_called
    with pytest.raises(ValueError):
        await repo.get_inbox_page(test_account, offset=-1)


class FakeEmailProvider(EmailProvider):
    """Configurable test provider yielding staged message pages."""

    def __init__(self, pages: list[list[NormalizedMessage]] | None = None) -> None:
        self.pages = pages or []
        self.fail_at_page: int | None = None
        self.failure_exception: Exception = RuntimeError("Provider exploded")
        self.body_fetch_called = False

    @property
    def provider_kind(self) -> ProviderKind:
        return ProviderKind.MICROSOFT

    async def connect(self) -> AccountIdentity:
        return AccountIdentity(
            provider=ProviderKind.MICROSOFT,
            provider_account_id="acc-1",
            email_address="user@example.com",
        )

    async def current_account(self) -> AccountIdentity | None:
        return await self.connect()

    async def iter_message_pages(
        self,
        *,
        range_start_utc: datetime,
        range_end_utc: datetime,
        continuation: str | None = None,
    ) -> AsyncIterator[MessagePage]:
        start_idx = 0
        if continuation and continuation.startswith("page-"):
            start_idx = int(continuation.split("-")[1]) - 1

        for i, page_messages in enumerate(self.pages[start_idx:], start=start_idx + 1):
            if self.fail_at_page == i:
                # Clear so retry succeeds if configured
                self.fail_at_page = None
                raise self.failure_exception
            yield MessagePage(
                page_number=i,
                messages=tuple(page_messages),
                continuation=f"page-{i + 1}" if i < len(self.pages) else None,
            )

    async def fetch_message_body(self, provider_message_id: str) -> MessageBody:
        self.body_fetch_called = True
        return MessageBody(
            provider_message_id=provider_message_id,
            text="Message body content",
            source=BodySource.PLAIN,
        )

    async def disconnect(self) -> None:
        pass


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def test_account(async_session: AsyncSession) -> int:
    repo = AccountRepository(async_session)
    account = await repo.upsert(
        AccountIdentity(
            provider=ProviderKind.MICROSOFT,
            provider_account_id="acc-1",
            email_address="user@example.com",
            display_name="Test User",
        )
    )
    await async_session.commit()
    return account.id


async def test_sync_empty_inbox(async_session: AsyncSession, test_account: int) -> None:
    provider = FakeEmailProvider(pages=[[]])
    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    progress_events: list[SyncProgress] = []
    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
        progress=progress_events.append,
    )

    assert result.status == SyncStatus.COMPLETE
    assert result.message_count == 0
    assert result.page_count == 1
    assert not provider.body_fetch_called


async def test_sync_multi_page_success(async_session: AsyncSession, test_account: int) -> None:
    # 125 messages over 3 pages (50, 50, 25)
    page1 = [make_message(provider_message_id=f"msg-{i:03d}") for i in range(1, 51)]
    page2 = [make_message(provider_message_id=f"msg-{i:03d}") for i in range(51, 101)]
    page3 = [make_message(provider_message_id=f"msg-{i:03d}") for i in range(101, 126)]

    provider = FakeEmailProvider(pages=[page1, page2, page3])
    msg_repo = MessageRepository(async_session)
    sync_service = SyncService(
        provider=provider,
        message_repo=msg_repo,
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    progress_events: list[SyncProgress] = []
    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
        progress=progress_events.append,
    )

    assert result.status == SyncStatus.COMPLETE
    assert result.page_count == 3
    assert result.message_count == 125

    # Check progress stages
    assert len(progress_events) >= 3
    assert progress_events[-1].messages_fetched == 125
    assert progress_events[-1].stage == SyncStage.FETCHING

    # Verify rows in DB
    db_msgs = await msg_repo.get_messages_in_range(
        test_account, TEST_WINDOW.start_utc, TEST_WINDOW.end_utc
    )
    assert len(db_msgs) == 125


async def test_sync_idempotency_and_ranking_preservation(
    async_session: AsyncSession, test_account: int
) -> None:
    msg1 = make_message(provider_message_id="msg-1", subject="Original Subject")
    msg2 = make_message(provider_message_id="msg-2", subject="Second Message")

    provider = FakeEmailProvider(pages=[[msg1, msg2]])
    msg_repo = MessageRepository(async_session)
    sync_service = SyncService(
        provider=provider,
        message_repo=msg_repo,
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    # First sync
    await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    # Set ranking on msg-1
    saved_msg1 = await msg_repo.get_by_provider_message_id(test_account, "msg-1")
    assert saved_msg1 is not None
    await msg_repo.update_ranking(
        saved_msg1.id, rank_score=42, rank_reasons=[RankReason.HIGH_IMPORTANCE]
    )
    await async_session.commit()

    # Re-sync with updated subject
    msg1_updated = make_message(provider_message_id="msg-1", subject="Updated Subject")
    provider.pages = [[msg1_updated, msg2]]

    await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    # Verify total count is still 2 (no duplicates)
    all_msgs = await msg_repo.get_messages_in_range(
        test_account, TEST_WINDOW.start_utc, TEST_WINDOW.end_utc
    )
    assert len(all_msgs) == 2

    # Verify msg-1 has updated subject AND preserved rank_score and rank_reasons
    async_session.expire_all()
    reloaded_msg1 = await msg_repo.get_by_provider_message_id(test_account, "msg-1")
    assert reloaded_msg1 is not None
    assert reloaded_msg1.subject == "Updated Subject"
    assert reloaded_msg1.rank_score == 42
    assert reloaded_msg1.rank_reasons_json == ["high_importance"]


async def test_sync_partial_failure(async_session: AsyncSession, test_account: int) -> None:
    page1 = [make_message(provider_message_id=f"msg-{i}") for i in range(1, 10)]
    page2 = [make_message(provider_message_id=f"msg-{i}") for i in range(10, 20)]

    provider = FakeEmailProvider(pages=[page1, page2])
    provider.fail_at_page = 2
    provider.failure_exception = ProviderRateLimitError("Throttled with secret token_abc123")

    msg_repo = MessageRepository(async_session)
    sync_run_repo = SyncRunRepository(async_session)
    sync_service = SyncService(
        provider=provider,
        message_repo=msg_repo,
        sync_run_repo=sync_run_repo,
        account_repo=AccountRepository(async_session),
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    assert result.status == SyncStatus.PARTIAL
    assert result.page_count == 1
    assert result.message_count == 9
    assert result.error_code == "RATE_LIMITED"

    # Verify sanitized code and absence of sensitive tokens in sync_runs
    latest_run = await sync_run_repo.get_latest_sync_run(test_account)
    assert latest_run is not None
    assert latest_run.status == "partial"
    assert latest_run.sanitized_error_code == "RATE_LIMITED"
    assert "token_abc123" not in str(latest_run.sanitized_error_code)


async def test_sync_total_failure_at_page_1(async_session: AsyncSession, test_account: int) -> None:
    provider = FakeEmailProvider(pages=[[make_message(provider_message_id="msg-1")]])
    provider.fail_at_page = 1
    provider.failure_exception = AuthenticationRequiredError("Expired session token_xyz")

    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    assert result.status == SyncStatus.FAILED
    assert result.page_count == 0
    assert result.message_count == 0
    assert result.error_code == "AUTH_REQUIRED"


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (ProviderPermissionError("Forbidden"), "PERMISSION_DENIED"),
        (ProviderResponseError("Server Error 500"), "PROVIDER_RESPONSE_ERROR"),
        (ProviderError("Network connection reset"), "PROVIDER_ERROR"),
        (RuntimeError("Unexpected crash"), "UNKNOWN_ERROR"),
    ],
)
async def test_sync_error_sanitization_branches(
    async_session: AsyncSession,
    test_account: int,
    exc: Exception,
    expected_code: str,
) -> None:
    provider = FakeEmailProvider(pages=[[make_message(provider_message_id="msg-1")]])
    provider.fail_at_page = 1
    provider.failure_exception = exc

    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    assert result.status == SyncStatus.FAILED
    assert result.error_code == expected_code


async def test_sync_cancellation_token(async_session: AsyncSession, test_account: int) -> None:
    page1 = [make_message(provider_message_id="msg-1")]
    page2 = [make_message(provider_message_id="msg-2")]

    provider = FakeEmailProvider(pages=[page1, page2])
    cancel_event = asyncio.Event()

    def on_progress(p: SyncProgress) -> None:
        if p.pages_fetched == 1:
            cancel_event.set()

    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
        progress=on_progress,
        cancel=cancel_event,
    )

    assert result.status == SyncStatus.CANCELLED
    assert result.page_count == 1
    assert result.message_count == 1


async def test_sync_task_cancellation(async_session: AsyncSession, test_account: int) -> None:
    page1 = [make_message(provider_message_id="msg-1")]

    provider = FakeEmailProvider(pages=[page1])
    sync_run_repo = SyncRunRepository(async_session)

    class _CancellingIterator:
        def __aiter__(self) -> "_CancellingIterator":
            return self

        async def __anext__(self) -> MessagePage:
            raise asyncio.CancelledError()

    def cancelling_iter(*args: object, **kwargs: object) -> AsyncIterator[MessagePage]:
        return _CancellingIterator()

    provider.iter_message_pages = cancelling_iter  # type: ignore[method-assign]

    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=sync_run_repo,
        account_repo=AccountRepository(async_session),
    )

    with pytest.raises(asyncio.CancelledError):
        await sync_service.sync_day(
            account_id=test_account,
            account_identity="user@example.com",
            window=TEST_WINDOW,
        )

    latest_run = await sync_run_repo.get_latest_sync_run(test_account)
    assert latest_run is not None
    assert latest_run.status == "cancelled"


async def test_sync_progress_callback_exception_handled(
    async_session: AsyncSession, test_account: int
) -> None:
    provider = FakeEmailProvider(pages=[[make_message(provider_message_id="msg-1")]])
    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    def buggy_progress(p: SyncProgress) -> None:
        raise ValueError("UI buggy callback")

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
        progress=buggy_progress,
    )

    assert result.status == SyncStatus.COMPLETE
    assert result.message_count == 1


async def test_sync_day_rate_limit_exceeded_marks_partial_and_records_error_code(
    async_session: AsyncSession, test_account: int
) -> None:
    provider = FakeEmailProvider(
        pages=[
            [make_message(provider_message_id="msg-1")],
            [make_message(provider_message_id="msg-2")],
        ]
    )
    provider.fail_at_page = 2
    provider.failure_exception = ProviderRateLimitError(
        "throttled",
        retry_after_seconds=120.0,
        provider_error_code="RATE_LIMIT_EXCEEDED",
    )
    sync_run_repo = SyncRunRepository(async_session)
    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=sync_run_repo,
        account_repo=AccountRepository(async_session),
        session=async_session,
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    assert result.status == SyncStatus.PARTIAL
    assert result.page_count == 1
    assert result.message_count == 1
    assert result.error_code == "RATE_LIMIT_EXCEEDED"

    latest = await sync_run_repo.get_latest_sync_run(test_account)
    assert latest is not None
    assert latest.status == "partial"
    assert latest.sanitized_error_code == "RATE_LIMIT_EXCEEDED"


async def test_sync_day_rate_limit_honored_within_cap_resumes_pagination(
    async_session: AsyncSession, test_account: int
) -> None:
    from unittest.mock import AsyncMock, patch

    provider = FakeEmailProvider(
        pages=[
            [make_message(provider_message_id="msg-1")],
            [make_message(provider_message_id="msg-2")],
        ]
    )
    provider.fail_at_page = 2
    provider.failure_exception = ProviderRateLimitError(
        "throttled",
        retry_after_seconds=5.0,
        provider_error_code="RATE_LIMIT_EXCEEDED",
    )
    sync_run_repo = SyncRunRepository(async_session)
    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=sync_run_repo,
        account_repo=AccountRepository(async_session),
        session=async_session,
    )

    progress_events: list[SyncProgress] = []
    with patch("mailbrief.services.sync.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await sync_service.sync_day(
            account_id=test_account,
            account_identity="user@example.com",
            window=TEST_WINDOW,
            progress=lambda p: progress_events.append(p),
        )

    assert result.status == SyncStatus.COMPLETE
    assert result.page_count == 2
    assert result.message_count == 2
    mock_sleep.assert_awaited_once_with(5.0)
    assert len(progress_events) >= 3


async def test_sync_work_budget_is_enforced_between_pages(
    async_session: AsyncSession, test_account: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowProvider(FakeEmailProvider):
        async def iter_message_pages(
            self,
            *,
            range_start_utc: datetime,
            range_end_utc: datetime,
            continuation: str | None = None,
        ) -> AsyncIterator[MessagePage]:
            async for page in super().iter_message_pages(
                range_start_utc=range_start_utc,
                range_end_utc=range_end_utc,
                continuation=continuation,
            ):
                await asyncio.sleep(0.05)
                yield page

    monkeypatch.setattr(sync_module, "MAX_RUN_WORK_SECONDS", 0.02)
    provider = SlowProvider(
        pages=[[make_message(provider_message_id=f"slow-{i}")] for i in range(1, 4)]
    )
    sync_service = SyncService(
        provider=provider,
        message_repo=MessageRepository(async_session),
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )

    result = await sync_service.sync_day(
        account_id=test_account,
        account_identity="user@example.com",
        window=TEST_WINDOW,
    )

    assert result.status is SyncStatus.PARTIAL
    assert result.error_code == "SYNC_RUN_TIMEOUT"
    assert result.page_count == 1


@pytest.mark.parametrize("pause", ["budget", "network", "rate", "cancel", "task_cancel"])
@pytest.mark.parametrize("metadata_failed", [False, True])
async def test_inbox_scan_resumes_after_restart(
    async_session: AsyncSession,
    test_account: int,
    monkeypatch: pytest.MonkeyPatch,
    pause: str,
    metadata_failed: bool,
) -> None:
    from mailbrief.storage.inbox_scan import InboxScanRepository

    stale = make_message(provider_message_id="archived")
    first = make_message(provider_message_id="first")
    older = make_message(provider_message_id="older")
    await MessageRepository(async_session).upsert_messages(test_account, [stale])
    await async_session.commit()
    calls: list[str | None] = []
    cancel = asyncio.Event()

    class Reader:
        async def iter_inbox_pages(
            self, continuation: str | None = None
        ) -> AsyncIterator[MessagePage]:
            calls.append(continuation)
            if continuation is None:
                yield MessagePage(
                    page_number=1,
                    messages=(first,),
                    continuation="older-page",
                    failed_message_count=int(metadata_failed),
                )
                if pause == "network":
                    raise ProviderResponseError("synthetic network failure")
                if pause == "rate":
                    raise ProviderRateLimitError("synthetic", retry_after_seconds=1000)
                if pause == "task_cancel":
                    raise asyncio.CancelledError
            # Duplicate IDs between pages must not inflate checkpoint counts.
            yield MessagePage(page_number=2, messages=(first, older))

    def progress(value: SyncProgress) -> None:
        if value.pages_fetched == 1:
            if pause == "budget":
                monkeypatch.setattr(sync_module, "MAX_RUN_WORK_SECONDS", -1)
            if pause == "cancel":
                cancel.set()

    def service(session: AsyncSession) -> SyncService:
        return SyncService(
            provider=FakeEmailProvider(),
            message_repo=MessageRepository(session),
            sync_run_repo=SyncRunRepository(session),
            account_repo=AccountRepository(session),
        )

    if pause == "task_cancel":
        with pytest.raises(asyncio.CancelledError):
            await service(async_session).sync_inbox(
                reader=Reader(),
                account_id=test_account,
                account_identity="user@example.com",
                progress=progress,
                cancel=cancel,
            )
    else:
        result = await service(async_session).sync_inbox(
            reader=Reader(),
            account_id=test_account,
            account_identity="user@example.com",
            progress=progress,
            cancel=cancel,
        )
        assert result.status in (SyncStatus.PARTIAL, SyncStatus.CANCELLED)
    checkpoint = await InboxScanRepository(async_session).resume(test_account)
    assert checkpoint is not None and checkpoint.seen_ids == {"first"}
    assert await InboxScanRepository(async_session).resume(test_account + 100) is None
    await async_session.commit()
    rows, _ = await MessageRepository(async_session).get_inbox_page(test_account)
    assert {row.provider_message_id for row in rows} == {"archived", "first"}
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    await async_session.close()
    monkeypatch.setattr(sync_module, "MAX_RUN_WORK_SECONDS", 180)
    async with factory() as reopened:
        result = await service(reopened).sync_inbox(
            reader=Reader(),
            account_id=test_account,
            account_identity="user@example.com",
        )
        assert calls == [None, "older-page"]
        assert result.page_count == 2 and result.message_count == 2
        assert result.failed_message_count == int(metadata_failed)
        assert result.status is (SyncStatus.PARTIAL if metadata_failed else SyncStatus.COMPLETE)
        rows, _ = await MessageRepository(reopened).get_inbox_page(test_account)
        assert ("archived" in {row.provider_message_id for row in rows}) == metadata_failed
        assert await InboxScanRepository(reopened).resume(test_account) is None
        account = await AccountRepository(reopened).get_by_id(test_account)
        assert account is not None and account.last_sync_at_utc is None


@pytest.mark.parametrize("invalidate", ["expired", "deleted", "clear", "oversized"])
async def test_inbox_invalid_checkpoints_restart_without_reconciliation(
    async_session: AsyncSession,
    test_account: int,
    invalidate: str,
) -> None:
    from sqlalchemy import delete

    from mailbrief.ports.errors import ProviderContinuationError
    from mailbrief.storage.inbox_scan import InboxScanRepository
    from mailbrief.storage.tables import MessageTable, SyncRunTable

    repo = MessageRepository(async_session)
    await repo.upsert_messages(test_account, [make_message(provider_message_id="first")])
    run = await SyncRunRepository(async_session).create_sync_run(
        account_id=test_account,
        range_start_utc=TEST_WINDOW.start_utc,
        range_end_utc=TEST_WINDOW.end_utc,
        status="partial",
    )
    run.message_count = 1
    run.page_count = 1
    scans = InboxScanRepository(async_session)
    await scans.remember(run.id, test_account, {"first"}, "old-page")
    if invalidate == "deleted":
        await async_session.execute(delete(MessageTable))
    elif invalidate == "clear":
        await scans.clear()
    elif invalidate == "oversized":
        run.inbox_continuation = "x" * 4097
    await async_session.commit()
    calls: list[str | None] = []

    class Reader:
        async def iter_inbox_pages(
            self, continuation: str | None = None
        ) -> AsyncIterator[MessagePage]:
            calls.append(continuation)
            if continuation:
                raise ProviderContinuationError("synthetic expired cursor")
            yield MessagePage(page_number=1, messages=(make_message(provider_message_id="fresh"),))

    service = SyncService(
        provider=FakeEmailProvider(),
        message_repo=repo,
        sync_run_repo=SyncRunRepository(async_session),
        account_repo=AccountRepository(async_session),
    )
    result = await service.sync_inbox(
        reader=Reader(), account_id=test_account, account_identity="user@example.com"
    )
    if invalidate == "expired":
        assert result.status is SyncStatus.PARTIAL
        preserved = await repo.get_by_provider_message_id(test_account, "first")
        assert preserved is not None and preserved.is_in_inbox
        result = await service.sync_inbox(
            reader=Reader(), account_id=test_account, account_identity="user@example.com"
        )
        assert calls == ["old-page", None]
    else:
        assert calls == [None]
    assert result.status is SyncStatus.COMPLETE
    old_run = await async_session.get(SyncRunTable, run.id)
    assert old_run is not None and old_run.inbox_continuation is None


async def test_inbox_checkpoint_survives_database_engine_shutdown(tmp_path: Path) -> None:
    from mailbrief.storage.database import Database
    from mailbrief.storage.migrate import upgrade_database

    path = tmp_path / "restart.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    calls: list[str | None] = []

    class Reader:
        async def iter_inbox_pages(
            self, continuation: str | None = None
        ) -> AsyncIterator[MessagePage]:
            calls.append(continuation)
            if continuation is None:
                yield MessagePage(page_number=1, messages=(make_message(),), continuation="next")
                raise ProviderResponseError("synthetic interruption")
            yield MessagePage(page_number=2, messages=())

    async def run(session: AsyncSession, account_id: int) -> SyncStatus:
        result = await SyncService(
            provider=FakeEmailProvider(),
            message_repo=MessageRepository(session),
            sync_run_repo=SyncRunRepository(session),
            account_repo=AccountRepository(session),
        ).sync_inbox(reader=Reader(), account_id=account_id, account_identity="user@example.com")
        return result.status

    try:
        async with database.session() as session:
            account = await AccountRepository(session).upsert(
                AccountIdentity(
                    provider=ProviderKind.MICROSOFT,
                    provider_account_id="restart",
                    email_address="user@example.com",
                )
            )
            await session.commit()
            account_id = account.id
            assert await run(session, account_id) is SyncStatus.PARTIAL
    finally:
        await database.dispose()
    reopened = Database.from_path(path)
    try:
        async with reopened.session() as session:
            assert await run(session, account_id) is SyncStatus.COMPLETE
            assert calls == [None, "next"]
    finally:
        await reopened.dispose()


@pytest.mark.parametrize("cancel_at", ["never", "before", "after"])
async def test_bounded_browsing_preserves_unobserved_cache_and_cancellation(
    async_session: AsyncSession,
    test_account: int,
    cancel_at: str,
) -> None:
    repo = MessageRepository(async_session)
    await repo.upsert_messages(
        test_account,
        [
            make_message(provider_message_id="unobserved"),
            make_message(provider_message_id="archived"),
            make_message(provider_message_id="discarded"),
        ],
    )
    await async_session.commit()
    cancel = asyncio.Event()
    if cancel_at == "before":
        cancel.set()
    calls = 0

    class Reader:
        async def fetch_mailbox_page(
            self, *, continuation: str | None = None, page_size: int = 50, query: str = ""
        ) -> MailboxPage:
            nonlocal calls
            calls += 1
            if cancel_at == "after":
                cancel.set()
            return MailboxPage(
                messages=(
                    make_message(provider_message_id="new"),
                    make_message(provider_message_id="archived", is_in_inbox=False),
                ),
                attempted=3,
                discarded_ids=frozenset({"discarded"}),
                continuation="older",
            )

    service = InboxService(async_session, Reader())
    if cancel_at != "never":
        with pytest.raises(asyncio.CancelledError):
            await service.load_page(test_account, cancel)
        rows, _ = await repo.get_inbox_page(test_account)
        assert {r.provider_message_id for r in rows} == {"unobserved", "archived", "discarded"}
        assert calls == int(cancel_at == "after")
    else:
        result = await service.load_page(test_account, cancel)
        assert result.continuation == "older"
        rows, _ = await repo.get_inbox_page(test_account)
        assert {r.provider_message_id for r in rows} == {"unobserved", "new"}
        assert await repo.get_by_provider_message_id(test_account, "archived") is not None
        assert await repo.get_by_provider_message_id(test_account, "discarded") is None
