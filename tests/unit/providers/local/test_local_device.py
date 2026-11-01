"""What the local model can expect from this computer: its memory, the devices llama-server
lists, the time per email and the sentence the owner reads."""

import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

from mailbrief.providers.local import device as device_module
from mailbrief.providers.local.device import (
    CPU_SECONDS_PER_EMAIL,
    GPU_SECONDS_PER_EMAIL,
    MIN_MEMORY_BYTES,
    DeviceAssessment,
    GpuDevice,
    assess,
    list_devices,
    parse_devices,
    summary,
    total_memory_bytes,
)

GIB = 1024**3
EXECUTABLE = Path("local-ai") / "llama-server.exe"
# llama-server b12015 on Windows, after a log line, with Windows line endings.
WINDOWS = (
    "load_backend: loaded Vulkan backend from ggml-vulkan.dll\r\n"
    "Available devices:\r\n"
    "  Vulkan0: AMD Radeon(TM) 780M (16724 MiB, 15888 MiB free)\r\n"
)
METAL = "Available devices:\n  MTL0: Apple M2 (10922 MiB, 10922 MiB free)\n"
RADEON = GpuDevice(id="Vulkan0", name="AMD Radeon(TM) 780M", memory_mib=16724, free_mib=15888)
APPLE = GpuDevice(id="MTL0", name="Apple M2", memory_mib=10922, free_mib=10922)


