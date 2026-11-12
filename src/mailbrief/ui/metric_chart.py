"""Existing-Qt chart feasibility component, backed by an equivalent keyboard table.

No query, persistence or provider access happens here. Values remain exact in labels
and tables; only painter coordinates and approximate axis ticks use floats.
"""

import math
from typing import Final

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.dashboards import ChartKind
from mailbrief.domain.metric_results import MetricDisplay, metric_number
from mailbrief.ui.hairline import HairlineFrame, hairline_pen
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.theme import (
    CAPTION_PX,
    TEXT_PX,
    TITLE_PX,
    current_tokens,
    ui_font,
)

type _Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()
LABEL_GAP: Final = 10.0  # Between two labels under the plot.
CATEGORY_LABEL_MAX: Final = 120.0
CATEGORY_LABEL_MIN: Final = 34.0
_COMPACT: Final = ((1e12, "T"), (1e9, "B"), (1e6, "M"))


def nice_ticks(low: float, high: float, *, whole: bool = False) -> list[float]:
    """Round tick values from ``low`` to ``high``: steps of 1, 2, 2.5 or 5 times a power
    of ten, the first at or below ``low`` and the last at or above ``high``, so each
    gridline sits where its label says. ``whole`` keeps them whole numbers, for counts."""
    span = high - low
    if not math.isfinite(span) or span <= 0:
        return [low, low + 1.0]
    rough = span / 4
    magnitude = 10.0 ** math.floor(math.log10(rough))
    step = 10 * magnitude
    for factor in (1.0, 2.0, 2.5, 5.0):
        candidate = factor * magnitude
        if candidate >= rough and not (whole and candidate != round(candidate)):
            step = candidate
            break
    if whole:
        step = max(1.0, step)
    first = math.floor(low / step + 1e-9) * step
    count = max(1, math.ceil((high - first) / step - 1e-9))
    return [first + index * step for index in range(count + 1)]


def axis_label(value: float, step: float) -> str:
    """A tick's value in plain digits: grouped thousands, the decimals its step needs, and
    millions, billions and trillions as M, B and T. It approximates only the gridline;
    exact values are in the labels and the table."""
    if value == 0:
        return "0"
    size = abs(value)
    if size >= 1e15:
        return f"{value:.3g}"
    for scale, suffix in _COMPACT:
        if size >= scale:
            scaled = value / scale
            text = f"{scaled:.0f}" if abs(scaled) >= 100 else f"{scaled:.2f}".rstrip("0")
            return text.rstrip(".") + suffix
    decimals = next(
        (places for places in range(7) if abs(round(step, places) - step) <= step * 1e-6), 6
    )
    return f"{value:,.{decimals}f}"


def label_marks(count: int, slot: float, width: float) -> list[int]:
    """Which of ``count`` marks, ``slot`` apart, carry a label ``width`` wide without two
    labels touching: evenly spaced from the first, and always the last."""
    if count <= 0:
        return []
    every = max(1, math.ceil((width + LABEL_GAP) / max(slot, 0.001)))
    marks = list(range(0, count, every))
    if marks[-1] != count - 1:
        if len(marks) > 1:
            marks[-1] = count - 1
        else:
            marks.append(count - 1)
    return marks


class MetricTableModel(QAbstractTableModel):
    def __init__(self, data: MetricDisplay, parent: QWidget) -> None:
        super().__init__(parent)
        self.display = data

    def set_display(self, data: MetricDisplay) -> None:
        self.beginResetModel()
        self.display = data
        self.endResetModel()

    def rowCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self.display.samples)

    def columnCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else 3

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if (
            not index.isValid()
            or not 0 <= index.row() < len(self.display.samples)
            or not 0 <= index.column() < 3
        ):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return None
        sample = self.display.samples[index.row()]
        return (sample.label, metric_number(sample.value), sample.explanation)[index.column()]

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < 3
        ):
            return ("Date or category", self.display.unit, "About this value")[section]
        return None


