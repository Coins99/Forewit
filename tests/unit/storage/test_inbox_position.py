"""Where a cached message sits in the Inbox page's order (the command palette opens it)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.messages import AccountIdentity, ProviderKind
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from tests.factories import make_message

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema_for_tests()
    async with database.session() as active:
        yield active
    await database.dispose()


async def test_the_position_follows_the_inbox_page_order(session: AsyncSession) -> None:
    account = await AccountRepository(session).upsert(
        AccountIdentity(
            provider=ProviderKind.GMAIL,
            provider_account_id="owner@example.com",
            email_address="owner@example.com",
        )
    )
    repository = MessageRepository(session)
    received = {
        "newest": NOW,
        "tie-a": NOW - timedelta(hours=1),
        "tie-b": NOW - timedelta(hours=1),
        "oldest": NOW - timedelta(days=30),
        "archived": NOW - timedelta(minutes=5),
    }
    await repository.upsert_messages(
        account.id,
        [
            make_message(
                provider=ProviderKind.GMAIL,
                provider_message_id=key,
                received_at_utc=at,
                is_in_inbox=key != "archived",
            )
            for key, at in received.items()
        ],
    )
    await session.commit()

    rows, _ = await repository.get_inbox_page(account.id)
    order = [row.provider_message_id for row in rows]
    positions = {key: await repository.inbox_position(account.id, key) for key in order}

    assert positions == {key: number for number, key in enumerate(order)}
    assert order[:2] == ["newest", "tie-a"]  # Ties keep the page's order.
    assert await repository.inbox_position(account.id, "archived") is None
    assert await repository.inbox_position(account.id, "missing") is None
