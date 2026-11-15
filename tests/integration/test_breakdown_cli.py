"""forewit-gmail-diagnostic breakdown --dry-run and the breakdown consent lines, end to end.

The dry run reads the database only: it never calls Groq, prints no task text and writes no
generation to the history.
"""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import respx

from mailbrief.diagnostics import gmail
from mailbrief.domain.actions import ActionEdit, StepEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.services.actions import ActionService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from tests.integration.test_brief_cli import vault  # noqa: F401 - a fixture used by name
from tests.unit.providers.groq.groq_fixtures import CHAT_URL, MemoryVault

TITLE = "Plan the secret launch"
NOTES = "Budget is 2,000 for sam@example.com."


def create_task(path: Path, *, done: bool = False) -> str:
    async def create() -> str:
        await asyncio.to_thread(upgrade_database, path)
        database = Database.from_path(path)
        try:
            async with database.session() as session:
                service = ActionService(session)
                task = await service.create(
                    ActionEdit(
                        title=TITLE,
                        ownership=ActionOwnership.MINE,
                        person="Sam Private",
                        priority=None,
                        tags=("hidden-tag",),
                        effort=None,
                        estimate_minutes=90,
                        target_date=None,
                        notes=NOTES,
                    ),
                    [StepEdit(step_id=None, text="Book the room", done=False)],
                )
                if done:
                    await service.complete(task.public_id, task.revision)
                return task.public_id
        finally:
            await database.dispose()

    return asyncio.run(create())


def history(path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT * FROM task_breakdowns").fetchall()


@pytest.fixture
def groq(
    respx_mock: respx.MockRouter,
    vault: MemoryVault,  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
) -> respx.Route:
    monkeypatch.setenv("MAILBRIEF_GROQ_MODEL", "test-model")
    return respx_mock.post(CHAT_URL)


def dry_run(path: Path, public_id: str, *options: str) -> int:
    return gmail.main(["breakdown", public_id, "--dry-run", "--database", str(path), *options])


def test_the_dry_run_prints_sizes_only_and_sends_nothing(
    tmp_path: Path, groq: respx.Route, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "breakdown.sqlite3"
    public_id = create_task(path)

    assert dry_run(path, public_id) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing is sent to Groq or saved." in out
    assert "Provider and model: Groq / test-model" in out
    assert "- Always: the task's title, today's date and your instruction: 32 characters" in out
    assert "notes" not in out and "Total: 32 characters" in out
    assert "First use would ask for consent." in out

    assert dry_run(path, public_id, "--notes", "--steps", "--estimate") == 0
    out = capsys.readouterr().out
    assert f"- The task's notes: {len(NOTES)} characters" in out
    assert "- Its existing steps, with their done marks: 13 characters" in out
    assert "- Your estimate: 2 characters" in out
    for private in (TITLE, NOTES, "Book the room", "Sam Private", "hidden-tag", public_id):
        assert private not in out
    assert groq.call_count == 0
    assert history(path) == []


def test_a_task_that_cannot_be_broken_down_says_why(
    tmp_path: Path, groq: respx.Route, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "breakdown.sqlite3"
    done = create_task(path, done=True)

    assert dry_run(path, done) == 3
    assert capsys.readouterr().out.strip() == "Reopen the task to break it down."
    assert dry_run(path, "99999999-9999-4999-8999-999999999999") == 3
    assert capsys.readouterr().out.strip() == "That task was not found."
    assert dry_run(path, done, "--project") == 3  # A part the task doesn't have.
    capsys.readouterr()
    assert groq.call_count == 0 and history(path) == []


def test_the_command_needs_dry_run_and_a_model(
    tmp_path: Path,
    groq: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "breakdown.sqlite3"
    public_id = create_task(path)
    with pytest.raises(SystemExit) as stopped:
        gmail.main(["breakdown", public_id, "--database", str(path)])
    assert stopped.value.code == 2
    capsys.readouterr()

    monkeypatch.delenv("MAILBRIEF_GROQ_MODEL")
    assert dry_run(path, public_id) == 3
    assert "Set MAILBRIEF_GROQ_MODEL" in capsys.readouterr().out
    assert groq.call_count == 0


def test_consent_status_lists_each_of_the_owner_s_uses(
    tmp_path: Path, groq: respx.Route, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "breakdown.sqlite3"
    create_task(path)

    assert gmail.main(["ai-consent", "status", "--database", str(path)]) == 0
    out = capsys.readouterr().out
    for use in ("drafting", "calendar planning", "breakdown"):
        assert f"AI {use}: Groq consent not granted" in out
    assert gmail.main(["ai-consent", "revoke", "--database", str(path)]) == 0
    assert (
        "Groq consent revoked: 0 (briefs 0, drafting, planning and breakdown 0)"
        in capsys.readouterr().out
    )