class MetricDataTable(QTableView):
    """Activate rows once with either Enter key, including on Cocoa."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                self.activated.emit(index)
            event.accept()
            return
        super().keyPressEvent(event)


class MetricPlot(QWidget):
    selected = Signal(int)
    activated = Signal(str)

    def __init__(self, data: MetricDisplay, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.display = data
        self.selection = 0 if data.samples else -1
        self.setObjectName("metric-plot")
        self.setAccessibleName(data.title)
        self.setAccessibleDescription("Use arrow keys to select a value and Enter to inspect it.")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(190)
        self.setMinimumWidth(0)

    def sizeHint(self) -> QSize:
        return QSize(420, 220)

    def set_display(self, data: MetricDisplay) -> None:
        """Show fresh values, keeping the selected sample where it is still there."""
        old = self.display.samples
        key = old[self.selection].key if 0 <= self.selection < len(old) else None
        self.display = data
        self.setAccessibleName(data.title)
        keys = [sample.key for sample in data.samples]
        if not keys:
            self.selection = -1
            self.setAccessibleDescription(
                "Use arrow keys to select a value and Enter to inspect it."
            )
            self.update()
            return
        self.select(keys.index(key) if key in keys else max(self.selection, 0))

    def _area(self) -> QRectF:
        return QRectF(64, 24, max(1, self.width() - 78), max(1, self.height() - 58))

    def select(self, index: int) -> None:
        if not self.display.samples:
            return
        self.selection = max(0, min(index, len(self.display.samples) - 1))
        sample = self.display.samples[self.selection]
        self.setAccessibleDescription(
            f"{sample.label}: {metric_number(sample.value)} {self.display.unit}. "
            f"{sample.explanation} Use arrows and Enter, or View data."
        )
        self.selected.emit(self.selection)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            index = self.selection + (1 if event.key() == Qt.Key.Key_Right else -1)
            if event.key() == Qt.Key.Key_Home:
                index = 0
            elif event.key() == Qt.Key.Key_End:
                index = len(self.display.samples) - 1
            self.select(index)
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.selection >= 0:
            self.activated.emit(self.display.samples[self.selection].key)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._area().contains(event.position()):
            count = len(self.display.samples)
            if count:
                self.select(
                    int((event.position().x() - self._area().left()) * count / self._area().width())
                )
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.activated.emit(self.display.samples[self.selection].key)
        super().mousePressEvent(event)

    def bottom_labels(self, metrics: QFontMetrics, area: QRectF) -> list[tuple[QRectF, str]]:
        """Where each label under the plot goes, and its text. Categories are named at
        every bar that has room, a long name cut to its bar; dates stay whole, so fewer of
        them are shown. The first and the last are always there."""
        labels = [sample.label for sample in self.display.samples]
        if not labels:
            return []
        slot = area.width() / len(labels)
        widest = float(max(metrics.horizontalAdvance(label) for label in labels))
        if self.display.categorical:
            width = min(widest, CATEGORY_LABEL_MAX, max(CATEGORY_LABEL_MIN, slot - 6))
        else:
            width = min(widest, max(20.0, area.width() / 2 - LABEL_GAP))
        placed = []
        for index in label_marks(len(labels), slot, width):
            x = min(
                max(area.left(), area.left() + (index + 0.5) * slot - width / 2),
                area.right() - width,
            )
            text = metrics.elidedText(labels[index], Qt.TextElideMode.ElideRight, int(width) + 1)
            placed.append((QRectF(x, area.bottom() + 8, width, 18), text))
        return placed

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tokens = current_tokens()
        painter.setFont(ui_font(CAPTION_PX, tabular=True))
        painter.setPen(QColor(tokens.text_secondary))
        painter.drawText(QRectF(0, 0, self.width(), 20), self.display.unit)
        area = self._area()
        exact = [sample.value for sample in self.display.samples if sample.value is not None]
        if not exact:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No observations")
            return
        if self.display.daily_target is not None:
            exact.append(self.display.daily_target)
        whole = all(value == value.to_integral_value() for value in exact)
        values = [float(value) for value in exact]
        if self.display.chart is ChartKind.BAR:
            values.append(0.0)  # Bars grow from zero, so zero is always a gridline.
        low, high = min(values), max(values)
        if low == high:
            padding = max(abs(low) * 0.1, 1.0)
            # A flat line at zero or above sits on the axis: counts never dip below zero.
            low, high = max(0.0, low - padding) if low >= 0 else low - padding, high + padding
        ticks = nice_ticks(low, high, whole=whole)
        # The axis runs between its end ticks; the data's own ends guard a float's last digit.
        low, high = min(low, ticks[0]), max(high, ticks[-1])

        def y(value: float) -> float:
            return area.bottom() - (value - low) * area.height() / (high - low)

        for value in ticks:
            at = y(value)
            painter.setPen(hairline_pen(tokens.hairline))
            painter.drawLine(QPointF(area.left(), at), QPointF(area.right(), at))
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(
                QRectF(0, at - 8, area.left() - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                axis_label(value, ticks[1] - ticks[0]),
            )
        if self.display.daily_target is not None:
            pen = QPen(QColor(tokens.text_secondary), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            at = y(float(self.display.daily_target))
            painter.drawLine(QPointF(area.left(), at), QPointF(area.right(), at))
        count = len(self.display.samples)
        slot = area.width() / count
        previous: QPointF | None = None
        painter.save()
        painter.setClipRect(area.adjusted(-2, -3, 2, 3))
        for index, sample in enumerate(self.display.samples):
            if sample.value is None:
                previous = None
                continue
            point = QPointF(area.left() + (index + 0.5) * slot, y(float(sample.value)))
            painter.setPen(QPen(QColor(tokens.accent_fg), 2))
            painter.setBrush(QColor(tokens.accent_fg))
            if self.display.chart is ChartKind.BAR:
                baseline = y(0)
                bar = QRectF(
                    point.x() - slot * 0.35,
                    min(point.y(), baseline),
                    slot * 0.7,
                    max(abs(point.y() - baseline), 2),
                )
                painter.drawRoundedRect(bar, min(3.0, slot * 0.2), min(3.0, slot * 0.2))
            else:
                if previous is not None:
                    painter.drawLine(previous, point)
                painter.drawEllipse(point, 2, 2)
                previous = point
            if index == self.selection:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(tokens.text), 1))
                painter.drawEllipse(point, 5, 5)
        painter.restore()
        painter.setPen(QColor(tokens.text_secondary))
        for place, text in self.bottom_labels(painter.fontMetrics(), area):
            painter.drawText(place, Qt.AlignmentFlag.AlignCenter, text)
        if self.hasFocus():
            painter.setPen(hairline_pen(tokens.accent_fg))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)


class MetricChartCard(HairlineFrame):
    """Prepared-data chart with an equivalent exact table and keyboard inspection."""

    activated = Signal(str)

    def __init__(self, data: MetricDisplay, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metric-chart-card")
        self.setAccessibleName(data.title)
        self.setMinimumWidth(0)
        self.display = data
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title_label = wrap_label("", px=TEXT_PX, semibold=True)
        self.title_label.setObjectName("metric-chart-title")
        layout.addWidget(self.title_label)
        self.total = wrap_label("", px=TITLE_PX)
        self.total.setObjectName("metric-chart-total")
        layout.addWidget(self.total)
        self.plot = MetricPlot(data, self)
        self.plot.activated.connect(self.activated)
        layout.addWidget(self.plot)
        self.selection_label = wrap_label("", px=CAPTION_PX)
        self.selection_label.setObjectName("metric-chart-selection")
        self.selection_label.setAccessibleName("Selected value")
        layout.addWidget(self.selection_label)
        self.plot.selected.connect(self._selected)
        self.caption = wrap_label("", px=CAPTION_PX)
        self.caption.setObjectName("metric-chart-caption")
        self.caption.setAccessibleName("Metric definition and coverage")
        layout.addWidget(self.caption)
        self.view_data = outline_button("View data", "metric-view-data")
        self.view_data.setCheckable(True)
        layout.addWidget(self.view_data)
        self.table = MetricDataTable(self)
        self.table.setObjectName("metric-data-table")
        self.table.setProperty("dataTable", True)  # The theme's table look (style.py).
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_model = MetricTableModel(data, self.table)
        self.table.setModel(self.table_model)
        self.table.verticalHeader().hide()
        # Date and value columns as wide as their content; the last one shown takes the rest.
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setMinimumHeight(160)
        self.table.hide()
        self.table.activated.connect(self._activate_row)
        self.view_data.toggled.connect(self._show_data)
        layout.addWidget(self.table)
        self._show(data)
        if data.samples:
            self.plot.select(0)

    def _show(self, data: MetricDisplay) -> None:
        """Every text and name that follows the data."""
        self.setAccessibleName(data.title)
        self.title_label.setText(data.title)
        self.title_label.setAccessibleName(data.title)
        self.total.setText(f"{metric_number(data.total)} {data.unit}")
        self.total.setAccessibleName(self.total.text())
        charted = data.chart is not ChartKind.NUMBER
        self.plot.setVisible(charted)
        self.selection_label.setVisible(charted)
        caption = data.caption
        if data.daily_target is not None:
            caption += f" Daily target: {metric_number(data.daily_target)} {data.unit}."
        self.caption.setText(caption)
        self.view_data.setAccessibleName(f"View data for {data.title}")
        self.table.setAccessibleName(f"{data.title}: exact values in {data.unit}")
        # "About this value" is shown only when some value has something to say.
        self.table.setColumnHidden(2, not any(sample.explanation for sample in data.samples))

    def set_display(self, data: MetricDisplay) -> None:
        """Show fresh values in place. The card, its focus, the selected value and an open
        data table stay as the owner left them: a refresh must not move their place."""
        vertical, horizontal = self.table.verticalScrollBar(), self.table.horizontalScrollBar()
        scroll = (vertical.value(), horizontal.value())
        row = self.table.currentIndex().row()
        old = self.display.samples
        key = old[row].key if 0 <= row < len(old) else None
        self.display = data
        self.table_model.set_display(data)
        self.plot.set_display(data)
        self._show(data)
        keys = [sample.key for sample in data.samples]
        if not keys:
            self.selection_label.setText("")
        elif key is not None:
            self.table.selectRow(keys.index(key) if key in keys else 0)
        # Reselecting scrolls to the current row, which the owner may have scrolled away
        # from. Lay out the new rows before restoring the viewport, clamped to its range.
        self.table.doItemsLayout()
        vertical.setValue(scroll[0])
        horizontal.setValue(scroll[1])

    def _show_data(self, visible: bool) -> None:
        self.table.setVisible(visible)
        self.view_data.setText("Hide data" if visible else "View data")
        if visible:
            if self.display.samples:
                self.table.selectRow(max(0, self.plot.selection))
            self.table.setFocus(Qt.FocusReason.TabFocusReason)

    def _selected(self, index: int) -> None:
        sample = self.display.samples[index]
        self.selection_label.setText(
            f"{sample.label}: {metric_number(sample.value)} {self.display.unit}. "
            f"{sample.explanation}"
        )

    def _activate_row(self, index: QModelIndex) -> None:
        if index.isValid():
            self.activated.emit(self.display.samples[index.row()].key)
