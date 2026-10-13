"""ADR 0020's stored signals: Gmail's category, star and bulk mail, the owner's review marks
and what they say about each sender."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, update

from mailbrief.domain.messages import (
    AccountIdentity,
    EmailContact,
    InboxCategory,
    NormalizedMessage,
    ProviderKind,
    SenderHistory,
)
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from mailbrief.storage.tables import (
    AccountTable,
    ActionSourceTable,
    ActionTable,
    DigestItemTable,
    DigestTable,
    MessageTable,
)
from tests.factories import make_message

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=90)
IDENTITY = AccountIdentity(
    provider=ProviderKind.GMAIL,
    provider_account_id="owner@example.com",
    email_address="owner@example.com",
    account_addresses=("owner@example.com", "alias@example.org"),
)


@pytest.fixture
async def database(tmp_path: Path) -> AsyncGenerator[Database]:
    db = Database.from_path(tmp_path / "signals.sqlite3")
    await db.create_schema_for_tests()
    try:
        yield db
    finally:
        await db.dispose()


def message(
    identifier: str, sender: str = "alex@example.com", **overrides: object
) -> NormalizedMessage:
    return make_message(
        provider=ProviderKind.GMAIL,
        provider_account_id=IDENTITY.provider_account_id,
        provider_message_id=identifier,
        sender=EmailContact(address=sender),
        web_link=f"https://mail.google.com/mail/u/0/#all/{identifier}",
        **overrides,
    )


async def seeded(database: Database, *messages: NormalizedMessage) -> AccountTable:
    async with database.transaction() as session:
        account = await AccountRepository(session).upsert(IDENTITY)
        await MessageRepository(session).upsert_messages(account.id, list(messages))
        return account


async def test_the_new_metadata_round_trips_and_a_resync_updates_it(database: Database) -> None:
    account = await seeded(
        database,
        message("m1", category=InboxCategory.PROMOTIONS, is_starred=True, is_bulk=True),
        message("m2"),
    )
    async with database.session() as session:
        rows = {
            row.provider_message_id: MessageRepository.to_domain(
                row, IDENTITY.provider_account_id, ProviderKind.GMAIL
            )
            for row in await MessageRepository(session).get_by_provider_ids(
                account.id, ["m1", "m2"]
            )
        }
    assert (rows["m1"].category, rows["m1"].is_starred, rows["m1"].is_bulk) == (
        InboxCategory.PROMOTIONS,
        True,
        True,
    )
    assert (rows["m2"].category, rows["m2"].is_starred, rows["m2"].is_bulk) == (
        None,
        False,
        False,
    )

    await seeded(database, message("m1", category=InboxCategory.PERSONAL))

    async with database.session() as session:
        row = await MessageRepository(session).get_by_provider_message_id(account.id, "m1")
    assert row is not None
    assert (row.category, row.is_starred, row.is_bulk) == ("personal", False, False)


async def test_a_category_this_version_does_not_know_reads_as_unknown(database: Database) -> None:
    account = await seeded(database, message("m1"))
    async with database.transaction() as session:
        await session.execute(update(MessageTable).values(category="travel"))
    async with database.session() as session:
        row = await MessageRepository(session).get_by_provider_message_id(account.id, "m1")
        assert row is not None
        domain = MessageRepository.to_domain(row, IDENTITY.provider_account_id, ProviderKind.GMAIL)
    assert domain.category is None


async def marks(database: Database, account: AccountTable) -> dict[str, tuple[bool, bool]]:
    """Each message's (declined, wanted) marks."""
    async with database.session() as session:
        rows = await MessageRepository(session).get_by_provider_ids(account.id, ["m1", "m2"])
        return {
            row.provider_message_id: (
                row.review_declined_at_utc is not None,
                row.review_wanted_at_utc is not None,
            )
            for row in rows
        }


async def test_wanting_a_message_forgets_its_decline_and_declining_forgets_wanting(
    database: Database,
) -> None:
    account = await seeded(database, message("m1"), message("m2"))
    async with database.transaction() as session:
        repository = MessageRepository(session)
        assert await repository.set_review_declined(account.id, ["m1", "m2"], NOW) == 2
    async with database.transaction() as session:
        assert await MessageRepository(session).set_review_wanted(account.id, ["m1"], NOW) == 1
    assert await marks(database, account) == {"m1": (False, True), "m2": (True, False)}

    async with database.transaction() as session:
        repository = MessageRepository(session)
        assert await repository.set_review_wanted(account.id, ["m1"], NOW) == 0  # Already.
        assert await repository.set_review_declined(account.id, ["m1"], NOW) == 1
    assert await marks(database, account) == {"m1": (True, False), "m2": (True, False)}

    async with database.transaction() as session:
        repository = MessageRepository(session)
        assert await repository.set_review_wanted(account.id, ["m2", "missing"], None) == 0
        assert await repository.set_review_declined(account.id, ["m1", "m2"], None) == 2
    assert await marks(database, account) == {"m1": (False, False), "m2": (False, False)}


