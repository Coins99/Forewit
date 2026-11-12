"""Quiet check runner for agents: one short report, full logs under scratch/qa/.

Commands: ``changed [--base main]``, ``area <test paths>``, ``full`` and
``hotspots [-n 15]``. See docs/qa.md. Standard library only; every tool runs as
``sys.executable -m <tool>``.
"""

import argparse
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as ``python scripts/qa.py``
    sys.path.insert(0, str(ROOT))
from scripts.check_coverage import combined_counts  # noqa: E402

QA_DIR = ROOT / "scratch" / "qa"
SOURCE_RULES = "tests/unit/test_source_rules.py"
MAX_LINES = 30
MAX_FAILURES = 10
MAX_EXCERPT = 3
MIGRATION_TEST = "tests/unit/storage/test_migrations.py"

Runner = Callable[[Sequence[str], Path, Mapping[str, str]], int]
"""Runs a command with extra environment, writes its output to the log, returns the exit code."""
Git = Callable[[Sequence[str]], str]
MAX_NOTES = 4
PLATFORM: str = sys.platform


class GitError(RuntimeError):
    """A git query failed, so the changed files are unknown."""


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    junit: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    remove: tuple[Path, ...] = ()


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    seconds: float
    log: Path
    detail: str = ""


def py(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", *args)


def default_runner(argv: Sequence[str], log: Path, extra: Mapping[str, str]) -> int:
    env = dict(os.environ)
    env.update(extra)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as handle:
        return subprocess.run(
            list(argv), cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        ).returncode


def default_git(args: Sequence[str]) -> str:
    done = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, encoding="utf-8"
    )
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def changed_files(base: str, git: Git) -> list[str]:
    """Files that differ from ``base`` (from the merge base) plus uncommitted and new ones."""
    anchor = git(["merge-base", base, "HEAD"]).strip()
    names = git(["diff", "--name-only", anchor]).splitlines()
    names += git(["ls-files", "--others", "--exclude-standard"]).splitlines()
    return sorted({name.strip().replace("\\", "/") for name in names if name.strip()})


def tests_for(files: Sequence[str], root: Path = ROOT) -> tuple[list[str], list[str]]:
    """The test files to run for changed files, and notes about fallbacks."""
    tests: set[str] = {SOURCE_RULES}
    notes: list[str] = []
    tests_root = root / "tests"
    for name in files:
        path = Path(name)
        if path.suffix != ".py" or not (root / name).exists():
            continue  # not Python, or deleted
        if path.parts[0] == "tests":
            if path.name.startswith("test_"):
                tests.add(name)
            else:
                folder = path.parent.as_posix()
                tests.add(folder)
                notes.append(f"test helper {name}: running {folder}")
            continue
        if "migrations/versions/" in name:
            tests.add(MIGRATION_TEST)
            continue
        if path.parts[:2] == ("src", "mailbrief") or path.parts[0] == "scripts":
            init = path.name == "__init__.py"
            mirrors = [] if init else sorted(tests_root.rglob(f"test_{path.stem}*.py"))
            if mirrors:
                tests.update(m.relative_to(root).as_posix() for m in mirrors)
                continue
            fallback = _fallback_folder(path, root)
            if fallback is not None:
                tests.add(fallback)
                notes.append(f"no mirror for {name}: running {fallback}")
            elif init:
                notes.append(f"{name}: no tests chosen")
            else:
                notes.append(f"no mirror for {name}: no tests chosen")
    return sorted(tests), notes


def _fallback_folder(path: Path, root: Path) -> str | None:
    if path.parts[:2] != ("src", "mailbrief") or len(path.parts) < 4:
        return None
    package = path.parts[2]
    for candidate in (f"tests/{package}", f"tests/unit/{package}"):
        if (root / candidate).is_dir():
            return candidate
    return None


