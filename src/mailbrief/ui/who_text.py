"""Who an action is with (B08), in the one phrase every view uses: the detail's state line and
Owner fact, the Actions rows, Your day and the Calendar's markers. Plain text with no Qt, so
the Calendar's arithmetic (calendar_layout) can use it too."""

from typing import Final

UNNAMED_WAITING: Final = "Waiting for someone"


def who_text(waiting: bool, person: str | None, *, unnamed: str | None = None) -> str | None:
    """ "Waiting on Alex", or "For Sam" for one of the owner's own; ``unnamed`` for a waiting
    one that names no one (UNNAMED_WAITING, say); else None."""
    if person is None:
        return unnamed if waiting else None
    return f"Waiting on {person}" if waiting else f"For {person}"


def in_sentence(text: str) -> str:
    """A phrase as it reads after a line's start: "For Sam" is "for Sam"."""
    return text[:1].lower() + text[1:]
