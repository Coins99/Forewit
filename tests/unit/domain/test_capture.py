"""Capture preserves owner text without an account, inferred dates or required organization."""

import pytest
from pydantic import ValidationError

from mailbrief.domain.capture import CaptureRequest

COMMAND = "12345678-1234-4234-8234-123456789abc"


def test_title_only_task_is_valid() -> None:
    request = CaptureRequest(command_id=COMMAND, kind="task", title="Call the dentist")
    edit = request.task_edit()
    assert edit.title == request.title
    assert edit.project_public_id is None and edit.target_date is None
    assert edit.notes == "" and edit.estimate_minutes is None


def test_note_preserves_writing_exactly() -> None:
    request = CaptureRequest(command_id=COMMAND.upper(), kind="note", body="  Owner text\n  ")
    assert request.command_id == COMMAND
    assert request.note_edit().body == "  Owner text\n  "
    assert "Owner text" not in repr(request)


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"body": "Only notes, no task title"},
        {"title": "Task", "body": "x" * 10_001},
        {"kind": "note", "body": "\n"},
        {"kind": "note", "title": "x" * 201},
        {"kind": "note", "body": "x" * 20_001},
        {"command_id": "invalid", "title": "Task"},
    ],
)
def test_invalid_content_is_refused(changes: dict[str, str]) -> None:
    values = {"command_id": COMMAND, "kind": "task", **changes}
    with pytest.raises(ValidationError):
        CaptureRequest.model_validate(values)
