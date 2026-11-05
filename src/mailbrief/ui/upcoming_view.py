"""Known commitments and honest calendar occupancy, read from saved records."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QVBoxLayout, QWidget

from mailbrief.domain.upcoming import Upcoming
from mailbrief.ui.labels import wrap_label
from mailbrief.ui.lists import ActivatingList


class UpcomingDialog(QDialog):
    range_requested = Signal(int)
    open_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("upcomingDialog")
        self.setAccessibleName("Upcoming commitments")
        self.setWindowTitle("Upcoming")
        self.resize(640, 500)
        self._shown: Upcoming | None = None
        layout = QVBoxLayout(self)
        self.range = QComboBox()
        self.range.setObjectName("upcomingRange")
        self.range.setAccessibleName("Upcoming date range")
        self.range.addItem("Seven local dates starting today", 7)
        self.range.addItem("Thirty local dates starting today", 30)
        self.range.currentIndexChanged.connect(
            lambda _index: self.range_requested.emit(self.range.currentData())
        )
        layout.addWidget(self.range)
        self.status = wrap_label("Loading saved commitments…")
        self.status.setObjectName("upcomingStatus")
        self.status.setAccessibleName("Known occupancy and Google cache freshness")
        layout.addWidget(self.status)
        self.list = ActivatingList()
        self.list.setObjectName("upcomingCommitments")
        self.list.setAccessibleName("Known upcoming commitments")
        self.list.itemActivated.connect(lambda _item: self._open())
        layout.addWidget(self.list, 1)
        layout.addWidget(
            wrap_label(
                "Timed occupancy is the union of known meetings. All-day events are separate. "
                "This does not estimate available capacity."
            )
        )

    def show_upcoming(self, shown: Upcoming) -> None:
        self._shown = shown
        row = self.list.currentRow()
        self.list.clear()
        for item in shown.items:
            self.list.addItem(f"{item.day.isoformat()} · {item.title}\n" + " · ".join(item.reasons))
        self.list.setCurrentRow(min(max(0, row), len(shown.items) - 1))
        freshness = (
            shown.google_synced_at_utc.isoformat() if shown.google_synced_at_utc else "unknown"
        )
        coverage = "covers these dates" if shown.google_complete else "incomplete or unavailable"
        self.status.setText(
            f"{shown.timed_minutes:g} known timed meeting minutes · "
            f"{shown.all_day_events} all-day events. "
            f"Google cache: {coverage}; last refreshed {freshness}."
        )

    def _open(self) -> None:
        row = self.list.currentRow()
        if self._shown is not None and 0 <= row < len(self._shown.items):
            self.open_requested.emit(self._shown.items[row].route)
            self.reject()
