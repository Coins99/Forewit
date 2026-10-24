"""forewit-gmail-diagnostic actions list says when the completed list is capped, who set each
deadline, and whom each task waits on or is for."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from mailbrief.diagnostics import gmail
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.tables import ActionTable

DONE = datetime(2026, 9, 28, 12, tzinfo=UTC)


def seed_completed(path: Path, count: int) -> None:
    upgrade_database(path)

    async def add() -> None:
        database = Database.from_path(path)
        try:
            async with database.session() as session:
                for index in range(count):
                    session.add(
                        ActionTable(
                            public_id=f"00000000-0000-4000-8000-{index:012d}",
                            title=f"Action {index}",
                            ownership="mine",
                            status="completed",
                            deadline_precision="none",
                            notes="",
                            created_at_utc=DONE,
                            updated_at_utc=DONE,
                            completed_at_utc=DONE + timedelta(minutes=index),
                            revision=1,
                        )
                    )
                await session.commit()
        finally:
            await database.dispose()

    asyncio.run(add())


@pytest.mark.parametrize(("count", "footer"), [(200, False), (203, True)])
def test_the_completed_list_says_when_more_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], count: int, footer: bool
) -> None:
    path = tmp_path / "actions.sqlite3"
    seed_completed(path, count)

    code = gmail.main(
        ["actions", "list", "--view", "completed", "--timezone", "UTC", "--database", str(path)]
    )

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"00000000-0000-4000-8000-{count - 1:012d} [completed]")
    if footer:
        assert len(lines) == 201
        assert lines[-1] == "Showing the newest 200 of 203 completed actions."
    else:
        assert len(lines) == 200


def test_the_list_marks_a_deadline_the_owner_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "actions.sqlite3"
    upgrade_database(path)

    async def add() -> None:
        database = Database.from_path(path)
        try:
            async with database.session() as session:
                for index, origin in enumerate(("email", "owner")):
                    session.add(
                        ActionTable(
                            public_id=f"00000000-0000-4000-8000-{index:012d}",
                            title=f"Action {index}",
                            ownership="mine",
                            status="open",
                            deadline_text="by Friday" if origin == "email" else None,
                            deadline_precision="date",
                            deadline_date=date(2026, 10, 2),
                            deadline_timezone="UTC",
                            deadline_origin=origin,
                            notes="",
                            created_at_utc=DONE + timedelta(minutes=index),
                            updated_at_utc=DONE,
                            revision=1,
                        )
                    )
                await session.commit()
        finally:
            await database.dispose()

    asyncio.run(add())

    code = gmail.main(["actions", "list", "--timezone", "UTC", "--database", str(path)])

    assert code == 0
    email, owner = capsys.readouterr().out.splitlines()
    assert "; deadline 2026-10-02; steps 0/0" in email
    assert "; deadline 2026-10-02 (set by you); steps 0/0" in owner


def test_the_list_names_whom_a_task_waits_on_or_is_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B08: the person, and since when a waiting task waits, on the owner's day."""
    path = tmp_path / "actions.sqlite3"
    upgrade_database(path)

    async def add() -> None:
        database = Database.from_path(path)
        try:
            async with database.session() as session:
                for index, (ownership, person) in enumerate(
                    (("waiting_for", "Alex Kim"), ("waiting_for", None), ("mine", "Sam"))
                ):
                    session.add(
                        ActionTable(
                            public_id=f"00000000-0000-4000-8000-{index:012d}",
                            title=f"Action {index}",
                            ownership=ownership,
                            person=person,
                            person_key=None if person is None else person.casefold(),
                            # 01:00 UTC on the 29th is still the 28th in Toronto.
                            waiting_since_utc=(
                                datetime(2026, 9, 29, 1, tzinfo=UTC)
                                if ownership == "waiting_for"
                                else None
                            ),
                            status="open",
                            deadline_precision="none",
                            notes="",
                            created_at_utc=DONE + timedelta(minutes=index),
                            updated_at_utc=DONE,
                            revision=1,
                        )
                    )
                await session.commit()
        finally:
            await database.dispose()

    asyncio.run(add())
    database = ["--database", str(path)]

    code = gmail.main(
        ["actions", "list", "--view", "waiting", "--timezone", "America/Toronto", *database]
    )

    assert code == 0
    named, unnamed = capsys.readouterr().out.splitlines()
    assert " Action 0 (waiting for Alex Kim); " in named
    assert named.endswith("; steps 0/0; waiting since 2026-09-28; carried over")
    assert " Action 1 (waiting for); " in unnamed
    assert gmail.main(["actions", "list", "--timezone", "UTC", *database]) == 0
    (mine,) = capsys.readouterr().out.splitlines()
    assert " Action 2 (mine, for Sam); " in mine and "waiting since" not in mine
