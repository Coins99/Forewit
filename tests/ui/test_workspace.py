"""The three-pane workspace renders in both themes at the mockup and desktop sizes.

With MAILBRIEF_UI_SHOTS set to a folder, each render is saved there as
``{size}-{mode}-{dpr}x.png`` for the screenshot review loop (.claude/skills/mailbrief-ui).
"""

import gc
import os
import weakref
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QAbstractSlider, QApplication, QLabel, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.digests import DailyDigest, DigestCoverage, DigestItem, DigestStatus
from mailbrief.domain.messages import EmailContact
from mailbrief.ui.actions_view import ActionRow, ActionsPanel
from mailbrief.ui.brief_detail import DETAIL_MAX_WIDTH
from mailbrief.ui.brief_list import ELIDE_MARGIN, ROW_ROLE, elided
from mailbrief.ui.calendar_view import GOOGLE_ON, CalendarPanel
from mailbrief.ui.hairline import HairlineDivider
from mailbrief.ui.theme import ThemeMode, apply_theme, current_tokens, ui_metrics
from mailbrief.ui.workspace import (
    EMPTY_BRIEF,
    KEY_ROLE,
    PAGE_SHORTCUTS,
    SHEET_GUTTER,
    ThreePaneWorkspace,
    brief_meta,
    brief_title,
    coverage_short,
)
from tests.factories import make_digest_item
from tests.ui.window_wait import show
from tests.ui.workspace_fixtures import (
    ACCOUNT,
    ACTIONS_NOW,
    ZONE,
    mockup_actions,
    mockup_digest,
    mockup_week,
)
from tests.unit.services.test_history import TODAY as HISTORY_DAY
from tests.unit.services.test_history import summary, with_follow_ups

SIZES = {"mockup": (680, 520), "desktop": (1100, 720)}
OWNER_ZONE = ZoneInfo(ZONE)
TODAY = date(2026, 10, 6)  # The mockup brief's day.


def test_brand_glyph_follows_live_appearance_changes(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT)
    workspace = ThreePaneWorkspace()
    qtbot.addWidget(workspace)
    brand = workspace.sidebar.findChild(QLabel, "brandIcon")
    assert brand is not None
    images = []
    for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
        apply_theme(qapp, mode)
        image = brand.pixmap().toImage()
        ink = max(
            (image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())),
            key=lambda color: color.alpha(),
        )
        assert ink.alpha() > 200
        assert ink.name() == current_tokens().panel
        images.append(image)
    assert images[0] != images[1]
    assert images[0] == images[2]


def build(qtbot: QtBot) -> ThreePaneWorkspace:
    workspace = ThreePaneWorkspace()
    qtbot.addWidget(workspace)
    brief = mockup_digest()
    workspace.show_digest(
        brief.digest,
        links=brief.links,
        proposals=brief.proposals,
        owner_zone=OWNER_ZONE,
        today=TODAY,
    )
    workspace.header.set_status("Checked Gmail at 09:14")
    workspace.sidebar.set_counts(5, 2, 3)
    workspace.sidebar.set_note("Gmail connected. AI asks before sending.")
    return workspace


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("mode", list(ThemeMode))
def test_workspace_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, size: str
) -> None:
    apply_theme(qapp, mode)
    workspace = build(qtbot)
    workspace.resize(*SIZES[size])
    show(qtbot, workspace)
    image = workspace.grab()
    assert not image.isNull()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        dpr = image.devicePixelRatio()
        assert image.save(str(Path(folder) / f"{size}-{mode.value}-{dpr:g}x.png"))


def build_calendar(qtbot: QtBot) -> ThreePaneWorkspace:
    """The workspace on the Calendar page with the mockup's week (ADR 0018), inside the
    12-pixel page margins the window gives it."""
    workspace = build(qtbot)
    panel = CalendarPanel()
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.addWidget(panel)
    workspace.add_page("calendar", page)
    # The week arrives once the page is in the window, as it does in the app: rows laid
    # out before then would miss the stylesheet's item padding.
    panel.show_range(mockup_week(), OWNER_ZONE, TODAY)
    panel.set_google(GOOGLE_ON, f"Google Calendar: {ACCOUNT} · updated 09:14")
    workspace.show_page("calendar")
    workspace.sidebar.set_current("calendar")
    return workspace


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_calendar_page_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    workspace = build_calendar(qtbot)
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    image = workspace.grab()
    assert not image.isNull()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        dpr = image.devicePixelRatio()
        assert image.save(str(Path(folder) / f"page-calendar-{mode.value}-{dpr:g}x.png"))


# The Actions page's renders: the sidebar row, then the view shown.
ACTION_PAGES = {
    "page-actions": ("actions", ActionFilter.OPEN),
    "page-waiting": ("waiting", ActionFilter.WAITING),
    "page-completed": ("actions", ActionFilter.COMPLETED),
}


def build_actions(qtbot: QtBot, key: str) -> ThreePaneWorkspace:
    """The workspace on the Actions page, as the window composes it."""
    workspace = build(qtbot)
    panel = ActionsPanel()
    for view, actions in mockup_actions().items():
        panel.show_actions(view, actions, today=TODAY, zone=OWNER_ZONE, now=ACTIONS_NOW)
    workspace.add_page("actions", panel)
    sidebar, view = ACTION_PAGES[key]
    workspace.show_page("actions")
    panel.show_view(view)
    workspace.sidebar.set_current(sidebar)
    return workspace


