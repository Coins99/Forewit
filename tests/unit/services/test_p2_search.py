"""Projects and saved briefs use canonical identity and bounded account-scoped search."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.projects import ProjectEdit
from mailbrief.domain.search import SearchKind
from mailbrief.services.projects import ProjectService
from mailbrief.services.search import SearchService
from mailbrief.storage.repositories import (
    AccountRepository,
    AnalysisRepository,
    DigestRepository,
    MessageRepository,
)
from tests.factories import make_analysis
from tests.unit.services.test_search import OWNER, add_mail


async def test_project_search_across_fields_and_archival(session: AsyncSession) -> None:
    projects = ProjectService(session)
    project = await projects.create_project(ProjectEdit(name="Budget 50%", notes="Q4 planning"))
    await projects.archive_project(project.public_id, project.revision, True)
    found = await SearchService(session).search("50% planning")
    assert len(found.hits) == 1
    assert found.hits[0].public_id == project.public_id
    assert found.hits[0].kind is SearchKind.PROJECT
    assert "Archived" in found.hits[0].detail
    assert not (await SearchService(session).search("50_ planning")).hits


async def test_brief_terms_can_match_different_items_without_duplicate_hits(
    session: AsyncSession,
) -> None:
    await add_mail(
        session, OWNER, ("m1", "First", True), ("m2", "Second", True), ("m3", "Orphan", True)
    )
    account = await AccountRepository(session).get_by_email(OWNER)
    assert account is not None
    items = []
    for index, summary in enumerate(("Budget alpha", "Renewal beta", "Detached analysis only"), 1):
        message = await MessageRepository(session).get_by_provider_message_id(
            account.id, f"m{index}"
        )
        assert message is not None
        analysis = await AnalysisRepository(session).upsert_analysis(
            message.id, str(index), "fake", "fake", "1", "7", make_analysis(summary=summary)
        )
        items.append((message.id, analysis.id, index - 1, "highlights"))
    await DigestRepository(session).save_digest(
        account.id, date(2026, 11, 2), "UTC", "complete", items[:2]
    )
    await session.commit()
    service = SearchService(session)
    found = [
        hit
        for hit in (await service.search("alpha beta", account_email=OWNER)).hits
        if hit.kind is SearchKind.BRIEF
    ]
    assert len(found) == 1 and found[0].day == date(2026, 11, 2)
    assert found[0].account_email == OWNER and len(found[0].detail) <= 240
    assert not (await service.search("alpha beta")).hits
    assert not (await service.search("alpha beta", account_email="other@example.com")).hits
    assert not [
        hit
        for hit in (await service.search("Detached", account_email=OWNER)).hits
        if hit.kind is SearchKind.BRIEF
    ]
