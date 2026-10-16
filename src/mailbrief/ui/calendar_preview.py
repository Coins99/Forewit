"""Read-only details of one cached Google event; opening its source is explicit."""

from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mailbrief.domain.events import CalendarEvent
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.ui.calendar_view import event_when
from mailbrief.ui.deadline_text import full_day_text
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.hairline import HairlineDivider
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.theme import ui_font


class GoogleEventDialog(QDialog):
    source_requested = Signal(object)
    event_requested = Signal(str, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Google Calendar event")
        self.resize(520, 380)
        self._event: GoogleEvent | CalendarEvent | None = None
        layout = QVBoxLayout(self)
        dialog_heading(self, layout, "Google Calendar event", "Cached event · read only")
        if (kicker := self.findChild(QLabel, "dialogHeading")) is not None:
            kicker.setObjectName("eventPreviewKicker")
            kicker.setText("EVENT DETAILS")
            kicker.setFont(ui_font(9))
        self.description = self.findChild(QLabel, "dialogDescription")
        self.title = wrap_label("", px=21, medium=True)
        self.title.setObjectName("googleEventTitle")
        self.notes = wrap_label("", tone="secondary", px=11)
        self.notes.setObjectName("eventPreviewNotes")
        self.when = wrap_label("", tone="secondary", px=11)
        self.when.setObjectName("googleEventWhen")
        self.calendar = wrap_label("", tone="secondary", px=11)
        self.calendar.setObjectName("googleEventCalendar")
        self.event_date = wrap_label("", px=11)
        self.event_date.setObjectName("eventPreviewDate")
        for label in (self.title, self.notes):
            layout.addWidget(label)
        layout.addWidget(HairlineDivider())
        facts = QFormLayout()
        facts.setVerticalSpacing(16)
        for caption, value in (
            ("Date", self.event_date),
            ("Time", self.when),
            ("Calendar", self.calendar),
        ):
            facts.addRow(plain_label(caption, tone="muted", px=8), value)
        layout.addLayout(facts)
        layout.addWidget(HairlineDivider())
        row = QHBoxLayout()
        self.source = outline_button(
            "Open in Google Calendar",
            "googleEventSource",
            primary=True,
            icon_name="external-link",
        )
        self.source.clicked.connect(self._open_source)
        self.close_button = outline_button("Close", "googleEventClose")
        self.close_button.clicked.connect(self.reject)
        self.close_button.hide()
        row.addWidget(self.source)
        self.edit_button = outline_button("Edit", "eventPreviewEdit", icon_name="pencil")
        self.delete_button = outline_button("Delete", "eventPreviewDelete")
        self.edit_button.clicked.connect(lambda: self._request("open"))
        self.delete_button.clicked.connect(lambda: self._request("delete"))
        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        layout.addStretch(1)

    def show_event(self, event: GoogleEvent | CalendarEvent, zone: ZoneInfo, today: date) -> None:
        self._event = event
        self.title.setText(event.title)
        google = isinstance(event, GoogleEvent)
        self.setAccessibleName("Google Calendar event" if google else "Local event details")
        self.notes.setText(event.notes if isinstance(event, CalendarEvent) else "")
        self.notes.setVisible(bool(self.notes.text()))
        if self.description is not None:
            self.description.setVisible(google)
        self.source.setVisible(google)
        self.edit_button.setVisible(not google)
        self.delete_button.setVisible(not google)
        first, last = event.days(zone)
        when = full_day_text(first, today=today)
        if event.all_day and last != first:
            when += " – " + full_day_text(last, today=today)
        self.event_date.setText(when)
        self.when.setText(event_when(event, first, zone, today))
        self.calendar.setText(
            event.calendar_name if isinstance(event, GoogleEvent) else "On this device"
        )
        self.source.setEnabled(isinstance(event, GoogleEvent) and event.web_link is not None)

    def _request(self, kind: str) -> None:
        event = self._event
        if isinstance(event, CalendarEvent):
            self.reject()
            self.event_requested.emit(kind, event)

    def _open_source(self) -> None:
        if isinstance(self._event, GoogleEvent) and self._event.web_link is not None:
            self.source_requested.emit(self._event)
