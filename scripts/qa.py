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
from collections.abc import Callable, Sequence
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

Runner = Callable[[Sequence[str], Path], int]
"""Runs a command, writes its combined output to the log path, returns the exit code."""
Git = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    junit: Path | None = None


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    seconds: float
    log: Path
    detail: str = ""


def py(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", *args)


def default_runner(argv: Sequence[str], log: Path) -> int:
    env = dict(os.environ)
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
    return done.stdout if done.returncode == 0 else ""


def changed_files(base: str, git: Git) -> list[str]:
    """Files that differ from ``base`` (from the merge base) plus uncommitted and new ones."""
    anchor = git(["merge-base", base, "HEAD"]).strip() or base
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
        if path.suffix != ".py" or path.name == "__init__.py":
            continue
        if path.parts[0] == "tests":
            if path.name.startswith("test_"):
                tests.add(name)
            continue
        if "migrations/versions/" in name:
            tests.add(MIGRATION_TEST)
            continue
        if path.parts[:2] == ("src", "mailbrief") or path.parts[0] == "scripts":
            mirrors = sorted(tests_root.rglob(f"test_{path.stem}*.py"))
            if mirrors:
                tests.update(m.relative_to(root).as_posix() for m in mirrors)
                continue
            fallback = _fallback_folder(path, root)
            if fallback is not None:
                tests.add(fallback)
                notes.append(f"no mirror for {name}: running {fallback}")
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


def junit_summary(report: Path) -> tuple[int, list[tuple[str, str]]]:
    """Number of tests and the failing ``(id, one message line)`` pairs of a junit file."""
    count = 0
    failures: list[tuple[str, str]] = []
    for case in ET.parse(report).iter("testcase"):
        count += 1
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        text = (bad.attrib.get("message") or bad.text or "").strip()
        first = text.splitlines()[0] if text else ""
        test_id = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        failures.append((test_id, first[:140]))
    return count, failures


def pytest_step(paths: Sequence[str], *, coverage: bool, parallel: bool) -> Step:
    junit = QA_DIR / "junit.xml"
    args = ["pytest", "-q", f"--junitxml={junit}"]
    if not coverage:
        args.append("--no-cov")
    if parallel:
        args += ["-n", "auto"]
    return Step("pytest", py(*args, *paths), junit)


def run_steps(steps: Sequence[Step], runner: Runner, clock: Callable[[], float]) -> list[str]:
    lines: list[str] = []
    for step in steps:
        log = QA_DIR / f"{step.name.replace(' ', '-')}.log"
        started = clock()
        code = runner(step.argv, log)
        seconds = clock() - started
        lines += _report(step, code, seconds, log)
    return lines


def _report(step: Step, code: int, seconds: float, log: Path) -> list[str]:
    label = f"{step.name} ({seconds:.1f}s)"
    shown = _rel(log)
    count = 0
    failures: list[tuple[str, str]] = []
    if step.junit is not None and step.junit.exists():
        count, failures = junit_summary(step.junit)
    extra = f" {count} tests" if step.junit is not None and count else ""
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


def changed_steps(files: Sequence[str], tests: Sequence[str]) -> list[Step]:
    pys = [name for name in files if name.endswith(".py") and (ROOT / name).exists()]
    steps: list[Step] = []
    if pys:
        steps.append(Step("ruff format", py("ruff", "format", "--check", *pys)))
        steps.append(Step("ruff check", py("ruff", "check", *pys)))
        steps.append(Step("mypy", py("mypy", "--no-pretty", *pys)))
    steps.append(pytest_step(tests, coverage=False, parallel=len(tests) > 3))
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
    steps.append(pytest_step([], coverage=True, parallel=True))
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
        print("\n".join(cap(hotspot_rows(report, args.n))))
        return 0

    notes: list[str] = []
    if args.command == "changed":
        files = changed_files(args.base, git)
        tests, notes = tests_for(files)
        steps = changed_steps(files, tests)
        notes.insert(0, f"{len(files)} changed files, {len(tests)} test paths")
    elif args.command == "area":
        steps = [pytest_step(args.paths, coverage=False, parallel=True)]
    else:
        steps = full_steps()
    lines = run_steps(steps, runner, time.monotonic)
    print("\n".join(cap([*notes, *lines])))
    return int(any(line.startswith("FAIL") for line in lines))


if __name__ == "__main__":
    raise SystemExit(main())
