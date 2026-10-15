"""Searching the owner's local records: actions, writing, events, Google events and the
connected account's cached mail metadata."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionEdit, StepEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.drafts import DraftKind
from mailbrief.domain.messages import AccountIdentity, EmailContact, ProviderKind
from mailbrief.domain.search import (
    QUERY_MAX_CHARS,
    QUERY_MAX_TERMS,
    SearchKind,
    query_terms,
)
from mailbrief.services.actions import ActionService
from mailbrief.services.search import SearchService, like_pattern
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from mailbrief.storage.tables import (
    CalendarEventTable,
    DraftTable,
    GoogleCalendarTable,
    GoogleEventTable,
)
from tests.factories import make_message

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
OWNER = "owner@example.com"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema_for_tests()
    async with database.session() as active:
        yield active
    await database.dispose()


def test_query_terms_need_two_characters_and_keep_at_most_five_words() -> None:
    assert query_terms("") == ()
    assert query_terms(" a ") == ()
    assert query_terms("ab") == ("ab",)
    assert query_terms("a b") == ("a", "b")
    assert query_terms("Budget budget BUDGET q3") == ("Budget", "q3")
    assert (
        query_terms("one two three four five six")
        == ("one", "two", "three", "four", "five")[:QUERY_MAX_TERMS]
    )
    assert query_terms("x" * (QUERY_MAX_CHARS + 50)) == ("x" * QUERY_MAX_CHARS,)


def test_like_patterns_take_wildcards_literally() -> None:
    assert like_pattern("50%") == r"%50\%%"
    assert like_pattern("a_b") == r"%a\_b%"
    assert like_pattern("c:\\x") == "%c:\\\\x%"


async def add_task(
    session: AsyncSession, title: str, *, notes: str = "", steps: tuple[str, ...] = ()
) -> str:
    action = await ActionService(session).create(
        ActionEdit(
            title=title,
            ownership=ActionOwnership.MINE,
            effort=None,
            priority=None,
            tags=(),
            estimate_minutes=None,
            target_date=None,
            notes=notes,
        ),
        tuple(StepEdit(step_id=None, text=step, done=False) for step in steps),
    )
    return action.public_id


async def test_actions_match_their_title_notes_or_steps_and_every_word(
    session: AsyncSession,
) -> None:
    by_title = await add_task(session, "Approve the Q3 budget")
    by_notes = await add_task(session, "Call Priya", notes="About the budget review")
    by_step = await add_task(session, "Prepare finance deck", steps=("Add budget slide",))
    await add_task(session, "Water the plants")

    found = await SearchService(session).search("BUDGET")

    assert {hit.public_id for hit in found.hits} == {by_title, by_notes, by_step}
    assert all(hit.kind is SearchKind.ACTION for hit in found.hits)
    narrowed = await SearchService(session).search("budget review")
    assert [hit.public_id for hit in narrowed.hits] == [by_notes]
    assert (await SearchService(session).search("budget  plants")).hits == ()


async def test_open_actions_come_first_and_deleted_ones_never(session: AsyncSession) -> None:
    service = ActionService(session)
    done_id = await add_task(session, "Renew passport")
    done = await service.get(done_id)
    await service.complete(done_id, done.revision)
    open_id = await add_task(session, "Renew library card")
    gone_id = await add_task(session, "Renew gym")
    gone = await service.get(gone_id)
    await service.delete(gone_id, gone.revision)

    found = (await SearchService(session).search("renew")).hits

    assert [(hit.public_id, hit.open) for hit in found] == [(open_id, True), (done_id, False)]


async def test_wildcards_in_a_query_match_only_themselves(session: AsyncSession) -> None:
    exact = await add_task(session, "Pay 50% deposit")
    await add_task(session, "Pay 500 dollars")

    found = await SearchService(session).search("50%")

    assert [hit.public_id for hit in found.hits] == [exact]


def draft_row(kind: DraftKind, title: str, body: str, *, deleted: bool = False) -> DraftTable:
    return DraftTable(
        public_id=str(uuid.uuid4()),
        kind=kind.value,
        title=title,
        body=body,
        created_at_utc=NOW,
        updated_at_utc=NOW,
        deleted_at_utc=NOW if deleted else None,
        revision=1,
    )


async def test_writing_matches_title_or_body_and_names_untitled_notes(
    session: AsyncSession,
) -> None:
    titled = draft_row(DraftKind.EMAIL, "Offer letter", "Dear Sam, the offer stands.")
    untitled = draft_row(DraftKind.NOTE, "", "\nOffer questions to ask\nMore")
    gone = draft_row(DraftKind.NOTE, "Offer", "deleted", deleted=True)
    session.add_all([titled, untitled, gone])
    await session.commit()

    found = (await SearchService(session).search("offer")).hits

    assert {(hit.title, hit.draft_kind) for hit in found} == {
        ("Offer letter", DraftKind.EMAIL),
        ("Offer questions to ask", DraftKind.NOTE),
    }
    assert all(hit.kind is SearchKind.WRITING for hit in found)


async def test_events_and_google_events_carry_what_opens_them(session: AsyncSession) -> None:
    session.add_all(
        [
            CalendarEventTable(
                public_id=str(uuid.uuid4()),
                title="Dentist",
                notes="Bring the insurance card",
                all_day=False,
                starts_at_utc=NOW,
                ends_at_utc=NOW + timedelta(hours=1),
                time_zone="UTC",
                created_at_utc=NOW,
                updated_at_utc=NOW,
                revision=1,
            ),
            CalendarEventTable(
                public_id=str(uuid.uuid4()),
                title="Insurance renewal",
                all_day=True,
                start_date=date(2026, 10, 20),
                last_date=date(2026, 10, 20),
                time_zone="UTC",
                created_at_utc=NOW,
                updated_at_utc=NOW,
                revision=1,
            ),
        ]
    )
    calendar = GoogleCalendarTable(
        calendar_id="work@example.com", name="Work", is_primary=False, updated_at_utc=NOW
    )
    session.add(calendar)
    await session.flush()
    session.add(
        GoogleEventTable(
            calendar_row_id=calendar.id,
            event_id="review",
            title="Insurance review",
            all_day=False,
            starts_at_utc=NOW + timedelta(days=1),
            ends_at_utc=NOW + timedelta(days=1, hours=1),
            web_link="https://calendar.google.com/calendar/event?eid=x",
            fetched_at_utc=NOW,
        )
    )
    await session.commit()

    found = (await SearchService(session).search("insurance")).hits

    assert [(hit.kind, hit.title) for hit in found] == [
        (SearchKind.EVENT, "Insurance renewal"),
        (SearchKind.EVENT, "Dentist"),
        (SearchKind.GOOGLE_EVENT, "Insurance review"),
    ]
    assert found[0].day == date(2026, 10, 20) and found[0].when is None
    assert found[1].when == NOW
    assert (found[2].detail, found[2].calendar_id, found[2].event_id) == (
        "Work",
        "work@example.com",
        "review",
    )


async def add_mail(session: AsyncSession, email: str, *messages: tuple[str, str, bool]) -> None:
    account = await AccountRepository(session).upsert(
        AccountIdentity(provider=ProviderKind.GMAIL, provider_account_id=email, email_address=email)
    )
    await MessageRepository(session).upsert_messages(
        account.id,
        [
            make_message(
                provider=ProviderKind.GMAIL,
                provider_account_id=email,
                provider_message_id=key,
                subject=subject,
                sender=EmailContact(name="Lena Park", address="lena@example.com"),
                body_preview="Snippet about the lease",
                received_at_utc=NOW - timedelta(hours=number),
                is_in_inbox=inbox,
                web_link=f"https://mail.google.com/mail/u/0/#all/{key}",
            )
            for number, (key, subject, inbox) in enumerate(messages)
        ],
    )
    await session.commit()


async def test_mail_is_searched_only_for_the_connected_account(session: AsyncSession) -> None:
    await add_mail(session, OWNER, ("m1", "Lease renewal", True), ("m2", "Old lease", False))
    await add_mail(session, "other@example.com", ("x1", "Lease elsewhere", True))
    service = SearchService(session)

    found = (await service.search("lease", account_email=OWNER)).hits

    assert [(hit.message_id, hit.open, hit.detail) for hit in found] == [
        ("m1", True, "Lena Park"),
        ("m2", False, "Lena Park"),
    ]
    assert found[0].web_link == "https://mail.google.com/mail/u/0/#all/m1"
    assert (await service.search("lease")).hits == ()  # No account connected.
    by_sender = await service.search("lena@example", account_email=OWNER)
    assert len(by_sender.hits) == 2


async def test_each_kind_is_bounded_and_says_when_there_are_more(session: AsyncSession) -> None:
    for number in range(4):
        await add_task(session, f"Invoice {number}")

    found = await SearchService(session).search("invoice", limit=3)

    assert len(found.hits) == 3 and found.more == frozenset({SearchKind.ACTION})
    assert (await SearchService(session).search("invoice", limit=4)).more == frozenset()
    with pytest.raises(ValueError):
        await SearchService(session).search("invoice", limit=0)


async def test_a_short_query_reads_nothing(session: AsyncSession) -> None:
    await add_task(session, "a")
    found = await SearchService(session).search(" a ")
    assert found.hits == () and found.more == frozenset()