def junit_summary(report: Path) -> tuple[int, int, list[tuple[str, str]]]:
    """Tests, skipped tests and the failing ``(id, one message line)`` pairs of a junit file."""
    count = skipped = 0
    failures: list[tuple[str, str]] = []
    for case in ET.parse(report).iter("testcase"):
        count += 1
        skipped += int(case.find("skipped") is not None)
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        text = (bad.attrib.get("message") or bad.text or "").strip()
        first = text.splitlines()[0] if text else ""
        test_id = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        failures.append((test_id, first[:140]))
    return count, skipped, failures


def pytest_step(
    paths: Sequence[str],
    *,
    coverage: bool,
    parallel: bool,
    name: str = "pytest",
    extra: Sequence[str] = (),
    env: tuple[tuple[str, str], ...] = (),
    remove: tuple[Path, ...] = (),
) -> Step:
    junit = QA_DIR / f"{name.replace(' ', '-')}.xml"
    args = ["pytest", "-q", f"--junitxml={junit}", *extra]
    if not coverage:
        args.append("--no-cov")
    if parallel:
        args += ["-n", "auto"]
    return Step(name, py(*args, *paths), junit, env, remove)


def run_steps(steps: Sequence[Step], runner: Runner, clock: Callable[[], float]) -> list[str]:
    lines: list[str] = []
    for step in steps:
        log = QA_DIR / f"{step.name.replace(' ', '-')}.log"
        for stale in (*step.remove, *([step.junit] if step.junit else [])):
            stale.unlink(missing_ok=True)
        started = clock()
        code = runner(step.argv, log, dict(step.env))
        seconds = clock() - started
        lines += _report(step, code, seconds, log)
    return lines


def macos_note(lines: Sequence[str]) -> list[str]:
    """Qt tests skip on macOS without a GUI session; say so instead of a quiet ok."""
    if PLATFORM != "darwin" or not any("skipped" in line for line in lines):
        return []
    return [
        "note: Qt tests were skipped on macOS; CI runs them with MACOS_GUI_AVAILABLE=1 "
        "QT_QPA_PLATFORM=cocoa (`full` does the same)"
    ]


def _report(step: Step, code: int, seconds: float, log: Path) -> list[str]:
    label = f"{step.name} ({seconds:.1f}s)"
    shown = _rel(log)
    count = skipped = 0
    failures: list[tuple[str, str]] = []
    if step.junit is not None and step.junit.exists():
        count, skipped, failures = junit_summary(step.junit)
    extra = f" {count} tests" if step.junit is not None and count else ""
    if skipped:
        extra += f", {skipped} skipped"
    if code == 0:
        return [f"ok   {label}{extra}"]
    lines = [f"FAIL {label}{extra}, log: {shown}"]
    for test_id, message in failures[:MAX_FAILURES]:
        lines.append(f"  {test_id}")
        if message:
            lines.append(f"    {message}")
    if len(failures) > MAX_FAILURES:
        lines.append(f"  ... {len(failures) - MAX_FAILURES} more failures in the log")
    if not failures:
        lines += [f"  {text[:140]}" for text in _tail(log, MAX_EXCERPT)]
    return lines


def _tail(log: Path, count: int) -> list[str]:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()][-count:]


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def cap(lines: Sequence[str]) -> list[str]:
    if len(lines) <= MAX_LINES:
        return list(lines)
    return [*lines[: MAX_LINES - 1], f"... {len(lines) - MAX_LINES + 1} more lines cut"]


def cap_notes(notes: Sequence[str]) -> list[str]:
    if len(notes) <= MAX_NOTES:
        return list(notes)
    return [*notes[: MAX_NOTES - 1], f"... {len(notes) - MAX_NOTES + 1} more notes"]


def changed_steps(files: Sequence[str], tests: Sequence[str]) -> list[Step]:
    pys = [name for name in files if name.endswith(".py") and (ROOT / name).exists()]
    steps: list[Step] = []
    if pys:
        steps.append(Step("ruff format", py("ruff", "format", "--check", *pys)))
        steps.append(Step("ruff check", py("ruff", "check", *pys)))
        # Whole tree: a changed signature breaks callers in files that did not change.
        steps.append(Step("mypy", py("mypy", "--no-pretty", "src", "tests")))
    parallel = len(tests) > 3 or any((ROOT / path).is_dir() for path in tests)
    steps.append(pytest_step(tests, coverage=False, parallel=parallel))
    return steps


