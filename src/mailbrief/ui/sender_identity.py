"""A literal sender identity shared by the brief and cached mail reader."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from mailbrief.domain.messages import EmailContact
from mailbrief.ui.brief_list import initials, sender_text, sender_with_address
from mailbrief.ui.labels import plain_label, wrap_label


def sender_identity(contact: EmailContact, *, name: str) -> QWidget:
    identity = QWidget()
    identity.setObjectName(name + "Identity")
    identity.setAccessibleName(sender_with_address(contact))
    row = QHBoxLayout(identity)
    row.setContentsMargins(0, 0, 0, 20)
    row.setSpacing(10)
    # A display name resembling an address must never impersonate the real address.
    safe_name = sender_with_address(contact) != contact.address
    display = sender_text(contact) if safe_name else contact.address
    avatar = plain_label(initials(display), px=10, medium=True)
    avatar.setObjectName("senderAvatar")
    avatar.setAccessibleName("Sender initials")
    avatar.setFixedSize(34, 34)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row.addWidget(avatar)
    copy = QVBoxLayout()
    copy.setSpacing(2)
    title = wrap_label(display, px=10, medium=True)
    title.setObjectName(name)
    title.setAccessibleName(sender_with_address(contact))
    copy.addWidget(title)
    if safe_name:
        address = wrap_label(contact.address, tone="muted", px=9)
        address.setObjectName(name + "Address")
        copy.addWidget(address)
    row.addLayout(copy, 1)
    return identity
