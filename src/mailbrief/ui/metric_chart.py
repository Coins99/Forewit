"""Existing-Qt chart feasibility component, backed by an equivalent keyboard table.

No query, persistence or provider access happens here. Values remain exact in labels
and tables; only painter coordinates and approximate axis ticks use floats.
"""

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
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
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


class MetricTableModel(QAbstractTableModel):
    def __init__(self, data: MetricDisplay, parent: QWidget) -> None:
        super().__init__(parent)
        self.display = data

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
        values = [
            float(sample.value) for sample in self.display.samples if sample.value is not None
        ]
        if not values:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No observations")
            return
        if self.display.daily_target is not None:
            values.append(float(self.display.daily_target))
        if self.display.chart is ChartKind.BAR:
            values.append(0.0)
        low, high = min(values), max(values)
        if low == high:
            padding = max(abs(low) * 0.1, 1.0)
            low, high = low - padding, high + padding

        def y(value: float) -> float:
            return area.bottom() - (value - low) * area.height() / (high - low)

        ticks = [low + (high - low) * tick / 4 for tick in range(5)]
        if self.display.chart is ChartKind.BAR:
            ticks = [value for value in ticks if abs(y(value) - y(0)) > 14]
            ticks.append(0.0)
        for value in ticks:
            at = y(value)
            painter.setPen(hairline_pen(tokens.hairline))
            painter.drawLine(QPointF(area.left(), at), QPointF(area.right(), at))
            painter.setPen(QColor(tokens.text_secondary))
            painter.drawText(
                QRectF(0, at - 8, area.left() - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.3g}",
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
        tick_indices = {0, count - 1}
        if area.width() >= 400:
            tick_indices.add(count // 2)
        for index in sorted(tick_indices):
            width = min(120.0, max(20.0, area.width() / 3))
            x = min(
                max(area.left(), area.left() + (index + 0.5) * slot - width / 2),
                area.right() - width,
            )
            text = painter.fontMetrics().elidedText(
                self.display.samples[index].label, Qt.TextElideMode.ElideRight, int(width)
            )
            painter.drawText(
                QRectF(x, area.bottom() + 8, width, 18), Qt.AlignmentFlag.AlignCenter, text
            )
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
        title = wrap_label(data.title, px=TEXT_PX, semibold=True)
        title.setObjectName("metric-chart-title")
        title.setAccessibleName(data.title)
        layout.addWidget(title)
        self.total = wrap_label(f"{metric_number(data.total)} {data.unit}", px=TITLE_PX)
        self.total.setObjectName("metric-chart-total")
        self.total.setAccessibleName(self.total.text())
        layout.addWidget(self.total)
        self.plot = MetricPlot(data, self)
        self.plot.setVisible(data.chart is not ChartKind.NUMBER)
        self.plot.activated.connect(self.activated)
        layout.addWidget(self.plot)
        self.selection_label = wrap_label("", px=CAPTION_PX)
        self.selection_label.setObjectName("metric-chart-selection")
        self.selection_label.setAccessibleName("Selected value")
        self.selection_label.setVisible(data.chart is not ChartKind.NUMBER)
        layout.addWidget(self.selection_label)
        self.plot.selected.connect(self._selected)
        caption_text = data.caption
        if data.daily_target is not None:
            caption_text += f" Daily target: {metric_number(data.daily_target)} {data.unit}."
        caption = wrap_label(caption_text, px=CAPTION_PX)
        caption.setObjectName("metric-chart-caption")
        caption.setAccessibleName("Metric definition and coverage")
        layout.addWidget(caption)
        self.view_data = outline_button("View data", "metric-view-data")
        self.view_data.setAccessibleName(f"View data for {data.title}")
        self.view_data.setCheckable(True)
        layout.addWidget(self.view_data)
        self.table = QTableView(self)
        self.table.setObjectName("metric-data-table")
        self.table.setAccessibleName(f"{data.title}: exact values in {data.unit}")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_model = MetricTableModel(data, self.table)
        self.table.setModel(self.table_model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(160)
        self.table.hide()
        self.table.activated.connect(self._activate_row)
        self.view_data.toggled.connect(self._show_data)
        layout.addWidget(self.table)
        if data.samples:
            self.plot.select(0)

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
