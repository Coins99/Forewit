"""Origin and optional review state for ordinary owner records."""

from enum import StrEnum


class CaptureOrigin(StrEnum):
    LEGACY = "legacy"
    DIRECT = "direct"
    QUICK_CAPTURE = "quick_capture"