def full_steps() -> list[Step]:
    steps = [
        Step("ruff format", py("ruff", "format", "--check", ".")),
        Step("ruff check", py("ruff", "check", ".")),
        Step("mypy", py("mypy", "--no-pretty", "src", "tests")),
    ]
    for platform in ("win32", "darwin"):
        steps.append(
            Step(
                f"mypy {platform}",
                py("mypy", "--no-pretty", "--platform", platform, "src", "tests"),
            )
        )
    report = ROOT / "coverage.xml"
    if PLATFORM == "darwin":
        # As CI: the Qt tests run in a second, serial pass that adds its coverage.
        steps.append(
            pytest_step(
                [], coverage=True, parallel=True, extra=["--cov-fail-under=0"], remove=(report,)
            )
        )
        steps.append(
            pytest_step(
                ["tests/ui", "tests/unit/test_app.py"],
                coverage=True,
                parallel=False,
                name="pytest cocoa",
                extra=["--cov-append"],
                env=(("MACOS_GUI_AVAILABLE", "1"), ("QT_QPA_PLATFORM", "cocoa")),
            )
        )
    else:
        steps.append(pytest_step([], coverage=True, parallel=True, remove=(report,)))
    steps.append(Step("service gate", (sys.executable, "scripts/check_coverage.py")))
    return steps


def hotspot_rows(report: Path, count: int) -> list[str]:
    rows: list[tuple[float, int, int, str]] = []
    for module in ET.parse(report).iter("class"):
        covered, total = combined_counts(module)
        if total:
            name = module.attrib["filename"].replace("\\", "/")
            rows.append((covered / total, total - covered, total, name))
    rows.sort()
    return [
        f"{100 * ratio:6.2f}%  {missed:5d} missed of {total:5d}  {name}"
        for ratio, missed, total, name in rows[:count]
    ]


def hotspot_header(report: Path) -> list[str]:
    """Where the numbers come from, with a warning when they look partial."""
    modules = sum(1 for _ in ET.parse(report).iter("class"))
    source = sum(
        1 for path in (ROOT / "src" / "mailbrief").rglob("*.py") if path.name != "__init__.py"
    )
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.stat().st_mtime))
    lines = [f"coverage.xml of {stamp}: {modules} modules"]
    if modules < source // 2:
        lines.append("warning: partial report (a single-path pytest run?); run `qa.py full`")
    return lines


def main(
    argv: Sequence[str] | None = None, runner: Runner = default_runner, git: Git = default_git
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    changed = sub.add_parser("changed")
    changed.add_argument("--base", default="main")
    area = sub.add_parser("area")
    area.add_argument("paths", nargs="+")
    sub.add_parser("full")
    hot = sub.add_parser("hotspots")
    hot.add_argument("-n", type=int, default=15)
    args = parser.parse_args(argv)

    if args.command == "hotspots":
        report = ROOT / "coverage.xml"
        if not report.exists():
            print("no coverage.xml: run `qa.py full` first")
            return 1
        print("\n".join(cap([*hotspot_header(report), *hotspot_rows(report, args.n)])))
        return 0

    notes: list[str] = []
    if args.command == "changed":
        try:
            files = changed_files(args.base, git)
        except GitError as error:
            print(f"FAIL {error}")
            return 2
        tests, notes = tests_for(files)
        steps = changed_steps(files, tests)
        notes.insert(0, f"{len(files)} changed files, {len(tests)} test paths")
    elif args.command == "area":
        steps = [pytest_step(args.paths, coverage=False, parallel=True)]
    else:
        steps = full_steps()
    lines = run_steps(steps, runner, time.monotonic)
    notes += macos_note(lines)
    print("\n".join(cap([*cap_notes(notes), *lines])))
    return int(any(line.startswith("FAIL") for line in lines))


if __name__ == "__main__":
    raise SystemExit(main())
