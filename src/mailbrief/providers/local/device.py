"""What to expect from the local model on this computer, said to the owner before setup.

The benchmark (P0-B1) ran Qwen3-4B on a computer with 16 GB of memory: about 15 seconds per
email with a graphics processor llama.cpp can use (Vulkan on Windows, Metal on macOS), and
about twice as long without one. Nothing was measured with less memory, so the owner is
warned then. The graphics processors are the ones llama-server itself lists.

Every reading is best effort: one that fails is None, never an error.
"""

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# A "16 GB" computer reports a little less than 16 GiB to programs, because some is reserved
# (for the firmware and built-in graphics).
MIN_MEMORY_BYTES: Final = 15 * 1024**3
_MEASURED_GB: Final = 16
GPU_SECONDS_PER_EMAIL: Final = 15
CPU_SECONDS_PER_EMAIL: Final = 30
LIST_TIMEOUT_SECONDS: Final = 60
_GIB: Final = 1024**3
_HEADER: Final = "Available devices:"
# "  Vulkan0: AMD Radeon(TM) 780M (16724 MiB, 15888 MiB free)"
_DEVICE: Final = re.compile(
    r"[ \t]+(?P<id>\S+?):[ \t]+(?P<name>\S.*?)[ \t]+"
    r"\((?P<memory>\d+) MiB, (?P<free>\d+) MiB free\)[ \t]*"
)


@dataclass(frozen=True, slots=True)
class GpuDevice:
    """A device llama-server can run the model on, as it lists it."""

    id: str
    name: str
    memory_mib: int
    free_mib: int


@dataclass(frozen=True, slots=True)
class DeviceAssessment:
    """What the owner can expect: the time per email and whether memory is short."""

    memory_bytes: int | None
    gpus: tuple[str, ...]
    seconds_per_email: int
    low_memory: bool


if sys.platform == "win32":
    import ctypes
    import functools
    from ctypes import wintypes

    _NO_WINDOW = subprocess.CREATE_NO_WINDOW

    class _MemoryStatus(ctypes.Structure):  # MEMORYSTATUSEX
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    @functools.cache
    def _kernel32() -> ctypes.WinDLL:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_MemoryStatus),)
        return kernel32

    def total_memory_bytes() -> int | None:
        """The computer's physical memory, or None when Windows doesn't say."""
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        try:
            if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
        except (OSError, AttributeError):
            return None
        total = int(status.ullTotalPhys)
        return total if total > 0 else None

elif sys.platform == "darwin":
    _NO_WINDOW = 0

    def total_memory_bytes() -> int | None:
        """The computer's physical memory, or None when sysctl doesn't say."""
        try:
            done = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            total = int(done.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return total if done.returncode == 0 and total > 0 else None

else:
    _NO_WINDOW = 0

    def total_memory_bytes() -> int | None:
        """The computer's physical memory, or None when the system doesn't say."""
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (OSError, ValueError):
            return None
        return total if total > 0 else None


def parse_devices(output: str) -> tuple[GpuDevice, ...]:
    """The devices in ``llama-server --list-devices`` output, in its order. Only the device
    lines after its "Available devices:" line count; any other line is ignored."""
    devices: list[GpuDevice] = []
    listed = False
    for line in output.splitlines():
        if line.strip() == _HEADER:
            listed = True
        elif listed and (match := _DEVICE.fullmatch(line)):
            devices.append(
                GpuDevice(
                    id=match["id"],
                    name=match["name"],
                    memory_mib=int(match["memory"]),
                    free_mib=int(match["free"]),
                )
            )
    return tuple(devices)


def list_devices(
    executable: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[GpuDevice, ...] | None:
    """The devices llama-server can use on this computer, possibly none; None when it
    couldn't be run or failed. Loading the graphics drivers can take seconds."""
    try:
        done = run(
            [str(executable), "--list-devices"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LIST_TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    # The list is on stdout and the drivers' messages on stderr; either may hold other lines.
    return parse_devices(f"{done.stdout or ''}\n{done.stderr or ''}")


def assess(memory_bytes: int | None, devices: tuple[GpuDevice, ...] | None) -> DeviceAssessment:
    """What to expect from the memory and devices found; None for either is unknown. Unknown
    devices count as none, so the owner is never promised the faster time."""
    gpus = tuple(device.name for device in devices or ())
    return DeviceAssessment(
        memory_bytes=memory_bytes,
        gpus=gpus,
        seconds_per_email=GPU_SECONDS_PER_EMAIL if gpus else CPU_SECONDS_PER_EMAIL,
        low_memory=memory_bytes is not None and memory_bytes < MIN_MEMORY_BYTES,
    )


def summary(assessment: DeviceAssessment) -> str:
    """One or two plain sentences for the owner."""
    seconds = assessment.seconds_per_email
    if assessment.gpus:
        gpus = ", ".join(assessment.gpus)
        text = f"About {seconds} seconds per email on this computer ({gpus})."
    else:
        text = (
            f"About {seconds} seconds per email: no graphics processor the model can use was found."
        )
    note = memory_note(assessment)
    return text if note is None else f"{text} {note}"


def memory_note(assessment: DeviceAssessment) -> str | None:
    """The warning for a computer with less memory than the model was measured with; None
    when memory is enough or unknown. It needs no runtime, so it can be shown before setup."""
    if not assessment.low_memory or assessment.memory_bytes is None:
        return None
    gb = round(assessment.memory_bytes / _GIB)
    return (
        f"This computer has {gb} GB of memory; the local model was measured with"
        f" {_MEASURED_GB} GB, so it may be slower or fail to load."
    )
