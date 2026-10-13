"""Metadata normalization with no MIME body access."""

import pytest

from mailbrief.domain.messages import InboxCategory, MessageImportance
from mailbrief.ports.errors import ProviderResponseError
from mailbrief.providers.gmail.mapper import map_metadata
from tests.unit.providers.gmail.metadata_fixtures import ACCOUNT, NOW, metadata


def test_normalized_metadata_and_account_scoped_link() -> None:
    item = map_metadata(metadata(), ACCOUNT)
    assert item.received_at_utc == NOW
    assert item.importance is MessageImportance.HIGH
    assert not item.is_read
    assert item.is_in_inbox
    assert not item.has_attachments
    assert item.body_preview == "Please approve & reply."
    assert "authuser=me%40example.com" in str(item.web_link)
    assert str(item.web_link).endswith("#all/thread_a1")


def test_omitted_empty_labels_are_not_current_inbox() -> None:
    raw = metadata()
    del raw["labelIds"]
    assert not map_metadata(raw, ACCOUNT).is_in_inbox


def test_malformed_optional_headers_and_body_ignored() -> None:
    raw = metadata()
    raw["payload"] = {
        "headers": [
            {"name": "SUBJECT", "value": "=?utf-8?b?SGVsbG8=?=\n\x1b"},
            {"name": "From", "value": "invalid"},
            {"name": "To", "value": "bad, Good <good@example.com>"},
            {"name": None, "value": []},
        ],
        "body": {"data": "FULL_BODY_MUST_NOT_BE_USED"},
    }
    raw["snippet"] = "x" * 5000
    item = map_metadata(raw, ACCOUNT)
    assert item.subject == "Hello"
    assert item.sender.address == "unknown@invalid"
    assert item.to_recipients[0].address == "good@example.com"
    assert len(item.body_preview) == 2048
    assert "FULL_BODY" not in item.model_dump_json()


@pytest.mark.parametrize(
    "key,value",
    [
        ("internalDate", "not-time"),
        ("internalDate", "9" * 100),
        ("id", "../other"),
        ("threadId", None),
        ("labelIds", "INBOX"),
        ("payload", []),
        ("snippet", 3),
    ],
)
def test_invalid_required_metadata_is_safe(key: str, value: object) -> None:
    raw = metadata()
    raw[key] = value
    with pytest.raises(ProviderResponseError) as error:
        map_metadata(raw, ACCOUNT)
    assert "sender@example.com" not in str(error.value)


def with_labels(*labels: str) -> dict[str, object]:
    raw = metadata()
    raw["labelIds"] = ["INBOX", *labels]
    return raw


def with_headers(*headers: tuple[str, str]) -> dict[str, object]:
    raw = metadata()
    payload = raw["payload"]
    assert isinstance(payload, dict)
    payload["headers"] = [
        *payload["headers"],
        *({"name": name, "value": value} for name, value in headers),
    ]
    return raw


@pytest.mark.parametrize(
    ("label", "category"),
    [
        ("CATEGORY_PERSONAL", InboxCategory.PERSONAL),
        ("CATEGORY_SOCIAL", InboxCategory.SOCIAL),
        ("CATEGORY_PROMOTIONS", InboxCategory.PROMOTIONS),
        ("CATEGORY_UPDATES", InboxCategory.UPDATES),
        ("CATEGORY_FORUMS", InboxCategory.FORUMS),
    ],
)
def test_gmails_category_label_becomes_the_category(label: str, category: InboxCategory) -> None:
    assert map_metadata(with_labels(label), ACCOUNT).category is category


def test_no_category_label_is_an_unknown_category_and_unstarred() -> None:
    item = map_metadata(metadata(), ACCOUNT)
    assert item.category is None
    assert not item.is_starred
    assert not item.is_bulk


def test_the_star_label_marks_a_starred_message() -> None:
    assert map_metadata(with_labels("STARRED"), ACCOUNT).is_starred


@pytest.mark.parametrize(
    "header",
    [
        ("List-Id", "Team news <news.example.com>"),
        ("List-Unsubscribe", "<https://example.com/unsubscribe?token=SECRET-TOKEN>"),
        ("LIST-UNSUBSCRIBE", "<mailto:leave@example.com>"),
        ("Precedence", "bulk"),
        ("Precedence", " List "),
        ("Precedence", "junk"),
    ],
)
def test_list_and_bulk_headers_mark_bulk_mail(header: tuple[str, str]) -> None:
    item = map_metadata(with_headers(header), ACCOUNT)
    assert item.is_bulk
    # Only whether the header is there is kept, never its value.
    assert "SECRET-TOKEN" not in item.model_dump_json()
    assert "unsubscribe" not in item.model_dump_json().casefold()


@pytest.mark.parametrize(
    "header", [("Precedence", "first-class"), ("List-Id", "  "), ("X-List", "news")]
)
def test_other_headers_are_not_bulk_mail(header: tuple[str, str]) -> None:
    assert not map_metadata(with_headers(header), ACCOUNT).is_bulk
