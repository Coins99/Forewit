"""The harness: passes, the long probe, the server's failure path and the reports."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from mailbrief.domain.analysis import AnalysisRequest, AnalysisResponse
from mailbrief.domain.bodies import MAX_ANALYSIS_CHARS
from scripts.local_ai import benchmark
from scripts.local_ai.benchmark import (
    BaselineAnalyzer,
    LlamaServer,
    build_report,
    percentile,
    probe_request,
    render_comparison,
    render_markdown,
    run_analyzer,
    run_pass,
)
from scripts.local_ai.fixture import DEFAULT_FIXTURE, load_fixture
from scripts.local_ai.local_model import CallTiming, LocalModelError

FIXTURE = load_fixture()


class FailingAnalyzer:
    async def analyze(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]:
        raise LocalModelError("the local server answered HTTP 500")


@pytest.fixture(autouse=True)
def no_device_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "device", lambda: {"platform": "test"})


async def test_a_pass_scores_every_message_in_batches() -> None:
    run = await run_pass(0, FIXTURE, BaselineAnalyzer(), batch_size=3)
    assert [item.key for item in run.scores] == [m.key for m in FIXTURE.messages]
    assert [len(call.keys) for call in run.calls] == [3] * 6 + [2]
    assert all(call.visible_after_seconds <= run.seconds for call in run.calls)


async def test_a_failed_call_marks_its_messages_and_the_pass_goes_on() -> None:
    run = await run_pass(0, FIXTURE, FailingAnalyzer(), batch_size=10)
    assert {item.status for item in run.scores} == {"error"}
    assert [call.error for call in run.calls] == ["LocalModelError"] * 2


def test_the_probe_is_one_email_at_the_length_limit() -> None:
    request = probe_request(FIXTURE)
    assert len(request.body_text) <= MAX_ANALYSIS_CHARS < len(request.body_text) + 200
    assert request.body_truncated and request.message_key == "probe"


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.0
    assert percentile([float(n) for n in range(1, 21)], 0.95) == 19.0


def test_a_server_that_exits_at_once_is_reported(tmp_path: Path) -> None:
    server = LlamaServer(
        Path(sys.executable),
        Path("no_such_module_for_the_benchmark"),  # "python -m" it: exits at once.
        port=8099,
        context=512,
        gpu_layers=0,
        threads=None,
        extra=[],
        log_path=tmp_path / "server.log",
    )
    with pytest.raises(RuntimeError, match="exited"), server:
        pass
    assert "No module named" in (tmp_path / "server.log").read_text(encoding="utf-8")


async def test_reports_render_as_markdown_tables() -> None:
    passes, probe = await run_analyzer(
        FIXTURE, BaselineAnalyzer(), repeats=2, batch_size=1, long_probe=True
    )
    report = build_report(
        label="baseline",
        system="baseline",
        fixture_path=DEFAULT_FIXTURE,
        fixture=FIXTURE,
        passes=passes,
        probe=probe,
        settings={},
    )
    report = json.loads(json.dumps(report))  # What compare reads back from report.json.
    page = render_markdown(report)
    assert "| Valid analyses (pass the app's checks) | 20/20 (100%) |" in page
    assert page.count("\n| m") == 20
    table = render_comparison([report, report | {"label": "copy"}])
    assert table.startswith("| Measure | baseline | copy |")
    assert "| Non-loopback requests | none (no network use) | none (no network use) |" in table


def test_baseline_command_writes_both_reports(tmp_path: Path) -> None:
    assert benchmark.main(["baseline", "--repeats", "1", "--out", str(tmp_path)]) == 0
    (out,) = tmp_path.iterdir()
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["system"] == "baseline"
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Local AI benchmark")


def test_a_remote_base_url_is_refused_before_any_request(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="localhost"):
        benchmark.main(
            ["model", "--base-url", "https://api.groq.com/openai", "--model", "x"]
            + ["--out", str(tmp_path)]
        )
