"""The owner's side of ranking in a run (ADR 0020): important senders, messages they pick
that weren't suggested, and what their choices teach about each sender."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.messages import NormalizedMessage, RankReason
from mailbrief.services.application import ApplicationService
from mailbrief.services.ranking import ExcludedSenderError
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import MessageRepository
from mailbrief.storage.tables import AccountTable
from tests.unit.services.test_application import NOW, ZONE
from tests.unit.services.test_application_outside import message, scene
from tests.unit.services.test_declines import Choosing, loud


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema_for_tests()
    async with database.session() as active:
        yield active
    await database.dispose()


def from_(sender: str, key: str, hours_ago: float, *, high: bool = True) -> NormalizedMessage:
    if high:
        return loud(key, hours_ago=hours_ago, sender=sender)
    return message(key, hours_ago=hours_ago, sender=sender)


async def run(service: ApplicationService, **options: object) -> tuple[str, ...]:
    _, shortlist = await service.prepare_daily_shortlist(
        tz_key=ZONE,
        now_utc=NOW,
        shortlist_limit=3,
        **options,  # type: ignore[arg-type]
    )
    return tuple(item.message.provider_message_id for item in shortlist)


async def reasons(session: AsyncSession, account: AccountTable, key: str) -> list[str]:
    await session.commit()
    row = await MessageRepository(session).get_by_provider_message_id(account.id, key)
    assert row is not None
    return row.rank_reasons_json


async def wanted(session: AsyncSession, account: AccountTable, *keys: str) -> set[str]:
    await session.commit()
    rows = await MessageRepository(session).get_by_provider_ids(account.id, keys)
    return {row.provider_message_id for row in rows if row.review_wanted_at_utc is not None}


INBOX = [
    from_("sam@example.com", "a1", 1),
    from_("sam@example.com", "a2", 2),
    from_("sam@example.com", "a3", 3),
    from_("partner@example.com", "quiet", 9, high=False),
]


async def test_an_important_sender_is_suggested_ahead_of_louder_mail(
    session: AsyncSession,
) -> None:
    service, account = await scene(session, today=list(INBOX), tracked=())

    assert await run(service) == ("a1", "a2", "a3")
    assert await run(service, important_senders=("partner@example.com",)) == (
        "quiet",
        "a1",
        "a2",
    )
    assert RankReason.IMPORTANT_SENDER.value in await reasons(session, account, "quiet")


async def test_an_exclusion_wins_over_an_important_address_in_its_domain(
    session: AsyncSession,
) -> None:
    service, _ = await scene(session, today=list(INBOX), tracked=())

    chosen = await run(
        service,
        excluded_senders=("@example.com",),
        important_senders=("partner@example.com",),
    )

    assert chosen == ()
    with pytest.raises(ExcludedSenderError):
        await run(
            service,
            excluded_senders=("@example.com",),
            important_senders=("partner@example.com",),
            include_ids=("quiet",),
        )


async def test_a_pick_that_was_not_suggested_is_remembered_and_lifts_its_sender(
    session: AsyncSession,
) -> None:
    service, account = await scene(session, today=list(INBOX), tracked=())

    assert await run(service, shortlist_gate=Choosing(("a1", "a2", "quiet"))) == (
        "a1",
        "a2",
        "quiet",
    )

    assert await wanted(session, account, "a1", "a2", "a3", "quiet") == {"quiet"}
    await run(service)
    assert RankReason.WANTED_SENDER.value in await reasons(session, account, "quiet")
    assert RankReason.WANTED_SENDER.value not in await reasons(session, account, "a1")


async def test_include_ids_count_as_a_pick_but_an_automatic_choice_does_not(
    session: AsyncSession,
) -> None:
    service, account = await scene(session, today=list(INBOX), tracked=())

    await run(service)
    assert await wanted(session, account, "a1", "a2", "a3", "quiet") == set()

    await run(service, include_ids=("quiet",))
    assert await wanted(session, account, "a1", "a2", "a3", "quiet") == {"quiet"}


async def test_a_sender_left_out_twice_ranks_lower_and_one_picked_ranks_higher(
    session: AsyncSession,
) -> None:
    today = [
        from_("news@shop.example", "n1", 1),
        from_("news@shop.example", "n2", 2),
        from_("news@shop.example", "n3", 3),
        from_("sam@example.com", "s1", 5, high=False),
        from_("sam@example.com", "s2", 6, high=False),
    ]
    service, account = await scene(session, today=today, tracked=())

    # n1 to n3 are suggested; the owner keeps n3 and picks two of Sam's instead.
    assert await run(service, shortlist_gate=Choosing(("n3", "s1", "s2"))) == ("n3", "s1", "s2")
    await run(service, shortlist_gate=Choosing(("n3", "s1", "s2")))

    assert RankReason.SKIPPED_SENDER.value in await reasons(session, account, "n3")
    assert RankReason.WANTED_SENDER.value in await reasons(session, account, "s1")


async def test_a_decline_after_a_pick_takes_the_wanted_mark_back(session: AsyncSession) -> None:
    service, account = await scene(session, today=list(INBOX), tracked=())
    await run(service, shortlist_gate=Choosing(("a1", "a2", "quiet")))
    assert await wanted(session, account, "quiet") == {"quiet"}

    # a3 stays out and quiet's sender now ranks higher, so the review suggests quiet; the
    # owner leaves it out this time.
    await run(service, shortlist_gate=Choosing(("a1",)))

    assert await wanted(session, account, "quiet") == set()
