"""The system's accessibility settings that change how glass is drawn, read without new
dependencies.

- Increased contrast: Qt's `contrastPreference` (Windows high contrast, and macOS
  Increase contrast), and on macOS also
  `NSWorkspace.accessibilityDisplayShouldIncreaseContrast`.
- Reduced transparency: macOS `accessibilityDisplayShouldReduceTransparency`, or
  Windows' "Transparency effects" switch (`EnableTransparency` in the registry). Qt has
  no API for it.
- Reduced motion: macOS `accessibilityDisplayShouldReduceMotion`, or Windows' "Animation
  effects" switch (`SPI_GETCLIENTAREAANIMATION`). Qt 6.11 has no API for it.

Every read that fails counts as "no preference": glass then draws normally, and the
owner's own choices in Settings still apply.
"""

import ctypes
import ctypes.util
import functools
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

# (increase contrast, reduce transparency, reduce motion), as the platform reports them.
Flags = tuple[bool, bool, bool]


@dataclass(frozen=True, slots=True)
class SystemAccessibility:
    increase_contrast: bool = False
    reduce_transparency: bool = False
    reduce_motion: bool = False


def _qt_increase_contrast() -> bool:
    hints = QGuiApplication.styleHints()
    return hints.accessibility().contrastPreference() == Qt.ContrastPreference.HighContrast


@functools.cache
def _workspace_query() -> Callable[[bytes], bool] | None:
    """Asks NSWorkspace's shared instance a yes-or-no question by name, or None without
    the Objective-C runtime. Each objc_msgSend is cast to its exact function type, as
    Apple silicon requires."""
    library = ctypes.util.find_library("objc")
    if library is None:
        return None
    objc = ctypes.CDLL(library)
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc)
    )
    send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc)
    )
    workspace = send_id(
        objc.objc_getClass(b"NSWorkspace"), objc.sel_registerName(b"sharedWorkspace")
    )
    if not workspace:
        return None

    def ask(name: bytes) -> bool:
        return bool(send_bool(workspace, objc.sel_registerName(name)))

    return ask


def macos_flags() -> Flags:
    ask = _workspace_query()
    if ask is None:
        return (False, False, False)
    return (
        ask(b"accessibilityDisplayShouldIncreaseContrast"),
        ask(b"accessibilityDisplayShouldReduceTransparency"),
        ask(b"accessibilityDisplayShouldReduceMotion"),
    )


if sys.platform == "win32":
    import winreg

    def platform_flags() -> Flags:
        """The registry's transparency switch and SystemParametersInfo's animation switch;
        Qt reports Windows high contrast itself."""
        reduce_transparency = False
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, _kind = winreg.QueryValueEx(key, "EnableTransparency")
            reduce_transparency = value == 0
        except OSError:
            pass
        animations = ctypes.c_int(1)
        get_client_area_animation = 0x1042
        if not ctypes.windll.user32.SystemParametersInfoW(
            get_client_area_animation, 0, ctypes.byref(animations), 0
        ):
            return (False, reduce_transparency, False)
        return (False, reduce_transparency, animations.value == 0)

elif sys.platform == "darwin":
    platform_flags = macos_flags

else:

    def platform_flags() -> Flags:
        return (False, False, False)


def read_system(
    flags: Callable[[], Flags] = platform_flags,
    contrast: Callable[[], bool] = _qt_increase_contrast,
) -> SystemAccessibility:
    """The settings now; a read that fails counts as no preference."""
    try:
        qt_contrast = contrast()
    except Exception:
        qt_contrast = False
    try:
        increase_contrast, reduce_transparency, reduce_motion = flags()
    except Exception:
        increase_contrast, reduce_transparency, reduce_motion = False, False, False
    return SystemAccessibility(
        increase_contrast=qt_contrast or increase_contrast,
        reduce_transparency=reduce_transparency,
        reduce_motion=reduce_motion,
    )
