"""How glass is drawn on this device: normal, or with reduced transparency or increased
contrast, and whether motion is reduced. Widgets read it at paint time. It holds the
defaults until the system's accessibility settings are read (a later change)."""

from dataclasses import dataclass

from mailbrief.ui.glass.painter import GlassMode


@dataclass(frozen=True, slots=True)
class GlassPreferences:
    mode: GlassMode = GlassMode.NORMAL
    reduce_motion: bool = False


_current = GlassPreferences()


def glass_preferences() -> GlassPreferences:
    return _current


def set_glass_preferences(preferences: GlassPreferences) -> None:
    global _current
    _current = preferences
