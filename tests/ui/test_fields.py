"""Shared field rules: a choice saved when made changes only in its popup, and the project
lists both editors offer."""

from datetime import UTC, datetime

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox
from pytestqt.qtbot import QtBot

from mailbrief.domain.projects import Project
from mailbrief.ui.fields import PopupChoice, fill_project_choice

HOME = "11111111-1111-4111-8111-111111111111"
OLD = "22222222-2222-4222-8222-222222222222"
GONE = "33333333-3333-4333-8333-333333333333"


def project(public_id: str, name: str, *, archived: bool = False) -> Project:
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    return Project(
        public_id=public_id,
        name=name,
        revision=1,
        created_at_utc=moment,
        updated_at_utc=moment,
        archived_at_utc=moment if archived else None,
    )


def choice(qtbot: QtBot) -> tuple[PopupChoice, list[int]]:
    field = PopupChoice()
    qtbot.addWidget(field)
    for name in ("First", "Second", "Third"):
        field.addItem(name)
    activated: list[int] = []
    field.activated.connect(activated.append)
    field.show()
    field.setFocus()
    return field, activated


def wheel(field: QComboBox) -> QWheelEvent:
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(field.mapToGlobal(QPoint(5, 5))),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(field, event)
    return event


def test_the_wheel_scrolls_past_a_popup_choice(qtbot: QtBot) -> None:
    field, activated = choice(qtbot)
    event = wheel(field)
    assert not event.isAccepted()  # Left for the scroll area beneath.
    assert field.currentIndex() == 0 and activated == []


def test_stepping_keys_and_letters_open_the_popup_instead(qtbot: QtBot) -> None:
    field, activated = choice(qtbot)
    for key in (Qt.Key.Key_Down, Qt.Key.Key_End, Qt.Key.Key_T):
        QTest.keyClick(field, key)
        assert field.view().isVisible()
        field.hidePopup()
    assert field.currentIndex() == 0 and activated == []


def test_a_plain_combo_box_would_have_changed(qtbot: QtBot) -> None:
    """What PopupChoice prevents: a closed QComboBox chooses on the arrow keys."""
    field = QComboBox()
    qtbot.addWidget(field)
    field.addItems(["First", "Second"])
    activated: list[int] = []
    field.activated.connect(activated.append)
    field.show()
    QTest.keyClick(field, Qt.Key.Key_Down)
    assert activated == [1]


def test_fill_project_choice_offers_live_projects_and_the_records_own(qtbot: QtBot) -> None:
    field = QComboBox()
    qtbot.addWidget(field)
    projects = (project(HOME, "Home"), project(OLD, "Old", archived=True))

    def names() -> list[str]:
        return [field.itemText(index) for index in range(field.count())]

    fill_project_choice(field, projects, None, none_label="No project")
    assert names() == ["No project", "Home"] and field.currentData() is None
    fill_project_choice(field, projects, OLD, none_label="No project")
    assert names() == ["No project", "Home", "Old (archived)"] and field.currentData() == OLD
    fill_project_choice(field, projects, GONE, none_label="Unassigned", fallback_name="Its own")
    assert names() == ["Unassigned", "Home", "Its own"] and field.currentData() == GONE
    fill_project_choice(field, projects, GONE, none_label="Unassigned")
    assert names() == ["Unassigned", "Home"] and field.currentIndex() == 0