@pytest.mark.parametrize("key", list(ACTION_PAGES))
@pytest.mark.parametrize("mode", list(ThemeMode))
def test_actions_page_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, key: str
) -> None:
    if key == "page-completed" and mode is ThemeMode.LIGHT:
        pytest.skip("one render of the Completed view is enough")
    apply_theme(qapp, mode)
    workspace = build_actions(qtbot, key)
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    image = workspace.grab()
    assert not image.isNull()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        dpr = image.devicePixelRatio()
        assert image.save(str(Path(folder) / f"{key}-{mode.value}-{dpr:g}x.png"))


@pytest.mark.parametrize("key", list(ACTION_PAGES))
def test_no_action_row_line_is_cut_off_at_the_gallery_size(
    qtbot: QtBot, qapp: QApplication, themed: None, key: str
) -> None:
    apply_theme(qapp, ThemeMode.DARK)
    workspace = build_actions(qtbot, key)
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    panel = workspace.findChild(ActionsPanel)
    assert panel is not None
    listing = panel.lists[panel.view()]
    assert listing.count() > 0
    if workspace.width() < SIZES["desktop"][0]:
        pytest.skip("the screen is too small for the gallery's window")
    # The width a row really gets, which differs a little by platform (frame, scroll bar).
    width = listing.viewport().width() - ELIDE_MARGIN
    metrics = ui_metrics(9)
    for number in range(listing.count()):
        row = listing.item(number).data(ROW_ROLE)
        assert isinstance(row, ActionRow)
        assert elided(row.meta, metrics, width) == row.meta, row.meta