class FakeRun:
    """Stands in for subprocess.run: records each call and answers or raises."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, options))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def refuse(*args: object, **kwargs: object) -> NoReturn:
    raise OSError("refused")


def test_the_windows_list_is_read_after_other_lines() -> None:
    assert parse_devices(WINDOWS) == (RADEON,)


def test_a_metal_list_is_read() -> None:
    assert parse_devices(METAL) == (APPLE,)


def test_several_devices_keep_their_order() -> None:
    output = (
        "Available devices:\n"
        "  CUDA0: NVIDIA GeForce RTX 4060 Laptop GPU (8188 MiB, 7040 MiB free)\n"
        "  Vulkan1: Intel(R) UHD Graphics (8000 MiB, 7000 MiB free)\n"
    )
    assert [(gpu.id, gpu.name) for gpu in parse_devices(output)] == [
        ("CUDA0", "NVIDIA GeForce RTX 4060 Laptop GPU"),
        ("Vulkan1", "Intel(R) UHD Graphics"),
    ]


def test_an_empty_list_has_no_devices() -> None:
    assert parse_devices("Available devices:\n") == ()


@pytest.mark.parametrize(
    "output",
    [
        "",
        "error: invalid argument: --list-devices\n",
        # A device line without the header.
        "  Vulkan0: AMD Radeon(TM) 780M (16724 MiB, 15888 MiB free)\n",
        "Available devices:\n"
        "Vulkan0: AMD Radeon(TM) 780M (16724 MiB, 15888 MiB free)\n"  # Not indented.
        "  Vulkan0: AMD Radeon(TM) 780M\n"
        "  Vulkan0: AMD Radeon(TM) 780M (many MiB, 15888 MiB free)\n"
        "  Vulkan0: (16724 MiB, 15888 MiB free)\n"
        "  \x00\xff garbage\n",
    ],
)
def test_garbage_or_a_missing_header_has_no_devices(output: str) -> None:
    assert parse_devices(output) == ()


def test_devices_are_listed_by_running_llama_server() -> None:
    run = FakeRun(stdout=WINDOWS.replace("\r\n", "\n"), stderr="ggml_vulkan: Found 1 device\n")

    assert list_devices(EXECUTABLE, run=run) == (RADEON,)

    [(command, options)] = run.calls
    assert command == [str(EXECUTABLE), "--list-devices"]
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "replace"
    assert options["timeout"] == 60
    assert options["stdin"] == subprocess.DEVNULL
    if sys.platform == "win32":
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert options["creationflags"] == 0


def test_a_build_without_a_usable_gpu_lists_none() -> None:
    assert list_devices(EXECUTABLE, run=FakeRun(stdout="Available devices:\n")) == ()


@pytest.mark.parametrize(
    ("returncode", "error"),
    [
        (1, None),
        (0, FileNotFoundError("llama-server.exe")),
        (0, PermissionError("denied")),
        (0, subprocess.TimeoutExpired(["llama-server"], 60)),
        (0, subprocess.SubprocessError()),
    ],
    ids=["exit-1", "missing", "denied", "timeout", "subprocess-error"],
)
def test_a_server_that_cant_list_says_nothing(returncode: int, error: Exception | None) -> None:
    run = FakeRun(returncode=returncode, stdout=METAL, error=error)
    assert list_devices(EXECUTABLE, run=run) is None
    assert len(run.calls) == 1


@pytest.mark.parametrize(
    ("memory", "low"),
    [
        (MIN_MEMORY_BYTES - 1, True),
        (MIN_MEMORY_BYTES, False),
        (16 * GIB, False),
        (8 * GIB, True),
        (None, False),
    ],
)
def test_memory_below_the_threshold_is_low(memory: int | None, low: bool) -> None:
    assessment = assess(memory, (RADEON,))
    assert assessment.low_memory is low
    assert assessment.memory_bytes == memory


def test_a_gpu_makes_it_faster() -> None:
    assert assess(16 * GIB, (RADEON, APPLE)) == DeviceAssessment(
        memory_bytes=16 * GIB,
        gpus=("AMD Radeon(TM) 780M", "Apple M2"),
        seconds_per_email=GPU_SECONDS_PER_EMAIL,
        low_memory=False,
    )
    assert GPU_SECONDS_PER_EMAIL == 15


@pytest.mark.parametrize("devices", [(), None], ids=["none", "unknown"])
def test_no_gpu_or_unknown_devices_take_twice_as_long(
    devices: tuple[GpuDevice, ...] | None,
) -> None:
    assessment = assess(None, devices)
    assert assessment.gpus == ()
    assert assessment.seconds_per_email == CPU_SECONDS_PER_EMAIL == 30
    assert not assessment.low_memory


def test_the_summary_names_the_gpu() -> None:
    assert summary(assess(16 * GIB, (RADEON,))) == (
        "About 15 seconds per email on this computer (AMD Radeon(TM) 780M)."
    )


def test_the_summary_says_no_gpu_was_found() -> None:
    assert summary(assess(None, ())) == (
        "About 30 seconds per email: no graphics processor the model can use was found."
    )


def test_the_summary_warns_about_low_memory_in_whole_gigabytes() -> None:
    # An "8 GB" computer reports a little less to programs.
    text = summary(assess(int(7.8 * GIB), None))
    assert text == (
        "About 30 seconds per email: no graphics processor the model can use was found."
        " This computer has 8 GB of memory; the local model was measured with 16 GB,"
        " so it may be slower or fail to load."
    )


def test_the_summary_for_a_16_gb_computer_has_no_warning() -> None:
    assert "memory" not in summary(assess(int(15.7 * GIB), (APPLE,)))


def test_this_computers_memory_is_read() -> None:
    memory = total_memory_bytes()
    assert type(memory) is int
    assert memory > GIB


def test_memory_that_cant_be_read_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        monkeypatch.setattr(device_module, "_kernel32", refuse)
    elif sys.platform == "darwin":
        monkeypatch.setattr(subprocess, "run", refuse)
    else:
        monkeypatch.setattr(os, "sysconf", refuse)
    assert total_memory_bytes() is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS reads its memory with sysctl")
@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [(0, "17179869184\n", 16 * GIB), (1, "", None), (0, "unknown oid\n", None), (0, "0\n", None)],
)
def test_mac_memory_comes_from_sysctl(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, expected: int | None
) -> None:
    run = FakeRun(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(subprocess, "run", run)
    assert total_memory_bytes() == expected
    assert [command for command, _ in run.calls] == [["/usr/sbin/sysctl", "-n", "hw.memsize"]]
