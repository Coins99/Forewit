from collections.abc import Mapping, Sequence
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
    _tree(tmp_path, "src/mailbrief/services/actions.py")
    tests, notes = qa.tests_for(["src/mailbrief/services/actions.py"], tmp_path)
    assert tests == ["tests/unit/services/test_actions_service.py", qa.SOURCE_RULES]
    assert notes == []


def test_no_mirror_falls_back_to_package_folder(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/unit/services/test_x.py")
    _tree(tmp_path, "src/mailbrief/services/nothing.py")
    tests, notes = qa.tests_for(["src/mailbrief/services/nothing.py"], tmp_path)
    assert "tests/unit/services" in tests
    assert notes == ["no mirror for src/mailbrief/services/nothing.py: running tests/unit/services"]


def test_no_mirror_and_no_folder(tmp_path: Path) -> None:
    _tree(tmp_path, "src/mailbrief/branding.py")
    tests, notes = qa.tests_for(["src/mailbrief/branding.py"], tmp_path)
    assert tests == [qa.SOURCE_RULES]
    assert "no tests chosen" in notes[0]


def test_test_file_changed(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/unit/domain/test_a.py")
    tests, notes = qa.tests_for(["tests/unit/domain/test_a.py"], tmp_path)
    assert tests == ["tests/unit/domain/test_a.py", qa.SOURCE_RULES]
    assert notes == []


def test_deleted_files_choose_nothing(tmp_path: Path) -> None:
    tests, notes = qa.tests_for(
        ["tests/unit/test_gone.py", "src/mailbrief/services/gone.py"], tmp_path
    )
    assert tests == [qa.SOURCE_RULES]
    assert notes == []


def test_helpers_and_inits_say_what_they_run(tmp_path: Path) -> None:
    _tree(tmp_path, "tests/factories.py", "src/mailbrief/ui/__init__.py", "tests/ui/test_a.py")
    _tree(tmp_path, "src/mailbrief/__init__.py")
    tests, notes = qa.tests_for(
        ["tests/factories.py", "src/mailbrief/ui/__init__.py", "src/mailbrief/__init__.py"],
        tmp_path,
    )
    assert tests == ["tests", "tests/ui", qa.SOURCE_RULES]
    assert len(notes) == 3 and "no tests chosen" in notes[-1]


def test_git_failure_fails_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    def git(args: Sequence[str]) -> str:
        raise qa.GitError("git merge-base nope HEAD failed: bad revision")

    assert qa.main(["changed", "--base", "nope"], git=git) == 2
    assert capsys.readouterr().out.startswith("FAIL git merge-base")


def test_cap_notes() -> None:
    notes = [str(n) for n in range(7)]
    assert qa.cap_notes(notes) == ["0", "1", "2", "... 4 more notes"]
    assert qa.cap_notes(notes[:2]) == ["0", "1"]


def test_migration_changed(tmp_path: Path) -> None:
    _tree(tmp_path, "src/mailbrief/migrations/versions/0039_x.py")
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
    count, skipped, failures = qa.junit_summary(report)
    assert (count, skipped) == (3, 0)
    assert failures == [("tests.a::test_bad", "assert 1 == 2"), ("tests.b::test_err", "boom")]


def test_run_steps_reports_failures_with_injected_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa, "QA_DIR", tmp_path)
    junit = tmp_path / "junit.xml"
    sample = (
        '<testsuite><testcase classname="t" name="test_x"><failure message="no"/></testcase>'
        "</testsuite>"
    )
    ticks = iter([0.0, 1.0, 1.0, 3.5])
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], log: Path, env: Mapping[str, str]) -> int:
        seen.append(argv)
        log.write_text("log")
        if argv[0] == "bad":
            junit.write_text(sample)
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


def test_full_steps_cover_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa, "PLATFORM", "win32")
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

    def runner(argv: Sequence[str], log: Path, env: Mapping[str, str]) -> int:
        seen.append(argv)
        return 0

    assert qa.main(["area", "tests/unit/domain"], runner) == 0
    assert "--no-cov" in seen[0] and "tests/unit/domain" in seen[0]
    assert capsys.readouterr().out.startswith("ok   pytest")


def test_stale_junit_is_removed_and_skips_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa, "QA_DIR", tmp_path)
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite><testcase classname="t" name="old"><failure message="x"/>')
    coverage = tmp_path / "coverage.xml"
    coverage.write_text("old")

    def crash(argv: Sequence[str], log: Path, env: Mapping[str, str]) -> int:
        log.write_text("Segmentation fault")
        return 1

    step = qa.Step("pytest", ("x",), junit, remove=(coverage,))
    lines = qa.run_steps([step], crash, lambda: 0.0)
    assert not coverage.exists()
    assert lines[1:] == ["  Segmentation fault"]

    def skipping(argv: Sequence[str], log: Path, env: Mapping[str, str]) -> int:
        junit.write_text(
            '<testsuite><testcase classname="t" name="a"><skipped/></testcase>'
            '<testcase classname="t" name="b"/></testsuite>'
        )
        return 0

    assert qa.run_steps([step], skipping, lambda: 0.0) == ["ok   pytest (0.0s) 2 tests, 1 skipped"]


def test_hotspot_header_warns_on_a_partial_report(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text('<coverage><class filename="a.py"><lines/></class></coverage>')
    lines = qa.hotspot_header(report)
    assert lines[0].endswith(": 1 modules")
    assert "partial" in lines[1]


def test_macos_runs_the_qt_tests_like_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa, "PLATFORM", "darwin")
    names = [step.name for step in qa.full_steps()]
    assert names[-3:] == ["pytest", "pytest cocoa", "service gate"]
    assert qa.macos_note(["ok   pytest (1.0s) 3 tests, 2 skipped"])
    assert qa.macos_note(["ok   pytest (1.0s) 3 tests"]) == []
