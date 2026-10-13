"""ADR 0020's ranking rules: Gmail's star, tabs and bulk mail, and the owner's signals."""

from datetime import timedelta

import pytest

from mailbrief.domain.messages import (
    EmailContact,
    InboxCategory,
    RankReason,
    SenderHistory,
    SenderSignal,
)
from mailbrief.services.ranking import (
    NO_SIGNALS,
    SKIPS_TO_LEARN,
    RankSignals,
    rank_messages,
    reason_text,
    score_message,
    select_shortlist,
    sender_signal,
    sender_signals,
)
from tests.unit.services.test_ranking import NOW, USER_EMAIL, make_baseline_message

PITCH = "Action required: confirm your order today and review our deals"


def score(**overrides: object) -> tuple[int, tuple[RankReason, ...]]:
    return score_message(make_baseline_message(**overrides), user_email=USER_EMAIL, now_utc=NOW)


def test_a_star_adds_fifteen() -> None:
    assert score(is_starred=True) == (15, (RankReason.STARRED,))


@pytest.mark.parametrize(
    ("category", "points", "reason"),
    [
        (InboxCategory.PROMOTIONS, -20, RankReason.PROMOTIONS),
        (InboxCategory.SOCIAL, -15, RankReason.SOCIAL),
        (InboxCategory.FORUMS, -8, RankReason.FORUMS),
        (InboxCategory.UPDATES, -5, RankReason.UPDATES),
    ],
)
def test_each_tab_but_primary_lowers_the_score(
    category: InboxCategory, points: int, reason: RankReason
) -> None:
    assert score(category=category) == (points, (reason,))


def test_primary_and_unknown_tabs_change_nothing() -> None:
    assert score(category=InboxCategory.PERSONAL) == (0, ())
    assert score(category=None) == (0, ())


@pytest.mark.parametrize("category", [InboxCategory.PROMOTIONS, InboxCategory.SOCIAL])
def test_marketing_wording_earns_nothing_in_promotions_and_social(
    category: InboxCategory,
) -> None:
    plain, plain_reasons = score(subject=PITCH)
    assert RankReason.ACTION_OR_DEADLINE_LANGUAGE in plain_reasons
    assert RankReason.APPROVAL_OR_REPLY_LANGUAGE in plain_reasons
    assert plain == 30

    tabbed, reasons = score(subject=PITCH, category=category)

    assert RankReason.ACTION_OR_DEADLINE_LANGUAGE not in reasons
    assert RankReason.APPROVAL_OR_REPLY_LANGUAGE not in reasons
    assert tabbed < 0


@pytest.mark.parametrize("category", [InboxCategory.UPDATES, InboxCategory.FORUMS])
def test_wording_still_counts_in_updates_and_forums(category: InboxCategory) -> None:
    """A bill that is due or a review someone asks for is often in these tabs."""
    _, reasons = score(subject="Your payment is due Friday", category=category)
    assert RankReason.ACTION_OR_DEADLINE_LANGUAGE in reasons


def test_bulk_mail_lowers_the_score_only_when_its_tab_did_not() -> None:
    assert score(is_bulk=True) == (-8, (RankReason.BULK_MAIL,))
    assert score(is_bulk=True, category=InboxCategory.PERSONAL) == (-8, (RankReason.BULK_MAIL,))
    assert score(is_bulk=True, category=InboxCategory.PROMOTIONS) == (
        -20,
        (RankReason.PROMOTIONS,),
    )


def test_an_important_sender_adds_thirty_by_address_or_domain() -> None:
    signals = RankSignals(important_senders=("partner@example.com", "@school.edu"))
    for address in ("Partner@Example.com", "dean@school.edu", "prof@cs.school.edu"):
        message = make_baseline_message(sender=EmailContact(address=address))
        assert score_message(message, user_email=USER_EMAIL, now_utc=NOW, signals=signals) == (
            30,
            (RankReason.IMPORTANT_SENDER,),
        )
    other = make_baseline_message(sender=EmailContact(address="someone@notschool.edu"))
    assert score_message(other, user_email=USER_EMAIL, now_utc=NOW, signals=signals) == (0, ())


def test_an_important_sender_still_carries_the_other_rules() -> None:
    signals = RankSignals(important_senders=("@uni.edu",))
    message = make_baseline_message(
        sender=EmailContact(address="noreply@uni.edu"),
        category=InboxCategory.UPDATES,
        is_bulk=True,
    )
    assert score_message(message, user_email=USER_EMAIL, now_utc=NOW, signals=signals) == (
        30 - 12 - 5,
        (RankReason.AUTOMATED_SENDER, RankReason.UPDATES, RankReason.IMPORTANT_SENDER),
    )


@pytest.mark.parametrize(
    ("signal", "points", "reason"),
    [
        (SenderSignal.WANTED, 10, RankReason.WANTED_SENDER),
        (SenderSignal.SKIPPED, -10, RankReason.SKIPPED_SENDER),
    ],
)
def test_what_the_owner_chose_before_moves_the_sender_ten(
    signal: SenderSignal, points: int, reason: RankReason
) -> None:
    signals = RankSignals(senders={"alex@example.com": signal})
    message = make_baseline_message(sender=EmailContact(address=" Alex@Example.com "))
    assert score_message(message, user_email=USER_EMAIL, now_utc=NOW, signals=signals) == (
        points,
        (reason,),
    )


