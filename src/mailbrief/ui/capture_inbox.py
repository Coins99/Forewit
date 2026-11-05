"""Review canonical captures without requiring organization or dates."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout, QWidget

from mailbrief.domain.actions import Action
from mailbrief.domain.drafts import Draft
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList


class CaptureInbox(QDialog):
    open_requested = Signal(object)
    keep_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("captureInbox")
        self.setAccessibleName("Capture inbox")
        self.setWindowTitle("Capture inbox")
        self.resize(560, 440)
        self._records: tuple[Action | Draft, ...] = ()
        self._shown: tuple[Action | Draft, ...] = ()
        layout = QVBoxLayout(self)
        layout.addWidget(
            wrap_label(
                "Up to 100 unprocessed quick captures. They are already usable tasks and notes."
            )
        )
        self.kind = QComboBox()
        self.kind.setObjectName("captureInboxKind")
        self.kind.setAccessibleName("Capture inbox type")
        self.kind.addItems(["Tasks and notes", "Tasks", "Notes"])
        self.kind.currentIndexChanged.connect(self._fill)
        layout.addWidget(self.kind)
        self.list = ActivatingList()
        self.list.setObjectName("captureInboxRecords")
        self.list.setAccessibleName("Unprocessed captures")
        layout.addWidget(self.list, 1)
        self.status = wrap_label("")
        self.status.setObjectName("captureInboxStatus")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for name, label, opens in (
            ("captureInboxOpen", "Open and organize", True),
            ("captureInboxKeep", "Keep as is", False),
        ):
            button = outline_button(label, name)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, target=opens: self._request(target))
            row.addWidget(button)
        layout.addLayout(row)
        self.list.itemActivated.connect(lambda _item: self._request(True))

    def show_records(self, records: tuple[Action | Draft, ...]) -> None:
        self._records = records
        self._fill()

    def _fill(self) -> None:
        selected = self.list.currentRow()
        kind = self.kind.currentIndex()
        self._shown = tuple(
            record
            for record in self._records
            if kind == 0 or isinstance(record, Action) == (kind == 1)
        )
        self.list.clear()
        for record in self._shown:
            self.list.addItem(
                ("Task · " + record.title)
                if isinstance(record, Action)
                else ("Note · " + record.display_title)
            )
        self.list.setCurrentRow(min(max(0, selected), len(self._shown) - 1))

    def _request(self, opens: bool) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._shown):
            if opens:
                self.open_requested.emit(self._shown[row])
            else:
                self.keep_requested.emit(self._shown[row])
