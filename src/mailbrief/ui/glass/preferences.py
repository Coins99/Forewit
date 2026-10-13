"""How glass is drawn on this device: normal, or with reduced transparency or increased
contrast, and whether motion is reduced. Widgets read it at paint time.

`effective_preferences` combines the system's accessibility settings with the owner's
choices in Settings > Appearance: the owner can make glass solid or reduce motion even
when the system doesn't, never the reverse, and increased contrast always wins.
"""

from dataclasses import dataclass
from enum import StrEnum

from mailbrief.ui.glass.painter import GlassMode
from mailbrief.ui.glass.system import SystemAccessibility


class Transparency(StrEnum):
    SYSTEM = "system"  # Follow the system's reduce-transparency setting.
    SOLID = "solid"  # Always draw glass solid.


class Motion(StrEnum):
    SYSTEM = "system"  # Follow the system's reduce-motion setting.
    REDUCED = "reduced"  # Never animate glass.


@dataclass(frozen=True, slots=True)
class GlassPreferences:
    mode: GlassMode = GlassMode.NORMAL
    reduce_motion: bool = False


def effective_preferences(
    system: SystemAccessibility, transparency: Transparency, motion: Motion
) -> GlassPreferences:
    if system.increase_contrast:
        mode = GlassMode.INCREASED_CONTRAST
    elif system.reduce_transparency or transparency is Transparency.SOLID:
        mode = GlassMode.REDUCED_TRANSPARENCY
    else:
        mode = GlassMode.NORMAL
    reduce_motion = system.reduce_motion or motion is Motion.REDUCED
    return GlassPreferences(mode=mode, reduce_motion=reduce_motion)


_current = GlassPreferences()


def glass_preferences() -> GlassPreferences:
    return _current


def set_glass_preferences(preferences: GlassPreferences) -> None:
    global _current
    _current = preferences
