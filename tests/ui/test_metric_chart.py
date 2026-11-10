"""Prepared-data chart spike: accessible precision, keyboard, states and native painting."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.dashboards import ChartKind
from mailbrief.domain.metric_results import MetricDisplay, MetricSample
from mailbrief.ui.metric_chart import MetricChartCard, axis_label, label_marks, nice_ticks
from mailbrief.ui.theme import CAPTION_PX, ColorScheme, ThemeMode, apply_theme, ui_metrics


def display(chart: ChartKind = ChartKind.LINE) -> MetricDisplay:
    return MetricDisplay(
        title="Reading minutes",
        unit="minutes",
        chart=chart,
        total=Decimal("12.123456"),
        samples=(
            MetricSample(key="mon", label="Monday", value=Decimal("12.123456")),
            MetricSample(key="tue", label="Tuesday", explanation="No observation"),
            MetricSample(key="wed", label="Wednesday", value=Decimal(0)),
            MetricSample(key="thu", label="Thursday", value=Decimal(-5)),
        ),
        caption="Based on recorded observations. Missing dates are unknown.",
    )


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_keyboard_selection_enter_and_exact_table(qtbot: QtBot, key: Qt.Key) -> None:
    card = MetricChartCard(display())
    qtbot.addWidget(card)
    card.resize(500, 480)
    card.show()
    qtbot.waitExposed(card)
    card.plot.setFocus()
    QTest.keyClick(card.plot, Qt.Key.Key_Right)
    assert "Tuesday: Unknown" in card.selection_label.text()
    QTest.keyClick(card.plot, Qt.Key.Key_End)
    with qtbot.waitSignal(card.activated) as signal:
        QTest.keyClick(card.plot, key)
    assert signal.args == ["thu"]
    QTest.keyClick(card.plot, Qt.Key.Key_Home)
    assert "12.123456" in card.selection_label.text()
    card.view_data.click()
    assert card.table.isVisible()
    qtbot.waitUntil(card.table.hasFocus)
    model = card.table_model
    assert model.rowCount() == 4
    assert model.data(model.index(0, 1)) == "12.123456"
    assert model.data(model.index(1, 1)) == "Unknown"
    assert model.data(model.index(2, 1)) == "0"
    assert model.data(model.index(0, 1), Qt.ItemDataRole.AccessibleTextRole) == "12.123456"
    activated = QSignalSpy(card.activated)
    QTest.keyClick(card.table, key)
    assert activated.count() == 1
    assert activated.at(0) == ["mon"]
    QTest.keyClick(card.table, Qt.Key.Key_Down)
    QTest.keyClick(card.table, key)
    assert activated.count() == 2
    assert activated.at(1) == ["tue"]
    card.view_data.click()
    assert not card.table.isVisible()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_empty_data_table_does_not_activate(qtbot: QtBot, key: Qt.Key) -> None:
    card = MetricChartCard(MetricDisplay(title="Empty", unit="minutes", chart=ChartKind.LINE))
    qtbot.addWidget(card)
    card.show()
    card.view_data.click()
    activated = QSignalSpy(card.activated)
    QTest.keyClick(card.table, key)
    assert activated.count() == 0


def test_click_selects_bucket_and_number_card_keeps_explicit_total(qtbot: QtBot) -> None:
    card = MetricChartCard(display(ChartKind.BAR))
    qtbot.addWidget(card)
    card.show()
    area = card.plot._area()
    QTest.mouseClick(
        card.plot,
        Qt.MouseButton.LeftButton,
        pos=QPoint(int(area.right() - 1), int(area.center().y())),
    )
    assert card.plot.selection == 3
    assert card.total.text() == "12.123456 minutes"
    number = MetricChartCard(display(ChartKind.NUMBER))
    qtbot.addWidget(number)
    number.show()
    assert number.plot.isHidden()
    assert number.view_data.isEnabled()
    number.view_data.click()
    assert number.table.isVisible()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize(
    "case", ["line", "bar", "number", "empty", "missing", "constant", "dense", "long", "extreme"]
)
def test_chart_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, case: str
) -> None:
    apply_theme(qapp, mode, ColorScheme.STUDIO)
    data = display(ChartKind.BAR if case == "bar" else ChartKind.LINE)
    if case == "number":
        data = display(ChartKind.NUMBER)
    if case in {"empty", "missing"}:
        data = MetricDisplay(
            title="No observations",
            unit="minutes",
            chart=ChartKind.LINE,
            samples=() if case == "empty" else (MetricSample(key="a", label="Monday"),),
        )
    if case in {"constant", "dense", "long", "extreme"}:
        count = 366 if case == "dense" else 7
        data = MetricDisplay(
            title="Observation" if case != "long" else "LongTitle" * 12,
            unit="units",
            chart=ChartKind.LINE,
            samples=tuple(
                MetricSample(
                    key=str(i),
                    label="Category" * 20 if case == "long" else str(i),
                    value=Decimal(10**30)
                    if case == "extreme"
                    else Decimal(3 if case == "constant" else i % 10),
                )
                for i in range(count)
            ),
            daily_target=Decimal(4),
            caption="Recorded observations. Daily target shown as a dashed line.",
        )
    card = MetricChartCard(data)
    qtbot.addWidget(card)
    card.resize(420, 470)
    card.show()
    card.plot.setFocus()
    qtbot.wait(10)
    rendered = card.grab()
    assert not rendered.isNull()
    assert card.width() == 420
    assert card.table.accessibleName()
    directory = os.environ.get("MAILBRIEF_UI_SHOTS")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        assert rendered.save(str(root / f"metric-{case}-{mode.value}.png"))


@pytest.mark.parametrize(
    ("low", "high", "whole", "labels"),
    [
        (0, 9648, True, ["0", "2,500", "5,000", "7,500", "10,000"]),
        (70.6, 71.2, False, ["70.6", "70.8", "71.0", "71.2"]),
        (4, 5, True, ["4", "5"]),
        (0, 1, True, ["0", "1"]),
        (-5, 12.123456, False, ["-5", "0", "5", "10", "15"]),
        (0, 1.5, False, ["0", "0.5", "1.0", "1.5"]),
        (0, 3_400_000, True, ["0", "1M", "2M", "3M", "4M"]),
    ],
)
def test_axis_ticks_are_round_values_in_plain_digits(
    low: float, high: float, whole: bool, labels: list[str]
) -> None:
    ticks = nice_ticks(low, high, whole=whole)
    step = ticks[1] - ticks[0]
    fuzz = step * 1e-9  # A float's last digits, far below a pixel.
    assert ticks[0] <= low + fuzz and ticks[-1] >= high - fuzz
    assert [axis_label(value, step) for value in ticks] == labels
    # Every gridline sits where its label says: the label is the tick, not a rounding of it.
    assert all(abs(b - a - step) <= fuzz for a, b in zip(ticks, ticks[1:], strict=False))


def test_axis_labels_at_the_edges_of_what_a_metric_can_hold() -> None:
    assert nice_ticks(3, 3) == [3, 4]  # No span: one step, never a division by zero.
    assert axis_label(0.25, 0.25) == "0.25" and axis_label(-0.5, 0.5) == "-0.5"
    assert axis_label(125_000_000, 25_000_000) == "125M"
    assert axis_label(2_500_000_000, 500_000_000) == "2.5B"
    assert axis_label(1.25e13, 2.5e12) == "12.5T"
    huge = nice_ticks(0, 1e30, whole=True)
    assert huge[-1] >= 1e30 * (1 - 1e-9)
    assert axis_label(huge[-1], huge[1] - huge[0]) == "1e+30"


def test_labels_under_the_plot_never_touch_and_always_name_the_last() -> None:
    assert label_marks(7, 51, 70) == [0, 2, 4, 6]
    assert label_marks(8, 40, 100) == [0, 3, 7]
    assert label_marks(3, 120, 95) == [0, 1, 2]
    assert label_marks(366, 1, 70)[-1] == 365
    assert label_marks(2, 20, 100) == [0, 1]
    assert label_marks(1, 300, 70) == [0]
    assert label_marks(0, 10, 10) == []


def test_every_category_is_named_and_dates_are_never_cut(qtbot: QtBot) -> None:
    groups = MetricDisplay(
        title="Completions by project",
        unit="episodes",
        chart=ChartKind.BAR,
        categorical=True,
        samples=tuple(
            MetricSample(key=name, label=name, value=Decimal(index + 1))
            for index, name in enumerate(("Thesis", "Home renovation", "Unassigned"))
        ),
    )
    card = MetricChartCard(groups)
    qtbot.addWidget(card)
    metrics = ui_metrics(CAPTION_PX)
    area = QRectF(64, 24, 360, 130)
    named = [text for _, text in card.plot.bottom_labels(metrics, area)]
    assert named == ["Thesis", "Home renovation", "Unassigned"]
    # Twenty-one narrow bars: fewer names, each cut to fit, and no two touching.
    many = groups.model_copy(
        update={
            "samples": tuple(
                MetricSample(key=str(i), label=f"Project number {i}", value=Decimal(i))
                for i in range(21)
            )
        }
    )
    card.plot.set_display(many)
    placed = card.plot.bottom_labels(metrics, area)
    assert 2 <= len(placed) < 21
    assert all(a.right() <= b.left() for (a, _), (b, _) in zip(placed, placed[1:], strict=False))
    # Dates: the first and the last, whole, with as many between as fit.
    days = MetricDisplay(
        title="Steps",
        unit="steps",
        chart=ChartKind.BAR,
        samples=tuple(
            MetricSample(key=f"2026-11-{day:02d}", label=f"2026-11-{day:02d}", value=Decimal(day))
            for day in range(3, 10)
        ),
    )
    card.plot.set_display(days)
    dates = [text for _, text in card.plot.bottom_labels(metrics, area)]
    assert dates[0] == "2026-11-03" and dates[-1] == "2026-11-09" and len(dates) >= 3
    assert all(text.startswith("2026-11-") and len(text) == 10 for text in dates)
    card.plot.set_display(MetricDisplay(title="Empty", unit="steps", chart=ChartKind.BAR))
    assert card.plot.bottom_labels(metrics, area) == []


def test_fresh_values_arrive_in_place_and_keep_the_owners_place(qtbot: QtBot) -> None:
    card = MetricChartCard(display(ChartKind.BAR))
    qtbot.addWidget(card)
    card.resize(500, 520)
    card.show()
    card.view_data.click()
    card.plot.select(2)
    card.table.selectRow(3)
    plot, table = card.plot, card.table
    fresh = display(ChartKind.BAR).model_copy(
        update={
            "total": Decimal(99),
            "caption": "Fresh.",
            "samples": (
                MetricSample(key="sun", label="Sunday", value=Decimal(1)),
                *display().samples,
            ),
        }
    )
    card.set_display(fresh)
    assert card.plot is plot and card.table is table  # The same widgets, not new ones.
    assert card.total.text() == "99 minutes" and card.caption.text() == "Fresh."
    assert card.table.isVisible() and card.view_data.isChecked()
    # The selection follows its sample, which is now one place further on.
    assert card.plot.selection == 3 and "Wednesday" in card.selection_label.text()
    assert card.table.currentIndex().row() == 4
    assert card.table_model.rowCount() == 5
    # A selected sample that has gone leaves the first one selected, while the table's
    # own row (Thursday) is followed back to where it now is; with no samples, none.
    card.plot.select(0)
    card.set_display(display(ChartKind.BAR))
    assert card.plot.selection == 0 and card.table.currentIndex().row() == 3
    card.set_display(MetricDisplay(title="Reading minutes", unit="minutes", chart=ChartKind.BAR))
    assert card.plot.selection == -1 and card.selection_label.text() == ""
    assert card.table_model.rowCount() == 0


def test_the_about_column_shows_only_when_a_value_has_something_to_say(qtbot: QtBot) -> None:
    card = MetricChartCard(display())
    qtbot.addWidget(card)
    assert not card.table.isColumnHidden(2)  # Tuesday carries "No observation".
    card.set_display(
        MetricDisplay(
            title="Steps",
            unit="steps",
            chart=ChartKind.LINE,
            samples=(MetricSample(key="a", label="2026-11-03", value=Decimal(8000)),),
        )
    )
    assert card.table.isColumnHidden(2)
    assert not card.table.verticalHeader().isVisible()


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        part = value / 255
        return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in (color.red(), color.green(), color.blue()))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_a_selected_table_row_is_readable(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode, ColorScheme.STUDIO)
    card = MetricChartCard(display(ChartKind.BAR))
    qtbot.addWidget(card)
    card.resize(520, 560)
    card.show()
    card.view_data.click()
    qapp.processEvents()
    row = card.table.visualRect(card.table_model.index(0, 0))
    image: QImage = card.table.viewport().grab(row).toImage()
    counts: dict[str, int] = {}
    for x in range(image.width()):
        for y in range(image.height()):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    background = _luminance(QColor(max(counts, key=lambda name: counts[name])))
    contrast = max(
        (max(_luminance(QColor(name)), background) + 0.05)
        / (min(_luminance(QColor(name)), background) + 0.05)
        for name in counts
    )
    assert contrast >= 4.5  # The row's text against its selected fill (WCAG AA).
