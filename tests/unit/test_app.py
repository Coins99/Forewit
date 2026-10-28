"""Smoke tests for the Qt application shell."""

import os
import sys
from typing import cast

import pytest
from PySide6.QtCore import QEvent, QLocale
from PySide6.QtWidgets import QApplication, QDateEdit
from pytestqt.qtbot import QtBot

from mailbrief.app import MailBriefApplication, create_application, use_english_locale

pytestmark = pytest.mark.skipif(
    (
        sys.platform == "darwin"
        and not os.environ.get("DISPLAY")
        and not os.environ.get("MACOS_GUI_AVAILABLE")
    ),
    reason="Requires active macOS GUI WindowServer session",
)


def test_create_application_reuses_qapplication(qapp: QApplication) -> None:
    assert create_application([]) is qapp
    assert qapp.applicationName() == "MailBrief"


def calendar_language(qtbot: QtBot) -> QLocale.Language:
    """The language of a new date field's calendar pop-up."""
    edit = QDateEdit()
    qtbot.addWidget(edit)
    edit.setCalendarPopup(True)  # Without it there is no calendar.
    calendar = edit.calendarWidget()
    assert calendar is not None
    return calendar.locale().language()


def test_calendar_pop_ups_read_in_english(qtbot: QtBot) -> None:
    previous = QLocale()
    try:
        # A French default first, so the check holds on an English machine too.
        QLocale.setDefault(QLocale(QLocale.Language.French, QLocale.Country.France))
        assert calendar_language(qtbot) == QLocale.Language.French

        use_english_locale()

        assert calendar_language(qtbot) == QLocale.Language.English
        assert QLocale().monthName(1) == "January"
    finally:
        QLocale.setDefault(previous)


def test_a_quit_goes_to_the_main_window(qapp: QApplication) -> None:
    """Cmd+Q and the Dock's Quit arrive as a Quit event. It goes to the main window, whose
    close asks before losing input and closes each dialog itself, and is ignored, so Qt
    doesn't close windows in its own order (on macOS, the quit is cancelled)."""
    closed: list[bool] = []
    application = cast(MailBriefApplication, qapp)  # The test's own QApplication.
    application.quit_request = lambda: closed.append(True)  # Its __init__ never ran on it.
    try:
        event = QEvent(QEvent.Type.Quit)
        assert MailBriefApplication.event(application, event)
        assert not event.isAccepted() and closed == [True]
    finally:
        del application.quit_request
