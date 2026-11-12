from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import qa


def _tree(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")


def test_mirror_found(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/unit/services/test_actions_service.py", "tests/unit/test_other.py")
    tests, notes = qa.tests_for(["src/mailbrief/services/actions.py"], tmp_path)
    assert tests == ["tests/unit/services/test_actions_service.py", qa.SOURCE_RULES]
    assert notes == []


def test_no_mirror_falls_back_to_package_folder(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/unit/services/test_x.py")
    tests, notes = qa.tests_for(["src/mailbrief/services/nothing.py"], tmp_path)
    assert "tests/unit/services" in tests
    assert notes == ["no mirror for src/mailbrief/services/nothing.py: running tests/unit/services"]


def test_no_mirror_and_no_folder(tmp_path: Path) -> None:
    tests, notes = qa.tests_for(["src/mailbrief/branding.py"], tmp_path)
    assert tests == [qa.SOURCE_RULES]
    assert "no tests chosen" in notes[0]


def test_test_file_changed(tmp_path: Path) -> None:
    tests, _ = qa.tests_for(["tests/unit/domain/test_a.py", "tests/conftest.py"], tmp_path)
    assert tests == ["tests/unit/domain/test_a.py", qa.SOURCE_RULES]


def test_migration_changed(tmp_path: Path) -> None:
    tests, notes = qa.tests_for(["src/mailbrief/migrations/versions/0039_x.py"], tmp_path)
    assert qa.MIGRATION_TEST in tests
    assert notes == []


def test_changed_files_merges_diff_and_untracked() -> None:
    calls: list[Sequence[str]] = []

    def git(args: Sequence[str]) -> str:
        calls.append(args)
        return {
            "merge-base": "abc\n",
            "diff": "a.py\nb\\c.py\n",
            "ls-files": "new.py\na.py\n",
        }[args[0]]

    assert qa.changed_files("main", git) == ["a.py", "b/c.py", "new.py"]
    assert calls[1] == ["diff", "--name-only", "abc"]


def test_junit_summary(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite tests="3">'
        '<testcase classname="tests.a" name="test_ok"/>'
        '<testcase classname="tests.a" name="test_bad"><failure message="assert 1 == 2&#10;more">'
        "trace</failure></testcase>"
        '<testcase classname="tests.b" name="test_err"><error message="boom"/></testcase>'
        "</testsuite></testsuites>"
    )
    count, failures = qa.junit_summary(report)
    assert count == 3
    assert failures == [("tests.a::test_bad", "assert 1 == 2"), ("tests.b::test_err", "boom")]


def test_run_steps_reports_failures_with_injected_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa, "QA_DIR", tmp_path)
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="test_x"><failure message="no"/></testcase>'
        "</testsuite>"
    )
    ticks = iter([0.0, 1.0, 1.0, 3.5])
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], log: Path) -> int:
        seen.append(argv)
        log.write_text("log")
        return 0 if argv[0] == "good" else 1

    steps = [qa.Step("one", ("good",)), qa.Step("pytest", ("bad",), junit)]
    lines = qa.run_steps(steps, runner, lambda: next(ticks))
    assert lines[0] == "ok   one (1.0s)"
    assert lines[1].startswith("FAIL pytest (2.5s) 1 tests, log: ")
    assert lines[2:] == ["  t::test_x", "    no"]
    assert [argv[0] for argv in seen] == ["good", "bad"]


def test_cap() -> None:
    lines = [str(n) for n in range(40)]
    out = qa.cap(lines)
    assert len(out) == qa.MAX_LINES
    assert out[-1] == "... 11 more lines cut"
    assert qa.cap(lines[:5]) == lines[:5]


def test_full_steps_cover_every_platform() -> None:
    names = [step.name for step in qa.full_steps()]
    assert names == [
        "ruff format",
        "ruff check",
        "mypy",
        "mypy win32",
        "mypy darwin",
        "pytest",
        "service gate",
    ]


def test_hotspots_ranks_least_covered_first(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        "<coverage><packages><package><classes>"
        '<class filename="a.py"><lines><line number="1" hits="1"/><line number="2" hits="1"/>'
        "</lines></class>"
        '<class filename="b.py"><lines><line number="1" hits="0"/>'
        '<line number="2" hits="1" condition-coverage="50% (1/2)"/></lines></class>'
        '<class filename="empty.py"><lines/></class>'
        "</classes></package></packages></coverage>"
    )
    rows = qa.hotspot_rows(report, 5)
    assert len(rows) == 2
    assert rows[0].endswith("b.py") and rows[0].startswith(" 50.00%")
    assert rows[1].endswith("a.py") and "100.00%" in rows[1]


def test_main_area_uses_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa, "QA_DIR", tmp_path)
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], log: Path) -> int:
        seen.append(argv)
        return 0

    assert qa.main(["area", "tests/unit/domain"], runner) == 0
    assert "--no-cov" in seen[0] and "tests/unit/domain" in seen[0]
    assert capsys.readouterr().out.startswith("ok   pytest")