def test_an_important_sender_ignores_what_was_learned() -> None:
    signals = RankSignals(
        important_senders=("alex@example.com",),
        senders={"alex@example.com": SenderSignal.SKIPPED},
    )
    assert score_message(
        make_baseline_message(), user_email=USER_EMAIL, now_utc=NOW, signals=signals
    ) == (30, (RankReason.IMPORTANT_SENDER,))


def test_new_rules_come_after_the_original_ones_in_order() -> None:
    signals = RankSignals(senders={"alex@example.com": SenderSignal.WANTED})
    message = make_baseline_message(
        is_read=False,
        is_starred=True,
        category=InboxCategory.FORUMS,
        received_at_utc=NOW - timedelta(hours=1),
    )
    assert score_message(message, user_email=USER_EMAIL, now_utc=NOW, signals=signals) == (
        8 + 8 + 15 - 8 + 10,
        (
            RankReason.UNREAD,
            RankReason.VERY_RECENT,
            RankReason.STARRED,
            RankReason.FORUMS,
            RankReason.WANTED_SENDER,
        ),
    )


def test_promotions_fall_below_the_threshold_that_real_mail_reaches() -> None:
    """Unread, addressed to the owner and recent: the same, but for the Promotions tab."""
    fresh = {
        "is_read": False,
        "to_recipients": (EmailContact(address=USER_EMAIL),),
        "received_at_utc": NOW - timedelta(hours=1),
        "subject": PITCH,
    }
    promotion = make_baseline_message(
        provider_message_id="promo", category=InboxCategory.PROMOTIONS, **fresh
    )
    person = make_baseline_message(
        provider_message_id="person",
        category=InboxCategory.PERSONAL,
        **{**fresh, "subject": "Lunch on Thursday?"},
    )

    ranked = rank_messages([promotion, person], user_email=USER_EMAIL, now_utc=NOW)

    assert [item.score for item in ranked] == [4, 24]
    assert [item.message.provider_message_id for item in select_shortlist(ranked)] == [
        "person",
        "promo",  # Only as backfill: fewer than three messages qualify.
    ]


def test_rank_messages_passes_the_signals_on() -> None:
    signals = RankSignals(important_senders=("alex@example.com",))
    (ranked,) = rank_messages(
        [make_baseline_message()], user_email=USER_EMAIL, now_utc=NOW, signals=signals
    )
    assert ranked.reasons == (RankReason.IMPORTANT_SENDER,)
    assert rank_messages([make_baseline_message()], user_email=USER_EMAIL, now_utc=NOW) == (
        rank_messages(
            [make_baseline_message()], user_email=USER_EMAIL, now_utc=NOW, signals=NO_SIGNALS
        )
    )


@pytest.mark.parametrize(
    ("wanted", "skipped", "kept", "expected"),
    [
        (0, 0, 0, None),
        (1, 0, 0, SenderSignal.WANTED),
        (2, 2, 0, SenderSignal.WANTED),  # As many wanted as left out: the benefit of the doubt.
        (1, 2, 0, None),
        (0, SKIPS_TO_LEARN - 1, 0, None),
        (0, SKIPS_TO_LEARN, 0, SenderSignal.SKIPPED),
        (0, 9, 0, SenderSignal.SKIPPED),
        (0, 9, 1, None),  # Some of their mail made a brief: not one the owner skips.
        (0, 0, 5, None),  # Kept alone says nothing new: the review already suggests it.
    ],
)
def test_a_senders_history_says_wanted_skipped_or_nothing(
    wanted: int, skipped: int, kept: int, expected: SenderSignal | None
) -> None:
    assert sender_signal(SenderHistory(wanted=wanted, skipped=skipped, kept=kept)) is expected


def test_sender_signals_keep_only_senders_with_something_to_say() -> None:
    assert sender_signals(
        {
            "a@example.com": SenderHistory(wanted=1),
            "b@example.com": SenderHistory(skipped=1),
            "c@example.com": SenderHistory(skipped=3),
        }
    ) == {"a@example.com": SenderSignal.WANTED, "c@example.com": SenderSignal.SKIPPED}


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        (RankReason.STARRED, "starred in Gmail"),
        (RankReason.PROMOTIONS, "in Gmail's Promotions tab"),
        (RankReason.SOCIAL, "in Gmail's Social tab"),
        (RankReason.UPDATES, "in Gmail's Updates tab"),
        (RankReason.FORUMS, "in Gmail's Forums tab"),
        (RankReason.BULK_MAIL, "mailing list or bulk mail"),
        (RankReason.IMPORTANT_SENDER, "a sender you marked important"),
        (RankReason.WANTED_SENDER, "you've wanted this sender's mail before"),
        (RankReason.SKIPPED_SENDER, "you've left this sender's mail out before"),
    ],
)
def test_every_new_reason_reads_as_words(reason: RankReason, text: str) -> None:
    assert reason_text(reason) == text