def test_sidebar_requests_pages_and_settings(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    sidebar = workspace.sidebar
    pages = QSignalSpy(sidebar.page_requested)
    settings = QSignalSpy(sidebar.settings_requested)
    sidebar.set_current("drafts")
    assert pages.count() == 0  # Showing a page doesn't request it.
    sidebar.nav.setCurrentIndex(sidebar.pages.index(sidebar._row("actions"), 0))
    assert pages.at(0) == ["actions"]
    sidebar.nav.setFocus()
    QTest.keyClick(sidebar.nav, Qt.Key.Key_Return)
    assert pages.at(1) == ["actions"]
    sidebar.settings.click()
    assert settings.count() == 1
    assert sidebar.count_text("actions") == "5"
    sidebar.set_counts(None, 2, 3)
    assert sidebar.count_text("actions") is None
    assert sidebar.note.text() == "Gmail connected. AI asks before sending."


def test_selecting_a_row_updates_the_detail(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    assert workspace.detail.title is not None
    assert workspace.detail.title.text() == "Q3 budget: approval needed by Friday"
    workspace.brief_list.setCurrentIndex(workspace.brief_list.model().index(4, 0))
    assert workspace.detail.title.text() == "Invoice 2041"
    assert workspace.coverage.text() == "Inbox on Oct 6, up to 09:14, plus 1 tracked reply"
    assert workspace.coverage.accessibleName().startswith("Covers messages received on 2026-10-06")
    assert workspace.header.status.text() == "Checked Gmail at 09:14"


def test_empty_brief_says_so(qtbot: QtBot) -> None:
    workspace = ThreePaneWorkspace()
    qtbot.addWidget(workspace)
    empty = DailyDigest.model_validate(
        {**mockup_digest().digest.model_dump(), "items": (), "status": DigestStatus.EMPTY}
    )
    workspace.show_digest(empty, owner_zone=OWNER_ZONE, today=TODAY)
    assert workspace.brief_list.model().rowCount() == 0
    assert workspace.detail.title is None
    texts = [label.text() for label in workspace.detail.findChildren(QLabel)]
    assert EMPTY_BRIEF in texts


def test_sidebar_rows_are_read_with_their_counts(qtbot: QtBot) -> None:
    workspace = build(qtbot)  # Counts 5, 2 and 3.
    sidebar = workspace.sidebar

    def read(key: str) -> str:
        row = next(
            number
            for number in range(sidebar.pages.rowCount())
            if sidebar.pages.item(number).data(KEY_ROLE) == key
        )
        return str(sidebar.pages.item(row).data(Qt.ItemDataRole.AccessibleTextRole))

    assert [read(key) for key in ("today", "actions", "waiting", "drafts", "briefs")] == [
        "Today",
        "Actions, 5",
        "Waiting, 2",
        "Drafts, 3",
        "Briefs",
    ]
    sidebar.set_counts(None, 0, None)  # No count: the label alone.
    assert [read(key) for key in ("actions", "waiting", "drafts")] == [
        "Actions",
        "Waiting, 0",
        "Drafts",
    ]


def test_settings_lines_up_with_the_page_rows(qtbot: QtBot) -> None:
    from mailbrief.ui import workspace as module

    workspace = build(qtbot)
    workspace.resize(*SIZES["mockup"])
    show(qtbot, workspace)
    settings = workspace.sidebar.settings
    nav = workspace.sidebar.nav
    # Both start at the same x inside the sidebar, and paint the same offsets.
    assert (
        settings.mapTo(workspace.sidebar, settings.rect().topLeft()).x()
        == nav.mapTo(workspace.sidebar, nav.rect().topLeft()).x()
    )
    assert settings.height() == module._NAV_ROW
    assert settings.accessibleName() == "Settings"
    assert not settings.grab().isNull()


def test_pages_switch_and_refuse_unknown_keys(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    assert workspace.current_page() == "today"
    page = QWidget()
    workspace.add_page("drafts", page)
    workspace.show_page("drafts")
    assert workspace.current_page() == "drafts"
    assert workspace.pages.currentWidget() is page
    with pytest.raises(KeyError):
        workspace.show_page("nowhere")
    workspace.show_page("today")
    today = workspace.pages.currentWidget()
    assert today is not None and today.objectName() == "todayPage"


def test_a_long_status_never_widens_the_header(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    header = workspace.header
    header.set_status("")
    empty = header.minimumSizeHint().width()
    assert header.refresh_icon.isHidden()
    header.set_status("Checked Gmail at 09:14 " * 14)
    assert len(header.status.text()) > 300
    assert header.minimumSizeHint().width() == empty
    assert not header.refresh_icon.isHidden()
    added = QLabel("added")
    header.add_widget(added)
    assert added.parent() is header


def test_the_header_alert_shows_once_and_never_widens_it(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    header = workspace.header
    empty = header.minimumSizeHint().width()
    assert header.alert.isHidden()
    whole = "Inbox sync incomplete. Cached messages may be stale. " * 8
    header.set_alert("Inbox sync incomplete", whole)
    assert not header.alert.isHidden()
    assert header.alert.text.text() == "Inbox sync incomplete"
    assert header.alert.text.property("tone") == "warning"  # Bare coloured text.
    assert header.alert.toolTip() == header.alert.accessibleDescription() == whole
    assert header.alert.accessibleName() == "Inbox sync incomplete"
    # Only its icon holds room; the words elide rather than widen the window.
    layout = header.layout()
    assert layout is not None
    alert_layout = header.alert.layout()
    assert alert_layout is not None
    least = header.alert.minimumSizeHint().width()
    assert least <= header.alert.icon.sizeHint().width() + alert_layout.spacing()
    assert header.minimumSizeHint().width() <= empty + least + layout.spacing()
    order = [item.widget() for index in range(layout.count()) if (item := layout.itemAt(index))]
    assert header.title_line.parentWidget() is header
    assert order.index(header.alert) < layout.count() - 1
    header.set_alert("")
    assert header.alert.isHidden() and not header.alert.toolTip()


@pytest.mark.parametrize("month", ["October", "September"])
@pytest.mark.parametrize("width", [680, 1100])
@pytest.mark.parametrize("mode", list(ThemeMode))
def test_calendar_header_keeps_the_month_and_year_on_one_line(
    qtbot: QtBot, qapp: QApplication, themed: None, month: str, width: int, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    workspace = build_calendar(qtbot)
    title = workspace.header.title
    title.setText(f"Calendar · {month} 2026")
    workspace.header.set_status("Checked Gmail at 09:14 " * 20)
    workspace.resize(width, 520)
    show(qtbot, workspace)

    assert not title.wordWrap()
    assert title.width() >= title.fontMetrics().horizontalAdvance(title.text())
    assert title.height() >= title.fontMetrics().height()
    origin = title.mapTo(workspace.header, title.rect().topLeft())
    assert workspace.header.rect().contains(origin)
    assert origin.y() + title.height() <= workspace.header.height()
    assert title.accessibleName() == title.text()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        image = workspace.grab()
        dpr = image.devicePixelRatio()
        assert image.save(
            str(Path(folder) / f"calendar-heading-{month}-{width}-{mode.value}-{dpr:g}x.png")
        )


def test_header_elides_instead_of_widening_for_an_oversized_title(qtbot: QtBot) -> None:
    workspace = build_calendar(qtbot)
    workspace.resize(680, 520)
    show(qtbot, workspace)
    original_width = workspace.width()
    title = workspace.header.title
    title.setText("Calendar · September 2026 " * 20)
    QApplication.processEvents()

    assert workspace.width() == original_width
    assert title.width() < title.fontMetrics().horizontalAdvance(title.text())
    assert title.height() >= title.fontMetrics().height()
    assert title.accessibleName() == title.text()


def test_sidebar_footer_order_and_mnemonics(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    sidebar = workspace.sidebar
    layout = sidebar.layout()
    assert layout is not None
    items = [layout.itemAt(index) for index in range(layout.count())]
    widgets = [None if item is None else item.widget() for item in items]
    order = [
        sidebar.brand,
        sidebar.nav,
        None,  # The stretch.
        widgets[3],  # The secondary navigation divider.
        sidebar.saved_mail,
        sidebar.data,
        sidebar.settings,
        sidebar.note,
        sidebar.account_button,
    ]
    assert widgets == order
    last = items[-1]
    assert last is not None and last.widget() is sidebar.account_button
    assert isinstance(widgets[3], HairlineDivider)
    assert sidebar.account_menu.layout() is sidebar.footer
    assert sidebar.saved_mail.text() == "Saved &mail"
    assert sidebar.saved_mail.accessibleName() == "Saved mail"
    assert sidebar.data.text() == "&Data"
    assert sidebar.data.accessibleName() == "Data and recovery"
    assert sidebar.settings.text() == "Se&ttings"
    assert sidebar.settings.accessibleName() == "Settings"
    sidebar.set_note("")
    assert sidebar.note.isHidden()


def test_arrow_keys_reach_briefs_like_any_page(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    workspace.resize(*SIZES["mockup"])
    show(qtbot, workspace)
    sidebar = workspace.sidebar
    pages = QSignalSpy(sidebar.page_requested)
    sidebar.set_current("drafts")
    sidebar.nav.setFocus()
    QTest.keyClick(sidebar.nav, Qt.Key.Key_Down)
    assert sidebar.current_key() == "briefs"
    assert pages.at(pages.count() - 1) == ["briefs"]
    QTest.keyClick(sidebar.nav, Qt.Key.Key_Up)
    assert pages.at(pages.count() - 1) == ["drafts"]


def brief_with(**updates: object) -> DailyDigest:
    return mockup_digest().digest.model_copy(update=updates)


def coverage(**counts: int | bool) -> DigestCoverage:
    values: dict[str, int | bool] = {
        "sync_complete": True,
        "analyzed": 3,
        "reused": 1,
        "failed": 0,
        "skipped": 1,
        "deferred": 0,
    }
    values.update(counts)
    values["shortlisted"] = sum(
        int(values[key]) for key in ("analyzed", "reused", "failed", "skipped", "deferred")
    )
    return DigestCoverage.model_validate(values)


def test_brief_meta_uses_the_brief_zone() -> None:
    toronto, paris = ZoneInfo("America/Toronto"), ZoneInfo("Europe/Paris")
    morning = brief_with(
        timezone_name="America/Toronto",
        local_date=date(2026, 10, 6),
        generated_at_utc=datetime(2026, 10, 6, 13, 14, tzinfo=UTC),
        coverage=None,
    )
    assert brief_meta(morning, paris, TODAY) == (
        "owner@example.com · saved 09:14 (America/Toronto)",
        "owner@example.com. Saved Tue Oct 6, 09:14 (America/Toronto).",
    )
    assert "up to 09:14" in coverage_short(morning, TODAY)  # The heading and footer agree.
    # Oct 6 23:30 in Toronto is Oct 7 05:30 in Paris: the brief's own day decides.
    late = morning.model_copy(update={"generated_at_utc": datetime(2026, 10, 7, 3, 30, tzinfo=UTC)})
    short, _full = brief_meta(late, paris, TODAY)
    assert short.startswith("owner@example.com · saved 23:30 (America/Toronto)")
    assert "Oct 7" not in short
    # Another name with the same offset at that moment shows the same time: no zone.
    for owner in (toronto, ZoneInfo("America/New_York")):
        for brief in (morning, late):
            assert "(" not in brief_meta(brief, owner, TODAY)[0]
    calcutta = morning.model_copy(update={"timezone_name": "Asia/Calcutta"})
    assert (
        "(" not in brief_meta(calcutta, ZoneInfo("Asia/Kolkata"), TODAY)[0]
    )  # One zone, two names.


def test_brief_title_names_the_day_and_an_incomplete_brief() -> None:
    assert brief_title(brief_with(), TODAY) == "Tue Oct 6"
    assert brief_title(brief_with(status=DigestStatus.PARTIAL), TODAY) == "Tue Oct 6 · Partial"
    assert brief_title(brief_with(status=DigestStatus.EMPTY), TODAY) == "Tue Oct 6 · Empty"


def test_a_brief_from_another_year_names_it() -> None:
    old = brief_with(local_date=date(2026, 12, 31))
    assert brief_title(old, date(2027, 1, 4)) == "Thu Dec 31, 2026"
    assert brief_title(old, date(2026, 12, 31)) == "Thu Dec 31"
    full = brief_meta(old, OWNER_ZONE, date(2027, 1, 4))[1]
    assert "Saved Tue Oct 6, 2026, 09:14." in full  # Same zone: not named.
    tokyo = brief_with(
        timezone_name="Asia/Tokyo",
        generated_at_utc=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        coverage=None,
    )
    # The brief's zone and the owner's differ: the time is the brief's, and named.
    assert brief_meta(tokyo, OWNER_ZONE, TODAY) == (
        "owner@example.com · saved 21:00 (Asia/Tokyo)",
        "owner@example.com. Saved Tue Oct 6, 21:00 (Asia/Tokyo).",
    )


def test_brief_meta_short_and_full() -> None:
    complete = brief_with(coverage=coverage())
    assert brief_meta(complete, OWNER_ZONE, TODAY) == (
        "owner@example.com · saved 09:14",
        "owner@example.com. Saved Tue Oct 6, 09:14. "
        "3 analyzed, 1 reused, 0 failed, 1 skipped. Inbox sync complete.",
    )
    later = brief_with(generated_at_utc=datetime(2026, 10, 7, 13, 5, tzinfo=UTC))
    assert brief_meta(later, OWNER_ZONE, TODAY)[0] == "owner@example.com · saved Oct 7 09:05"
    troubled = brief_with(coverage=coverage(failed=2, deferred=1, sync_complete=False))
    short, full = brief_meta(troubled, OWNER_ZONE, TODAY)
    assert short == (
        "owner@example.com · saved 09:14 · 2 failed · 1 deferred · Inbox sync incomplete"
    )
    assert full.endswith(
        "3 analyzed, 1 reused, 2 failed, 1 skipped, 1 deferred. Inbox sync incomplete."
    )
    unknown = brief_with(coverage=None)
    assert brief_meta(unknown, OWNER_ZONE, TODAY) == (
        "owner@example.com · saved 09:14",
        "owner@example.com. Saved Tue Oct 6, 09:14.",
    )


def test_the_heading_shows_the_brief(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    heading = workspace.heading
    assert not heading.isHidden()
    assert heading.title.text() == "Your brief"
    assert "TUE OCT 6" in heading.eyebrow.text()
    assert heading.meta.text() == "owner@example.com · saved 09:14"
    assert heading.meta.accessibleName().startswith("owner@example.com. Saved Tue Oct 6, 09:14")


def workspace_with(
    qtbot: QtBot, items: tuple[DigestItem, ...], size: tuple[int, int]
) -> ThreePaneWorkspace:
    workspace = ThreePaneWorkspace()
    qtbot.addWidget(workspace)
    digest = brief_with(items=items)
    workspace.show_digest(digest, owner_zone=OWNER_ZONE, today=TODAY)
    workspace.resize(*size)
    show(qtbot, workspace)
    return workspace


def long_brief(count: int) -> tuple[DigestItem, ...]:
    return tuple(
        make_digest_item(message_key=f"m{number}", position=number, subject=f"Email {number}")
        for number in range(count)
    )


def three_with_a_long_detail() -> tuple[DigestItem, ...]:
    """Three emails; the first one's detail is taller than the pane."""
    first, second, third = mockup_digest().digest.items[:3]
    first = first.model_copy(update={"action_text": "Read the attached notes. " * 38})
    return tuple(
        item.model_copy(update={"position": number})
        for number, item in enumerate((first, second, third))
    )


def test_page_keys_page_a_list_that_scrolls(qtbot: QtBot) -> None:
    workspace = workspace_with(qtbot, long_brief(20), (680, 420))
    view, detail = workspace.brief_list, workspace.detail.verticalScrollBar()
    assert view.verticalScrollBar().maximum() > 0  # 20 rows don't fit.
    first = view.currentIndex().row()
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert view.currentIndex().row() > first  # The list paged…
    assert detail.value() == 0  # …and the detail stayed put.


def test_page_keys_scroll_the_detail_while_the_list_fits(qtbot: QtBot) -> None:
    workspace = workspace_with(qtbot, three_with_a_long_detail(), (780, 720))
    view, bar = workspace.brief_list, workspace.detail.verticalScrollBar()
    assert view.verticalScrollBar().maximum() == 0  # Three rows fit.
    assert bar.maximum() > 0  # The first email's detail doesn't.
    selected = view.selected_key()
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert bar.value() > 0
    assert view.selected_key() == selected  # The selection stays put.
    QTest.keyClick(view, Qt.Key.Key_PageUp)
    assert bar.value() == 0


def test_page_up_from_a_detail_button_scrolls_the_pane(qtbot: QtBot) -> None:
    workspace = workspace_with(qtbot, three_with_a_long_detail(), (780, 720))
    bar = workspace.detail.verticalScrollBar()
    QTest.keyClick(workspace.brief_list, Qt.Key.Key_PageDown)
    scrolled = bar.value()
    assert scrolled > 0
    button = workspace.detail.buttons()[0]
    button.setFocus()
    # The button ignores Page Up, so it reaches the scroll area, which pages back.
    QTest.keyClick(button, Qt.Key.Key_PageUp)
    assert bar.value() < scrolled


def test_showing_the_same_brief_again_keeps_selection_and_scroll(qtbot: QtBot) -> None:
    brief = mockup_digest()
    workspace = build(qtbot)
    # At its minimum height the first email's detail scrolls.
    workspace.resize(680, workspace.minimumSizeHint().height())
    show(qtbot, workspace)
    view = workspace.brief_list
    rows = view.brief_model.rows()
    keys = [row.item.message_key for row in rows if row.item is not None]

    def select(key: str) -> None:
        number = next(
            index
            for index, row in enumerate(rows)
            if row.item is not None and row.item.message_key == key
        )
        view.setCurrentIndex(view.model().index(number, 0))

    def again(digest: DailyDigest) -> None:
        workspace.show_digest(
            digest, links=brief.links, proposals=brief.proposals, owner_zone=OWNER_ZONE, today=TODAY
        )

    select(keys[1])
    again(brief.digest)  # As after Accept or Dismiss.
    assert view.selected_key() == keys[1]
    select(keys[0])
    bar = workspace.detail.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=2000)
    bar.setValue(bar.maximum())
    kept = bar.value()
    again(brief.digest)
    assert view.selected_key() == keys[0]
    qtbot.waitUntil(lambda: bar.value() == kept, timeout=2000)

    later = brief.digest.generated_at_utc + timedelta(hours=1)
    again(brief.digest.model_copy(update={"generated_at_utc": later}))  # Another brief.
    QApplication.processEvents()
    assert view.selected_key() == keys[0]
    assert bar.value() == 0
    select(keys[1])
    again(brief.digest)
    assert view.selected_key() == keys[0]  # Another brief again: back to the first.


def test_a_restore_waits_for_the_range_and_a_hand_scroll_ends_it(qtbot: QtBot) -> None:
    """Wrapped labels can grow the detail's range over several layout passes: the restore
    keeps its position until a pass reaches it, unless the owner scrolls first."""
    workspace = build(qtbot)
    workspace.resize(680, workspace.minimumSizeHint().height())
    show(qtbot, workspace)
    bar = workspace.detail.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=2000)

    def restore(position: int) -> int | None:
        """Restore ``position`` now; what is still waiting."""
        workspace._pending_scroll = position
        workspace._restore_scroll()
        return workspace._pending_scroll

    far = bar.maximum() + 500
    assert restore(far) == far  # Still waiting for a longer range…
    assert bar.value() == bar.maximum()
    bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepSub)
    assert workspace._pending_scroll is None  # …until a scroll by hand.
    assert restore(1) is None
    assert bar.value() == 1


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_the_pages_sit_on_a_rounded_sheet(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """No pane meets the chrome at a sharp corner: the pages are masked to a rounded sheet
    with a gutter of glass at its right, and no hairline crosses the window."""
    apply_theme(qapp, mode)
    tokens = current_tokens()
    workspace = build(qtbot)
    workspace.resize(*SIZES["mockup"])
    show(qtbot, workspace)
    sheet = workspace.sheet
    edge = sheet.sheet_rect()
    assert sheet.width() - edge.width() == SHEET_GUTTER
    pages = workspace.pages
    mask = pages.mask()
    assert not mask.isEmpty()
    assert not mask.contains(QPoint(0, 0))  # Its square corner is cut away…
    assert not mask.contains(QPoint(pages.width() - 1, pages.height() - 1))
    assert mask.contains(QPoint(pages.width() // 2, 0))  # …its edges are not.
    image = sheet.grab().toImage()
    ratio = image.devicePixelRatio()

    def at(x: float, y: float) -> str:
        return image.pixelColor(round(x * ratio), round(y * ratio)).name()

    # The sheet's corner shows the glass round it, as does the gutter.
    assert at(0, 0) == tokens.panel
    assert at(sheet.width() - 2, 10) == tokens.panel


def test_clear_shows_only_the_message(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    workspace.clear("Saved brief unavailable.")
    assert workspace.brief_list.model().rowCount() == 0
    assert workspace.brief_list.selected_key() is None
    assert workspace.coverage.text() == ""
    assert workspace.heading.isHidden()
    texts = [label.text() for label in workspace.detail.findChildren(QLabel)]
    assert texts == ["Saved brief unavailable."]


def test_nav_buttons_are_muted_while_disabled(qtbot: QtBot) -> None:
    from mailbrief.ui.workspace import NavButton

    tokens = current_tokens()
    row = NavButton("Data", "database")
    qtbot.addWidget(row)
    assert row._color() == tokens.text_secondary
    row.setDown(True)
    assert row._color() == tokens.text
    row.setEnabled(False)
    assert row._color() == tokens.text_muted
    assert not row._active()  # No fill while disabled.
    assert not row.grab().isNull()


def test_unbroken_mail_text_never_widens_the_workspace(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)
    token = "y" * 150
    brief = mockup_digest()
    first = brief.digest.items[0]
    long_item = first.model_copy(
        update={
            "subject": f"Subject {token}",
            "summary": f"Summary {token}",
            "sender": EmailContact(name=None, address=f"{token}@example.com"),
        }
    )
    hostile = brief.digest.model_copy(update={"items": (long_item, *brief.digest.items[1:])})

    def narrowest(digest: DailyDigest) -> int:
        workspace = ThreePaneWorkspace()
        qtbot.addWidget(workspace)
        workspace.show_digest(
            digest, links=brief.links, proposals=brief.proposals, owner_zone=OWNER_ZONE, today=TODAY
        )
        workspace.resize(*SIZES["mockup"])
        show(qtbot, workspace)
        content = workspace.detail.widget()
        assert content is not None
        assert content.width() <= workspace.detail.viewport().width()
        return workspace.minimumSizeHint().width()

    assert narrowest(hostile) == narrowest(brief.digest)


def test_the_detail_content_is_capped_and_at_the_left(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    workspace.resize(*SIZES["mockup"])
    show(qtbot, workspace)
    content = workspace.detail.widget()
    assert content is not None
    assert content.maximumWidth() == DETAIL_MAX_WIDTH == 720
    assert content.x() == 0  # Left-aligned.


def test_the_detail_keeps_a_readable_width_on_wide_windows(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    # As wide as the screen allows: a platform that keeps windows on screen (Cocoa; CI's Mac
    # has about 1024 × 649) gives less than this, and offscreen gives all of it. The static
    # lookup, not workspace.screen(): PySide re-parents the shared QScreen wrapper under the
    # widget it was asked from, and deleting the widget then invalidates it for everyone.
    screen = QGuiApplication.primaryScreen()
    workspace.resize(screen.availableGeometry().size().expandedTo(QSize(1600, 900)))
    show(qtbot, workspace)
    if workspace.detail.viewport().width() <= DETAIL_MAX_WIDTH:
        pytest.skip("screen too small for the cap to apply")
    content = workspace.detail.widget()
    assert content is not None
    assert 0 < content.width() <= DETAIL_MAX_WIDTH
    assert content.x() == 0  # Left-aligned.


def test_the_short_coverage_names_the_day_and_when_it_was_made() -> None:
    today = summary(HISTORY_DAY, datetime(2026, 9, 29, 13, 14, tzinfo=UTC))
    later = summary(date(2026, 9, 28), datetime(2026, 9, 29, 14, 2, tzinfo=UTC))
    assert coverage_short(today, HISTORY_DAY) == "Inbox on Sep 29, up to 09:14"
    assert coverage_short(later, HISTORY_DAY) == "Inbox on Sep 28, checked Sep 29 at 10:02"


@pytest.mark.parametrize(
    ("count", "suffix"),
    [(0, ""), (1, ", plus 1 tracked reply"), (2, ", plus 2 tracked replies")],
)
def test_the_short_coverage_counts_tracked_replies(count: int, suffix: str) -> None:
    brief = with_follow_ups(count)
    assert coverage_short(brief, HISTORY_DAY) == "Inbox on Sep 29, up to 09:14" + suffix
    made = summary(HISTORY_DAY, datetime(2026, 9, 29, 13, 14, tzinfo=UTC))
    saved = made.model_copy(update={"follow_up_count": count})
    assert coverage_short(saved, HISTORY_DAY) == coverage_short(brief, HISTORY_DAY)


def test_the_short_coverage_names_another_year() -> None:
    # Dec 30, 2025 up to 09:14 in Toronto; Dec 31, 2026 checked Jan 1 at 09:02 there.
    old = summary(date(2025, 12, 30), datetime(2025, 12, 30, 14, 14, tzinfo=UTC))
    late = summary(date(2026, 12, 31), datetime(2027, 1, 1, 14, 2, tzinfo=UTC))
    assert coverage_short(old, date(2026, 1, 2)) == "Inbox on Dec 30, 2025, up to 09:14"
    assert coverage_short(late, date(2027, 1, 2)) == (
        "Inbox on Dec 31, 2026, checked Jan 1 at 09:02"
    )
    assert coverage_short(late, date(2026, 12, 31)) == (
        "Inbox on Dec 31, checked Jan 1, 2027 at 09:02"
    )


def test_the_heading_gains_its_year_after_new_year(qtbot: QtBot) -> None:
    workspace = ThreePaneWorkspace()
    qtbot.addWidget(workspace)
    digest = brief_with(
        local_date=date(2026, 12, 31),
        generated_at_utc=datetime(2026, 12, 31, 15, 0, tzinfo=UTC),
        coverage=None,
    )
    workspace.show_digest(digest, owner_zone=OWNER_ZONE, today=date(2026, 12, 31))
    assert "THU DEC 31" in workspace.heading.eyebrow.text()
    assert workspace.coverage.text() == "Inbox on Dec 31, up to 10:00, plus 1 tracked reply"
    assert workspace.heading.meta.accessibleName().startswith(
        "owner@example.com. Saved Thu Dec 31, 10:00"
    )

    workspace.set_today(date(2027, 1, 1), OWNER_ZONE)  # Midnight passes.

    assert "THU DEC 31, 2026" in workspace.heading.eyebrow.text()
    assert workspace.coverage.text() == (  # The footer too.
        "Inbox on Dec 31, 2026, up to 10:00, plus 1 tracked reply"
    )
    assert workspace.heading.meta.accessibleName().startswith(
        "owner@example.com. Saved Thu Dec 31, 2026, 10:00"
    )


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_manual_task_activity_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    from mailbrief.domain.actions import ActionActivity, ActionActivityKind, ActionStep
    from tests.factories import make_action

    apply_theme(qapp, mode)
    workspace = build(qtbot)
    panel = ActionsPanel()
    action = make_action(
        title="Plan next week",
        notes="Choose three priorities and leave room for breaks.",
        sources=(),
        target_date=TODAY,
        steps=(
            ActionStep(step_id=1, position=0, text="Review calendar", done=True),
            ActionStep(step_id=2, position=1, text="Write priorities", done=False),
        ),
        activity=tuple(
            ActionActivity(revision=index + 1, kind=kind, occurred_at_utc=ACTIONS_NOW)
            for index, kind in enumerate(
                (
                    ActionActivityKind.CREATED,
                    ActionActivityKind.EDITED,
                    ActionActivityKind.COMPLETED,
                    ActionActivityKind.REOPENED,
                )
            )
        ),
        revision=4,
    )
    panel.show_actions(ActionFilter.OPEN, (action,), today=TODAY, zone=OWNER_ZONE, now=ACTIONS_NOW)
    workspace.add_page("actions", panel)
    workspace.show_page("actions")
    workspace.sidebar.set_current("actions")
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    assert len(panel.detail.findChildren(QLabel, "actionActivityEntry")) == 4
    assert not panel.source_button.isVisible()
    image = workspace.grab()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        dpr = image.devicePixelRatio()
        assert image.save(str(Path(folder) / f"page-manual-{mode.value}-{dpr:g}x.png"))


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_project_organization_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    from mailbrief.domain.actions import ActionPriority
    from mailbrief.ui.projects_view import ProjectsDialog
    from tests.factories import make_action
    from tests.ui.test_projects import AREA, PROJECT

    apply_theme(qapp, mode)
    workspace = build(qtbot)
    panel = ActionsPanel()
    panel.show_groups((PROJECT,), (AREA,), ("Errands", "Home"))
    panel.group.setCurrentIndex(panel.group.findData("project:" + PROJECT.public_id))
    organized = make_action(
        project=PROJECT, sources=(), priority=ActionPriority.HIGH, tags=("Errands", "Home")
    )
    panel.show_actions(
        ActionFilter.OPEN,
        (organized,),
        today=TODAY,
        zone=OWNER_ZONE,
        now=ACTIONS_NOW,
    )
    workspace.add_page("actions", panel)
    workspace.show_page("actions")
    workspace.sidebar.set_current("actions")
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    images = {"page-project": workspace.grab()}
    dialog = ProjectsDialog(workspace)
    dialog.show_records((PROJECT,), (AREA,), PROJECT.public_id)
    show(qtbot, dialog)
    images["dialog-project"] = dialog.grab()
    dialog.notes.setPlainText("Draft notes to save or discard before archiving.")
    show(qtbot, dialog)
    images["dialog-project-dirty"] = dialog.grab()
    assert dialog.discard.isEnabled() and not dialog.archive.isEnabled()
    dialog.hide()
    from mailbrief.ui.action_editor import ActionEditor

    editor = ActionEditor(workspace)
    editor.set_projects((PROJECT,))
    editor.set_tags(("Errands", "Home"))
    editor.edit(organized, TODAY, OWNER_ZONE)
    show(qtbot, editor)
    images["dialog-task-project"] = editor.grab()
    assert editor.project.currentData() == PROJECT.public_id
    assert (editor.priority.currentText(), editor.tags.text()) == ("High", "Errands, Home")
    assert dialog.name.text() == PROJECT.name
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        for name, image in images.items():
            dpr = image.devicePixelRatio()
            assert image.save(str(Path(folder) / f"{name}-{mode.value}-{dpr:g}x.png"))


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_repeating_task_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """A weekly task on the Actions page, with Skip, and in the editor with its weekdays."""
    from mailbrief.domain.actions import ActionRepeat, ActionStatus
    from mailbrief.domain.recurrence import RepeatFrequency, RepeatRule
    from mailbrief.ui.action_editor import ActionEditor
    from tests.factories import make_action

    apply_theme(qapp, mode)
    workspace = build(qtbot)
    panel = ActionsPanel()
    weekly = make_action(
        title="Put the bins out",
        sources=(),
        target_date=TODAY,
        created_at_utc=ACTIONS_NOW,
        repeat=ActionRepeat(
            series_public_id="6c2e1e1f-4a5d-4b4f-9a6f-1d7c9a8b3e21",
            series_revision=2,
            rule=RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(1, 4)),
            occurrence_date=TODAY,
            previous_date=date(2026, 10, 2),
            previous_status=ActionStatus.COMPLETED,
        ),
    )
    panel.show_actions(ActionFilter.OPEN, (weekly,), today=TODAY, zone=OWNER_ZONE, now=ACTIONS_NOW)
    workspace.add_page("actions", panel)
    workspace.show_page("actions")
    workspace.sidebar.set_current("actions")
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    assert panel.skip_button.isVisible()
    images = {"page-repeating": workspace.grab()}
    editor = ActionEditor(workspace)
    editor.edit(weekly, TODAY, OWNER_ZONE)
    show(qtbot, editor)
    assert not editor.weekdays.isHidden() and not editor.later_too.isHidden()
    images["dialog-task-repeat"] = editor.grab()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        for name, image in images.items():
            dpr = image.devicePixelRatio()
            assert image.save(str(Path(folder) / f"{name}-{mode.value}-{dpr:g}x.png"))


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_owner_deadline_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """A deadline the owner set over an email's (B06): the detail line, and the editor's Due
    by fields with what the email said and Use the email's."""
    from mailbrief.domain.actions import DeadlineOrigin, StatedDeadline
    from mailbrief.domain.analysis import DeadlinePrecision, TargetReason
    from mailbrief.ui.action_editor import ActionEditor
    from tests.factories import make_action

    apply_theme(qapp, mode)
    workspace = build(qtbot)
    panel = ActionsPanel()
    moved = make_action(
        title="Send the Q3 deck",
        deadline_text="2026-10-16 17:00",
        deadline_precision=DeadlinePrecision.DATETIME,
        deadline_date=date(2026, 10, 16),
        deadline_at_utc=datetime(2026, 10, 16, 21, 0, tzinfo=UTC),
        deadline_timezone="America/Toronto",
        deadline_origin=DeadlineOrigin.OWNER,
        stated_deadline=StatedDeadline(
            deadline_text="by Friday",
            deadline_precision=DeadlinePrecision.DATE,
            deadline_date=date(2026, 10, 9),
            deadline_timezone="America/Toronto",
        ),
        suggested_target_date=date(2026, 10, 15),
        target_reason=TargetReason.WORKING_DAY_BEFORE,
        target_date=date(2026, 10, 15),
        created_at_utc=ACTIONS_NOW,
    )
    panel.show_actions(ActionFilter.OPEN, (moved,), today=TODAY, zone=OWNER_ZONE, now=ACTIONS_NOW)
    workspace.add_page("actions", panel)
    workspace.show_page("actions")
    workspace.sidebar.set_current("actions")
    workspace.resize(*SIZES["desktop"])
    show(qtbot, workspace)
    images = {"page-owner-deadline": workspace.grab()}
    editor = ActionEditor(workspace)
    editor.edit(moved, TODAY, OWNER_ZONE)
    show(qtbot, editor)
    assert editor.from_email.isVisible() and editor.deadline_time.currentText() == "17:00"
    images["dialog-task-deadline"] = editor.grab()
    folder = os.environ.get("MAILBRIEF_UI_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        for name, image in images.items():
            dpr = image.devicePixelRatio()
            assert image.save(str(Path(folder) / f"{name}-{mode.value}-{dpr:g}x.png"))


def test_pages_follow_gmail_and_outlook_order(qtbot: QtBot) -> None:
    """The daily home, the mailbox, flagged and deferred work, drafts and history, then
    Calendar set apart below a divider; Ctrl+1 to Ctrl+7 follow the same order."""
    workspace = build(qtbot)
    sidebar = workspace.sidebar
    keys = [str(sidebar.pages.item(row).data(KEY_ROLE)) for row in range(sidebar.pages.rowCount())]
    assert keys == ["today", "inbox", "actions", "waiting", "drafts", "briefs", "calendar"]
    assert list(PAGE_SHORTCUTS) == keys
    rows = [sidebar.nav.visualRect(sidebar.pages.index(row, 0)) for row in range(len(keys))]
    assert rows[-1].height() == rows[0].height()
    assert sidebar.nav.height() >= rows[-1].bottom()  # Every row fits, none scrolls.


def test_calendar_row_is_clickable_across_its_whole_height(qtbot: QtBot) -> None:
    workspace = build(qtbot)
    workspace.resize(*SIZES["mockup"])
    show(qtbot, workspace)
    sidebar = workspace.sidebar
    sidebar.set_current("today")
    pages = QSignalSpy(sidebar.page_requested)
    calendar = sidebar.nav.visualRect(sidebar.pages.index(sidebar._row("calendar"), 0))
    gap = QPoint(calendar.center().x(), calendar.top() + 4)  # On the divider.
    QTest.mouseClick(sidebar.nav.viewport(), Qt.MouseButton.LeftButton, pos=gap)
    assert sidebar.current_key() == "calendar"
    assert pages.count() == 1
    row = QPoint(calendar.center().x(), calendar.bottom() - 8)  # On the row itself.
    QTest.mouseClick(sidebar.nav.viewport(), Qt.MouseButton.LeftButton, pos=row)
    assert sidebar.current_key() == "calendar"


@pytest.mark.parametrize("calendar", [False, True], ids=["today", "calendar"])
def test_a_dropped_workspace_is_freed_without_the_cycle_collector(
    qtbot: QtBot, calendar: bool
) -> None:
    """Nothing inside the workspace refers back up to it (theme followers are handed their
    widget, the chrome holds it weakly), so the last reference frees it at once, and Qt
    deletes its widgets in order. Left to Python's cycle collector, the wrappers were
    cleared one by one, each child handed back to Python and deleted on its own while its
    parent was half gone: a collection crashed."""

    def dropped() -> weakref.ref[ThreePaneWorkspace]:
        workspace = build_calendar(qtbot) if calendar else build(qtbot)
        show(qtbot, workspace)
        from tests.ui.references import references_to

        assert not references_to(workspace), references_to(workspace)
        return weakref.ref(workspace)

    gc.collect()
    gc.disable()  # Only reference counting may free it.
    try:
        workspace = dropped()
        assert workspace() is None
    finally:
        gc.enable()
    gc.collect()  # Crashed here while the workspace was cyclic garbage.