async def test_a_mark_already_set_keeps_its_first_time(database: Database) -> None:
    account = await seeded(database, message("m1"))
    async with database.transaction() as session:
        await MessageRepository(session).set_review_wanted(account.id, ["m1"], NOW)
    async with database.transaction() as session:
        await MessageRepository(session).set_review_wanted(
            account.id, ["m1"], NOW + timedelta(days=1)
        )
    async with database.session() as session:
        row = await MessageRepository(session).get_by_provider_message_id(account.id, "m1")
    assert row is not None and row.review_wanted_at_utc == NOW


async def test_count_wanted_counts_marks_since_the_given_time(database: Database) -> None:
    account = await seeded(database, message("m1"), message("m2"))
    async with database.transaction() as session:
        repository = MessageRepository(session)
        await repository.set_review_wanted(account.id, ["m1"], NOW - timedelta(days=20))
        await repository.set_review_wanted(account.id, ["m2"], NOW - timedelta(days=1))
    async with database.session() as session:
        repository = MessageRepository(session)
        assert await repository.count_wanted(account.id, NOW - timedelta(days=14)) == 1
        assert await repository.count_wanted(account.id, NOW - timedelta(days=30)) == 2


async def add_action(
    database: Database,
    sender: str,
    *,
    created: datetime = NOW,
    deleted: bool = False,
    account: str = IDENTITY.provider_account_id,
    sources: int = 1,
) -> None:
    async with database.transaction() as session:
        action_id = await session.scalar(
            insert(ActionTable)
            .values(
                public_id=str(uuid.uuid4()),
                title="Reply to them",
                ownership="mine",
                status="open",
                created_at_utc=created,
                updated_at_utc=created,
                deleted_at_utc=NOW if deleted else None,
            )
            .returning(ActionTable.id)
        )
        for number in range(sources):
            await session.execute(
                insert(ActionSourceTable).values(
                    action_id=action_id,
                    provider_message_id=f"source-{action_id}-{number}",
                    subject="Subject",
                    sender_address=sender,
                    web_link="https://mail.google.com/mail/u/0/#all/x",
                    received_at_utc=created,
                    provider="gmail",
                    provider_account_id=account,
                )
            )


async def history(database: Database, account: AccountTable) -> dict[str, SenderHistory]:
    async with database.session() as session:
        return await MessageRepository(session).sender_history(account, SINCE)


async def test_sender_history_counts_wanted_left_out_and_acted_on_mail(
    database: Database,
) -> None:
    account = await seeded(
        database,
        message("w1", "Alex@Example.com"),
        message("w2", "alex@example.com"),
        message("s1", "news@shop.example"),
        message("s2", "news@shop.example"),
        message("old", "news@shop.example"),
        message("plain", "quiet@example.com"),
        message("own", "Alias@Example.org"),
    )
    async with database.transaction() as session:
        repository = MessageRepository(session)
        await repository.set_review_wanted(account.id, ["w1", "w2", "own"], NOW)
        await repository.set_review_declined(account.id, ["s1", "s2"], NOW)
        await repository.set_review_declined(account.id, ["old"], SINCE - timedelta(days=1))
    await add_action(database, "boss@example.com", sources=2)  # One action, two sources.
    await add_action(database, "alex@example.com")
    await add_action(database, "gone@example.com", deleted=True)
    await add_action(database, "stale@example.com", created=SINCE - timedelta(days=1))
    await add_action(database, "other@example.com", account="someone-else@example.com")

    assert await history(database, account) == {
        "alex@example.com": SenderHistory(wanted=3),
        "news@shop.example": SenderHistory(skipped=2),
        "boss@example.com": SenderHistory(wanted=1),
    }


async def test_sender_history_is_empty_without_choices(database: Database) -> None:
    account = await seeded(database, message("m1"))
    assert await history(database, account) == {}


async def add_brief(
    database: Database, account: AccountTable, *keys: str, generated: datetime = NOW
) -> None:
    async with database.transaction() as session:
        digest_id = await session.scalar(
            insert(DigestTable)
            .values(
                account_id=account.id,
                local_date=generated.date(),
                timezone_name="UTC",
                status="complete",
                generated_at_utc=generated,
            )
            .returning(DigestTable.id)
        )
        rows = await MessageRepository(session).get_by_provider_ids(account.id, keys)
        for position, row in enumerate(rows):
            await session.execute(
                insert(DigestItemTable).values(
                    digest_id=digest_id, message_id=row.id, position=position, section="actions"
                )
            )


async def test_mail_kept_in_a_brief_counts_for_its_sender(database: Database) -> None:
    account = await seeded(
        database,
        message("k1", "colleague@example.com"),
        message("s1", "colleague@example.com"),
        message("s2", "colleague@example.com"),
        message("old", "boss@example.com"),
        message("s3", "boss@example.com"),
    )
    async with database.transaction() as session:
        await MessageRepository(session).set_review_declined(account.id, ["s1", "s2", "s3"], NOW)
    await add_brief(database, account, "k1")
    await add_brief(database, account, "old", generated=SINCE - timedelta(days=1))

    assert await history(database, account) == {
        "colleague@example.com": SenderHistory(skipped=2, kept=1),
        "boss@example.com": SenderHistory(skipped=1),  # Its brief is older than the window.
    }
