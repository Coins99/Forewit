"""The harness: passes, the long probe, the server's command line and the reports."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from mailbrief.domain.analysis import AnalysisRequest, AnalysisResponse
from mailbrief.domain.bodies import MAX_ANALYSIS_CHARS
from mailbrief.ports.errors import ProviderUnavailableError
from mailbrief.providers.local.provider import CallTiming
from scripts.local_ai import benchmark
from scripts.local_ai.benchmark import (
    BaselineAnalyzer,
    budget_problem,
    build_report,
    percentile,
    probe_request,
    public_command,
    render_comparison,
    render_markdown,
    run_analyzer,
    run_pass,
    weighted_rate,
)
from scripts.local_ai.fixture import DEFAULT_FIXTURE, load_fixture

FIXTURE = load_fixture()


class FailingAnalyzer:
    async def analyze_timed(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]:
        raise ProviderUnavailableError("The local model failed (HTTP 500).", http_status=500)


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
    assert [call.error for call in run.calls] == ["ProviderUnavailableError"] * 2


def test_the_probe_is_one_email_at_the_length_limit() -> None:
    request = probe_request(FIXTURE)
    assert len(request.body_text) <= MAX_ANALYSIS_CHARS < len(request.body_text) + 200
    assert request.body_truncated and request.message_key == "probe"


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.0
    assert percentile([float(n) for n in range(1, 21)], 0.95) == 19.0


def test_reports_never_keep_the_servers_key_or_the_owners_folders() -> None:
    command = [
        sys.executable,
        "-m",
        str(Path.cwd() / "model.gguf"),
        "--api-key",
        "per-launch-key",
        "--api-key=other",
        "--lora",
        str(Path.cwd() / "adapter.gguf"),
    ]
    shown = public_command(command)
    assert "per-launch-key" not in " ".join(shown) and "other" not in " ".join(shown)
    assert shown[0] == Path(sys.executable).name and shown[2] == "model.gguf"
    assert shown[-1] == "adapter.gguf" and "--api-key=<hidden>" in shown


def test_rates_weigh_each_call_by_its_tokens() -> None:
    assert weighted_rate([(380, 12.0), (20, 20.0)]) == pytest.approx(400 / (380 / 12 + 1))
    assert weighted_rate([(None, 12.0), (0, 5.0), (10, None)]) is None


def test_batches_that_overflow_the_budget_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert budget_problem(1, 8192, 2048) is None
    assert budget_problem(3, 8192, 2048) == "--batch-size 3 needs --context of at least 9048"
    answer = budget_problem(5, None, 2048)
    assert answer == "--batch-size 5 needs --max-output-tokens of at least 3000"
    with pytest.raises(SystemExit):
        benchmark.main(["model", "--server", "x", "--model-file", "m.gguf", "--batch-size", "3"])
    assert "--context of at least 9048" in capsys.readouterr().err


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
