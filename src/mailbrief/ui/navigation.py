"""Compatibility adapters into canonical routes; no widgets, callbacks or I/O."""

from zoneinfo import ZoneInfo

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.routes import (
    BriefRoute,
    CalendarRoute,
    MailRoute,
    MessageRoute,
    PageRoute,
    ProjectRoute,
    Route,
    TaskRoute,
    WritingRoute,
)
from mailbrief.domain.search import SearchHit, SearchKind


def legacy_page_route(key: str) -> Route | None:
    """Preserve old sidebar IDs and command aliases while pages migrate incrementally."""
    match key:
        case "today":
            return PageRoute(page="today")
        case "actions" | "tasks":
            return TaskRoute()
        case "waiting":
            return TaskRoute(view=ActionFilter.WAITING)
        case "calendar":
            return PageRoute(page="calendar")
        case "notes":
            return PageRoute(page="notes")
        case "projects":
            return PageRoute(page="projects")
        case "inbox" | "mail":
            return MailRoute()
        case "briefs":
            return MailRoute(view="history")
        case "drafts":
            return MailRoute(view="drafts")
        case _:
            return None


def search_route(hit: SearchHit, zone: ZoneInfo, account_email: str | None) -> Route | None:
    """Bind account-sensitive results to the context in which they were searched."""
    if hit.kind is SearchKind.PROJECT and hit.public_id:
        return ProjectRoute(public_id=hit.public_id)
    if (
        hit.kind is SearchKind.BRIEF
        and hit.day
        and hit.account_email == account_email
        and account_email
    ):
        return BriefRoute(account_email=account_email, day=hit.day)
    if hit.kind is SearchKind.ACTION and hit.public_id is not None:
        return TaskRoute(
            public_id=hit.public_id,
            view=(
                ActionFilter.COMPLETED
                if not hit.open
                else ActionFilter.WAITING
                if hit.waiting
                else ActionFilter.OPEN
            ),
        )
    if hit.kind is SearchKind.WRITING and hit.public_id is not None:
        return WritingRoute(public_id=hit.public_id, draft_kind=hit.draft_kind)
    if hit.kind in (SearchKind.EVENT, SearchKind.GOOGLE_EVENT):
        day = hit.when.astimezone(zone).date() if hit.when is not None else hit.day
        if day is None:
            return None
        if hit.kind is SearchKind.EVENT and hit.public_id is not None:
            return CalendarRoute(day=day, entry=("event", hit.public_id))
        if hit.calendar_id and hit.event_id:
            return CalendarRoute(day=day, entry=("google", hit.calendar_id, hit.event_id))
    if hit.kind is SearchKind.MAIL and hit.message_id is not None and account_email is not None:
        return MessageRoute(
            account_email=account_email,
            message_id=hit.message_id,
            web_link=hit.web_link,
            in_inbox=hit.open,
        )
    return None
