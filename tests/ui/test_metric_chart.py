"""Prepared-data chart spike: accessible precision, keyboard, states and native painting."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.dashboards import ChartKind
from mailbrief.domain.metric_results import MetricDisplay, MetricSample
from mailbrief.ui.metric_chart import MetricChartCard
from mailbrief.ui.theme import ColorScheme, ThemeMode, apply_theme


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
