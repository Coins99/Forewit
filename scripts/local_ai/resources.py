"""Resource readings: a server process's peak memory, CPU time and GPU memory, the battery's
remaining charge, and a description of the device. Every reading is best effort: one that
fails is None, never an error that stops the benchmark."""

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessUsage:
    peak_memory_bytes: int | None
    cpu_seconds: float | None
    gpu_memory_bytes: int | None = None  # Dedicated and shared, read once (Windows).


@dataclass(frozen=True, slots=True)
class BatterySample:
    remaining_mwh: int
    on_ac_power: bool


UNKNOWN = ProcessUsage(None, None)


def _run(command: list[str]) -> str | None:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _powershell_json(script: str) -> object:
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    try:
        return None if not out else json.loads(out)
    except ValueError:
        return None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _QUERY = 0x1000 | 0x0010  # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def _ticks(value: wintypes.FILETIME) -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    def _gpu_memory(pid: int) -> int | None:
        """The process's GPU memory now, dedicated plus shared, from Windows' counters."""
        script = (
            f"(Get-Counter '\\GPU Process Memory(pid_{pid}_*)\\Dedicated Usage',"
            f"'\\GPU Process Memory(pid_{pid}_*)\\Shared Usage' -ErrorAction Stop)"
            ".CounterSamples | Measure-Object -Property CookedValue -Sum"
            " | Select-Object -ExpandProperty Sum | ConvertTo-Json"
        )
        total = _powershell_json(script)
        return int(total) if isinstance(total, int | float) and total > 0 else None

    def process_usage(pid: int) -> ProcessUsage:
        """A running process's peak working set, CPU time and GPU memory."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_QUERY, False, pid)
        if not handle:
            return UNKNOWN
        peak: int | None = None
        cpu: float | None = None
        try:
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                peak = int(counters.PeakWorkingSetSize)
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                cpu = (_ticks(kernel) + _ticks(user)) / 10_000_000  # 100 ns units.
        finally:
            kernel32.CloseHandle(handle)
        return ProcessUsage(peak, cpu, _gpu_memory(pid))

    def finished_children_usage() -> ProcessUsage:
        return UNKNOWN  # Windows reads the live process instead.

    def battery_sample() -> BatterySample | None:
        """The battery's remaining charge, or None without a battery or a reading."""
        data = _powershell_json(
            "Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus"
            " | Select-Object -First 1 RemainingCapacity,PowerOnline | ConvertTo-Json -Compress"
        )
        if not isinstance(data, dict):
            return None
        remaining, online = data.get("RemainingCapacity"), data.get("PowerOnline")
        if type(remaining) is not int or not isinstance(online, bool):
            return None
        return BatterySample(remaining, online)

    def _hardware() -> dict[str, object]:
        data = _powershell_json(
            "[pscustomobject]@{"
            "cpu=(Get-CimInstance Win32_Processor | Select-Object -First 1).Name;"
            "memory_bytes=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
            "gpus=@(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })"
            "} | ConvertTo-Json -Compress"
        )
        return data if isinstance(data, dict) else {}

else:
    import resource

    def process_usage(pid: int) -> ProcessUsage:
        return UNKNOWN  # Read once the server exits, by finished_children_usage().

    def finished_children_usage() -> ProcessUsage:
        """Peak memory and CPU time of the finished child processes."""
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        scale = 1 if sys.platform == "darwin" else 1024  # macOS reports bytes, Linux KiB.
        return ProcessUsage(usage.ru_maxrss * scale, usage.ru_utime + usage.ru_stime)

    def battery_sample() -> BatterySample | None:
        return None  # Not measured here yet.

    def _hardware() -> dict[str, object]:
        # macOS names; elsewhere sysctl fails or lacks them and both stay None.
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        memory = _run(["sysctl", "-n", "hw.memsize"])
        return {
            "cpu": None if cpu is None else cpu.strip(),
            "memory_bytes": int(memory) if memory and memory.strip().isdigit() else None,
        }


def device() -> dict[str, object]:
    """What the benchmark ran on."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
        **_hardware(),
    }
