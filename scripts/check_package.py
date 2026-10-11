"""Run a frozen package away from the checkout, using only disposable test data."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.package_manifest import (  # noqa: E402
    archive_inventory,
    audit_names,
    require_resources,
    require_x64,
    verify_manifest,
)


def audit_distribution(directory: Path) -> None:
    """Only inspect generated artifacts, never developer credentials or real profiles."""
    audit_names(str(path.relative_to(directory)) for path in directory.rglob("*"))


def stop_process(process: subprocess.Popen[str], data: Path) -> None:
    """Stop only the spawned check and its bootloader child on Windows."""
    if process.poll() is None:
        if sys.platform == "win32":
            child = data / f"child-{process.pid}.json"
            if child.is_file():
                pid = json.loads(child.read_text("utf-8"))["pid"]
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                    return
                except subprocess.TimeoutExpired:
                    pass
            else:
                # A bootloader/import failure can occur before Python writes its PID.
                # Kill the still-owned process tree, including any frozen error dialog.
                with suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)


def start_process(
    executable: Path,
    data: Path,
    cwd: Path,
    environment: dict[str, str],
    scenario: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(executable), "--smoke-test-dir", str(data), "--smoke-scenario", scenario],
        cwd=cwd,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )


def run_check(
    executable: Path,
    data: Path,
    cwd: Path,
    environment: dict[str, str],
    scenario: str = "full",
    *,
    acquired: bool | None = None,
) -> None:
    report = data / "smoke-result.json"
    report.unlink(missing_ok=True)
    started = time.monotonic()
    process = start_process(executable, data, cwd, environment, scenario)
    try:
        code = process.wait(timeout=180)
    finally:
        stop_process(process, data)
    if code or not report.is_file():
        raise ValueError(f"Package scenario {scenario} failed (exit {code}).")
    expected: dict[str, object] = (
        {"ok": True, "platform": sys.platform, "launches": 2}
        if scenario == "full"
        else {"ok": True, "scenario": scenario}
    )
    if acquired is not None:
        expected["acquired"] = acquired
    if json.loads(report.read_text("utf-8")) != expected:
        raise ValueError(f"Package scenario {scenario} returned an unexpected report.")
    print(
        f"Passed {scenario} ({environment['QT_QPA_PLATFORM']}, "
        f"scale {environment['QT_SCALE_FACTOR']}): {time.monotonic() - started:.1f}s"
    )


def check_locks(
    executable: Path,
    renamed: Path,
    data: Path,
    cwd: Path,
    environment: dict[str, str],
) -> None:
    """Exercise live, gracefully released and dead-process locks using renamed copies."""
    for force_stop in (False, True):
        ready = data / "lock-ready.json"
        ready.unlink(missing_ok=True)
        (data / "stop-lock").unlink(missing_ok=True)
        holder = start_process(executable, data, cwd, environment, "hold-lock")
        try:
            deadline = time.monotonic() + 150
            while not ready.exists():
                if holder.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError("Package lock holder failed to become ready.")
                time.sleep(0.1)
            run_check(renamed, data, cwd, environment, "try-lock", acquired=False)
            if force_stop:
                stop_process(holder, data)
            else:
                (data / "stop-lock").touch()
                if holder.wait(timeout=30):
                    raise ValueError("Package lock holder failed to shut down.")
        finally:
            stop_process(holder, data)
        run_check(renamed, data, cwd, environment, "try-lock", acquired=True)
        run_check(renamed, data, cwd, environment, "verify")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--layout", choices=("onedir", "onefile"), default="onedir")
    options = parser.parse_args()
    if sys.platform == "win32":
        default = root / (
            "out/dist/onefile/MailBrief.exe"
            if options.layout == "onefile"
            else "out/dist/MailBrief/MailBrief.exe"
        )
    elif sys.platform == "darwin" and options.layout == "onedir":
        default = root / "out/dist/MailBrief.app/Contents/MacOS/MailBrief"
    else:
        parser.error("Package checks require Windows or a macOS app bundle.")
    executable = (options.executable or default).resolve(strict=True)
    audit_distribution(executable.parent if sys.platform == "win32" else executable.parents[2])
    if options.layout == "onefile":
        verify_manifest(root, executable)
        require_x64(executable)
        entries = archive_inventory(executable)
        require_resources(
            (entry["name"] for entry in entries),
            (path.name for path in (root / "migrations/versions").glob("*.py")),
        )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("MAILBRIEF_", "QT_", "PYSIDE_"))
        and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="MailBrief package check ") as temporary:
        directory = Path(temporary)
        data, cwd, extraction = (directory / name for name in ("profile", "working", "extraction"))
        for target in (data, cwd, extraction):
            target.mkdir()
        environment.update(TEMP=str(extraction), TMP=str(extraction))
        launched = executable
        if options.layout == "onefile":
            location = directory / "Desktop café 测试"
            location.mkdir()
            launched = location / "MailBrief.exe"
            shutil.copy2(executable, launched)
        for scale in ("1", "2"):
            environment["QT_SCALE_FACTOR"] = scale
            if (data / "smoke.sqlite3").exists():
                run_check(launched, data, cwd, environment, "verify")
            run_check(launched, data, cwd, environment)
            run_check(launched, data, cwd, environment, "verify")
        if options.layout == "onefile":
            if list(extraction.glob("_MEI*")):
                raise ValueError("Normal exit left an extraction directory behind.")
            environment["QT_QPA_PLATFORM"] = "windows"
            environment["QT_SCALE_FACTOR"] = "1"
            run_check(launched, data, cwd, environment)
            renamed = launched.with_name("MailBrief renamed.exe")
            shutil.copy2(launched, renamed)
            check_locks(launched, renamed, data, cwd, environment)
            upgrade = directory / "upgrade"
            upgrade.mkdir()
            run_check(renamed, upgrade, cwd, environment, "upgrade")
    print("Package checks passed using disposable data only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
