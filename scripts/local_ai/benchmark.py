"""Run the P0-B1 benchmark over the synthetic overnight fixture and write its report.

    python -m scripts.local_ai.benchmark baseline
    python -m scripts.local_ai.benchmark model --server PATH/llama-server --model-file M.gguf
    python -m scripts.local_ai.benchmark model --base-url http://127.0.0.1:11434 --model NAME
    python -m scripts.local_ai.benchmark compare out/local-ai-benchmark/*/report.json

Use synthetic messages only: a report holds every analysis, and so text derived from the mail.
The first pass after the server starts is cold; later passes are warm (prompt cache filled).
"""

import argparse
import asyncio
import datetime as dt
import json
import math
import secrets
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Protocol, Self

import httpx

from mailbrief.domain.analysis import AnalysisRequest, AnalysisResponse
from mailbrief.domain.bodies import MAX_ANALYSIS_CHARS
from mailbrief.text.prepare import truncate_at_boundary
from scripts.local_ai import baseline
from scripts.local_ai.fixture import DEFAULT_FIXTURE, Fixture, fixture_digest, load_fixture
from scripts.local_ai.local_model import (
    CallTiming,
    LocalModel,
    LocalModelError,
    LoopbackGuard,
    require_loopback,
)
from scripts.local_ai.resources import (
    UNKNOWN,
    BatterySample,
    ProcessUsage,
    battery_sample,
    device,
    finished_children_usage,
    process_usage,
)
from scripts.local_ai.scoring import MessageScore, Ratio, outcomes, quality, score
from scripts.package_manifest import sha256

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "out" / "local-ai-benchmark"
HARNESS_VERSION = 2
HEALTH_TIMEOUT_SECONDS = 600.0
PROBE_KEY = "probe"
# llama-server flags whose value is a secret, kept out of reports.
SECRET_FLAGS = frozenset({"--api-key", "--api-key-file", "--hf-token", "-hft"})
# A conservative context budget per call: the instructions, each email at the 8,000-character
# limit (about 1,700 tokens measured) and the answer.
INSTRUCTION_TOKENS = 1_000
EMAIL_TOKENS = 2_000
ANSWER_TOKENS_PER_EMAIL = 600


class Analyzer(Protocol):
    async def analyze(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]: ...


class BaselineAnalyzer:
    """The rule-based baseline behind the same interface as a model."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock

    async def analyze(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]:
        started = self._clock()
        candidates = tuple(baseline.analyze(request) for request in requests)
        seconds = self._clock() - started
        return AnalysisResponse(candidates=candidates), CallTiming(seconds=seconds)


@dataclass(frozen=True, slots=True)
class CallRecord:
    keys: tuple[str, ...]
    timing: CallTiming | None  # None when the call failed.
    visible_after_seconds: float  # From the pass's start until these results were ready.
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Pass:
    index: int
    seconds: float
    calls: tuple[CallRecord, ...]
    scores: tuple[MessageScore, ...]


async def run_pass(
    index: int,
    fixture: Fixture,
    analyzer: Analyzer,
    *,
    batch_size: int,
    clock: Callable[[], float] = time.perf_counter,
) -> Pass:
    """Every fixture message once, in order, ``batch_size`` messages per call."""
    started = clock()
    calls: list[CallRecord] = []
    scores: list[MessageScore] = []
    messages = fixture.messages
    for first in range(0, len(messages), batch_size):
        batch = messages[first : first + batch_size]
        requests = [fixture.request(message) for message in batch]
        response: AnalysisResponse | None = None
        timing: CallTiming | None = None
        error: str | None = None
        try:
            response, timing = await analyzer.analyze(requests)
        except (LocalModelError, httpx.HTTPError) as exc:
            error = type(exc).__name__
        keys = tuple(request.message_key for request in requests)
        calls.append(CallRecord(keys, timing, clock() - started, error))
        for message, request, outcome in zip(
            batch, requests, outcomes(requests, response), strict=True
        ):
            scores.append(score(message, request, outcome))
    return Pass(index, clock() - started, tuple(calls), tuple(scores))


def probe_request(fixture: Fixture) -> AnalysisRequest:
    """A maximum-length email, the fixture's longest body repeated and cut at the limit, to
    time the worst-case prompt."""
    longest = max(fixture.messages, key=lambda message: len(message.body))
    repeated = "\n\n".join([longest.body] * (MAX_ANALYSIS_CHARS // len(longest.body) + 2))
    body, truncated = truncate_at_boundary(repeated, MAX_ANALYSIS_CHARS)
    request = fixture.request(longest)
    return request.model_copy(
        update={"message_key": PROBE_KEY, "body_text": body, "body_truncated": truncated}
    )


async def run_probe(
    fixture: Fixture, analyzer: Analyzer, clock: Callable[[], float] = time.perf_counter
) -> tuple[CallRecord, str]:
    """Time one maximum-length email; also say whether its analysis validated."""
    request = probe_request(fixture)
    started = clock()
    try:
        response, timing = await analyzer.analyze([request])
    except (LocalModelError, httpx.HTTPError) as exc:
        return CallRecord((PROBE_KEY,), None, clock() - started, type(exc).__name__), "error"
    status = outcomes([request], response)[0].status
    return CallRecord((PROBE_KEY,), timing, clock() - started), status


def percentile(values: Sequence[float], share: float) -> float | None:
    """The nearest-rank percentile, or None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass(frozen=True, slots=True)
class Timing:
    pass_seconds: float
    first_visible_seconds: float | None
    call_p50: float | None
    call_p95: float | None
    first_token_p50: float | None
    first_token_p95: float | None
    completion_tokens: int | None
    generation_per_second: float | None
    prompt_per_second: float | None


def weighted_rate(pairs: Iterable[tuple[int | None, float | None]]) -> float | None:
    """Tokens per second across calls, each call's (tokens, rate): all the tokens over the
    time they took, so a short call counts no more than its tokens."""
    counted = [(tokens, rate) for tokens, rate in pairs if tokens and rate]
    if not counted:
        return None
    return sum(tokens for tokens, _ in counted) / sum(tokens / rate for tokens, rate in counted)


def timing(run: Pass) -> Timing:
    timings = [call.timing for call in run.calls if call.timing is not None]
    first = [t.first_token_seconds for t in timings if t.first_token_seconds is not None]
    tokens = [t.completion_tokens for t in timings if t.completion_tokens is not None]
    return Timing(
        pass_seconds=run.seconds,
        first_visible_seconds=run.calls[0].visible_after_seconds if run.calls else None,
        call_p50=percentile([t.seconds for t in timings], 0.5),
        call_p95=percentile([t.seconds for t in timings], 0.95),
        first_token_p50=percentile(first, 0.5),
        first_token_p95=percentile(first, 0.95),
        completion_tokens=sum(tokens) if tokens else None,
        generation_per_second=weighted_rate(
            (t.completion_tokens, t.predicted_per_second) for t in timings
        ),
        prompt_per_second=weighted_rate((t.prompt_evaluated, t.prompt_per_second) for t in timings),
    )


def port_in_use(port: int) -> bool:
    """Whether something already listens on 127.0.0.1:``port``."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def public_command(command: Sequence[str]) -> list[str]:
    """``command`` as a report may keep it: secret values hidden, absolute paths cut to their
    names (they would record the owner's folders)."""
    shown: list[str] = []
    hide_next = False
    for part in command:
        flag, equals, _ = part.partition("=")
        if hide_next:
            shown.append("<hidden>")
        elif equals and flag in SECRET_FLAGS:
            shown.append(f"{flag}=<hidden>")
        else:
            shown.append(Path(part).name if Path(part).is_absolute() else part)
        hide_next = not hide_next and part in SECRET_FLAGS
    return shown


class LlamaServer:
    """llama-server on 127.0.0.1, started for one benchmark and stopped after it. It needs a
    fresh API key and has no web UI, so other programs and web pages can't use it meanwhile."""

    def __init__(
        self,
        executable: Path,
        model_file: Path,
        *,
        port: int,
        context: int,
        gpu_layers: int,
        threads: int | None,
        extra: Sequence[str],
        log_path: Path,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.url = f"http://127.0.0.1:{port}"
        self.port = port
        self.api_key = secrets.token_urlsafe(32)
        self.command = [
            str(executable),
            "-m",
            str(model_file),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-c",
            str(context),
            "-ngl",
            str(gpu_layers),
            "--parallel",
            "1",
            "--jinja",
            "--no-webui",
            "--api-key",
            self.api_key,
            *([] if threads is None else ["-t", str(threads)]),
            *extra,
        ]
        self._log_path = log_path
        self._clock = clock
        self._log: IO[str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._started = 0.0
        self.cold_start_seconds = 0.0

    def __enter__(self) -> Self:
        # Another server on the port would answer /health and be benchmarked instead.
        if port_in_use(self.port):
            raise RuntimeError(f"port {self.port} is already in use; stop that server first")
        self._log = self._log_path.open("w", encoding="utf-8")
        try:
            self._started = self._clock()
            self._process = subprocess.Popen(
                self.command, stdout=self._log, stderr=subprocess.STDOUT, text=True
            )
            self._wait_until_ready(self._process)
        except BaseException:  # Ctrl+C too: never leave the server running.
            self.__exit__(None, None, None)
            raise
        self.cold_start_seconds = self._clock() - self._started
        return self

    def _wait_until_ready(self, process: subprocess.Popen[str]) -> None:
        with httpx.Client(timeout=5.0) as client:
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"llama-server exited; see {self._log_path}")
                try:
                    if client.get(f"{self.url}/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                if self._clock() - self._started > HEALTH_TIMEOUT_SECONDS:
                    raise RuntimeError("llama-server did not become ready in time")
                time.sleep(0.25)

    def usage(self) -> tuple[ProcessUsage, float]:
        """The live server's usage so far and how long it has run."""
        if self._process is None:
            return UNKNOWN, 0.0
        return process_usage(self._process.pid), self._clock() - self._started

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        if self._log is not None:
            self._log.close()
            self._log = None


def server_version(executable: Path) -> str | None:
    try:
        done = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line for line in (done.stdout + done.stderr).splitlines() if "version" in line]
    return lines[0].strip() if lines else None


async def run_analyzer(
    fixture: Fixture,
    analyzer: Analyzer,
    *,
    repeats: int,
    batch_size: int,
    long_probe: bool,
) -> tuple[list[Pass], tuple[CallRecord, str] | None]:
    passes = [
        await run_pass(index, fixture, analyzer, batch_size=batch_size) for index in range(repeats)
    ]
    probe = await run_probe(fixture, analyzer) if long_probe else None
    return passes, probe


async def run_local(
    base_url: str,
    model_name: str,
    fixture: Fixture,
    *,
    repeats: int,
    batch_size: int,
    max_output_tokens: int,
    template_kwargs: dict[str, object],
    long_probe: bool,
    api_key: str | None = None,
) -> tuple[list[Pass], tuple[CallRecord, str] | None, dict[str, int], int]:
    """Run the model through the loopback guard; also return what the guard saw."""
    guard = LoopbackGuard()
    async with httpx.AsyncClient(transport=guard) as client:
        model = LocalModel(
            client,
            base_url=base_url,
            model=model_name,
            max_output_tokens=max_output_tokens,
            template_kwargs=template_kwargs,
            api_key=api_key,
        )
        passes, probe = await run_analyzer(
            fixture, model, repeats=repeats, batch_size=batch_size, long_probe=long_probe
        )
    return passes, probe, dict(guard.sent), guard.refused


def _battery(before: BatterySample | None, after: BatterySample | None) -> object:
    if before is None or after is None:
        return None
    on_battery = not before.on_ac_power and not after.on_ac_power
    return {
        "before": asdict(before),
        "after": asdict(after),
        "used_mwh": before.remaining_mwh - after.remaining_mwh if on_battery else None,
    }


def build_report(
    *,
    label: str,
    system: str,
    fixture_path: Path,
    fixture: Fixture,
    passes: Sequence[Pass],
    probe: tuple[CallRecord, str] | None,
    settings: dict[str, object],
    runtime: object = None,
    model: object = None,
    network: object = None,
    cold_start_seconds: float | None = None,
    usage: ProcessUsage = UNKNOWN,
    usage_seconds: float | None = None,
    battery: object = None,
) -> dict[str, object]:
    first = passes[0]
    busy = (
        usage.cpu_seconds / usage_seconds
        if usage.cpu_seconds is not None and usage_seconds
        else None
    )
    return {
        "benchmark": "P0-B1",
        "harness_version": HARNESS_VERSION,
        "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "label": label,
        "system": system,
        "fixture": {
            "name": fixture.name,
            "file": fixture_path.name,
            "sha256": fixture_digest(fixture_path),
            "messages": len(fixture.messages),
        },
        "device": device(),
        "runtime": runtime,
        "model": model,
        "settings": settings,
        "network": network,
        "cold_start_seconds": cold_start_seconds,
        "resources": {
            "peak_memory_bytes": usage.peak_memory_bytes,
            "gpu_memory_bytes": usage.gpu_memory_bytes,
            "cpu_seconds": usage.cpu_seconds,
            "cpu_cores_busy": busy,
        },
        "battery": battery,
        "quality": asdict(quality(first.scores)),
        "valid_by_pass": [asdict(quality(run.scores).valid) for run in passes],
        "timing_by_pass": [asdict(timing(run)) for run in passes],
        "long_probe": None if probe is None else {**asdict(probe[0]), "status": probe[1]},
        "messages": [asdict(item) for item in first.scores],
        "calls_first_pass": [asdict(call) for call in first.calls],
    }


def _seconds(value: object) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{value * 1000:.1f} ms" if value < 0.1 else f"{value:.1f} s"


def _gib(value: object) -> str:
    return f"{value / 1024**3:.2f} GiB" if isinstance(value, int | float) else "n/a"


def _ratio(value: object) -> str:
    if isinstance(value, dict):
        return str(Ratio(int(value["hits"]), int(value["total"])))
    return "n/a"


_QUALITY_ROWS = (
    ("Valid analyses (pass the app's checks)", "valid"),
    ("Category", "category"),
    ("Action required", "action_required"),
    ("Deadline day", "deadline"),
    ("Deadline time", "deadline_time"),
    ("Follow-up signal", "follow_up"),
    ("Expected actions found (recall)", "action_recall"),
    ("Suggestions that were expected (precision)", "action_precision"),
    ("Suggestions kept by validation", "suggestions_kept"),
    ("Summary facts", "summary_facts"),
    ("Summary in the email's language", "language"),
)
_FLAG_ROWS = (
    ("Secret or injected text echoed", "forbidden_echoes"),
    ("Deadlines dated before the email arrived", "past_deadlines"),
    ("Numbers the email doesn't contain", "unsupported_numbers"),
)


def _warm(report: dict[str, object], field: str) -> object:
    """The median of a timing field over the warm passes, or the only pass's value."""
    passes = report["timing_by_pass"]
    assert isinstance(passes, list)
    warm = passes[1:] or passes
    values = [item[field] for item in warm if isinstance(item.get(field), int | float)]
    return statistics.median(values) if values else None


def render_markdown(report: dict[str, object]) -> str:
    quality_ = report["quality"]
    assert isinstance(quality_, dict)
    lines = [
        f"# Local AI benchmark: {report['label']}",
        "",
        f"System: {report['system']}. Fixture: {_fixture_line(report)}. "
        f"Created {report['created_utc']}.",
        "",
        "## Quality (first pass)",
        "",
        "| Measure | Result |",
        "| --- | --- |",
    ]
    lines += [f"| {name} | {_ratio(quality_[key])} |" for name, key in _QUALITY_ROWS]
    lines += [f"| {name} | {quality_[key]} |" for name, key in _FLAG_ROWS]
    by_sensitivity = quality_["valid_by_sensitivity"]
    assert isinstance(by_sensitivity, dict)
    lines += [
        "",
        "Valid by sensitivity: "
        + ", ".join(f"{name} {_ratio(value)}" for name, value in by_sensitivity.items())
        + ".",
        "",
        "## Timing",
        "",
        f"Cold start (server launch to ready): {_seconds(report['cold_start_seconds'])}.",
        "",
        "| Pass | All messages | First result | Call p50 | Call p95 | First token p50 "
        "| Output tokens | Generation tok/s | Prompt tok/s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    passes = report["timing_by_pass"]
    assert isinstance(passes, list)
    for index, item in enumerate(passes):
        name = "1 (cold)" if index == 0 else str(index + 1)
        rate = item["generation_per_second"]
        prompt = item["prompt_per_second"]
        lines.append(
            f"| {name} | {_seconds(item['pass_seconds'])} "
            f"| {_seconds(item['first_visible_seconds'])} | {_seconds(item['call_p50'])} "
            f"| {_seconds(item['call_p95'])} | {_seconds(item['first_token_p50'])} "
            f"| {item['completion_tokens'] or 'n/a'} "
            f"| {'n/a' if rate is None else f'{rate:.1f}'} "
            f"| {'n/a' if prompt is None else f'{prompt:.0f}'} |"
        )
    probe = report["long_probe"]
    if isinstance(probe, dict):
        call = probe["timing"] or {}
        lines += [
            "",
            f"Longest email ({MAX_ANALYSIS_CHARS:,} characters): {_seconds(call.get('seconds'))}, "
            f"first token after {_seconds(call.get('first_token_seconds'))}, "
            f"{call.get('prompt_tokens') or 'n/a'} prompt tokens, {probe['status']}.",
        ]
    resources = report["resources"]
    assert isinstance(resources, dict)
    busy = resources["cpu_cores_busy"]
    lines += [
        "",
        "## Resources and network",
        "",
        f"- Server peak working set: {_gib(resources['peak_memory_bytes'])}; GPU memory: "
        f"{_gib(resources['gpu_memory_bytes'])}; CPU time: "
        f"{_seconds(resources['cpu_seconds'])}"
        f" ({'n/a' if busy is None else f'{busy:.1f}'} cores busy on average).",
        f"- Battery: {json.dumps(report['battery'])}.",
        f"- Requests by host: {json.dumps(report['network'])}.",
        "",
        "## Messages (first pass)",
        "",
        "| Key | Sensitivity | Status | Category | Deadline | Follow-up | Actions "
        "| Facts | Flags | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    messages = report["messages"]
    assert isinstance(messages, list)
    for item in messages:
        lines.append(_message_row(item))
    return "\n".join(lines) + "\n"


def _fixture_line(report: dict[str, object]) -> str:
    fixture = report["fixture"]
    assert isinstance(fixture, dict)
    return f"{fixture['name']} ({fixture['messages']} messages, sha256 {fixture['sha256'][:12]})"


def _mark(value: object, predicted: object = None) -> str:
    """Right or wrong, with what the system said when it said something."""
    mark = "–" if value is None else ("yes" if value else "no")
    return mark if predicted is None else f"{mark} ({predicted})"


def _message_row(item: dict[str, object]) -> str:
    expected = item["expected_actions"]
    actions = "–" if expected is None else f"{item['matched_actions']}/{expected}"
    forbidden = item["forbidden"]
    flags = [f"echoes {text}" for text in forbidden] if isinstance(forbidden, list) else []
    if item["past_deadlines"]:
        flags.append(f"{item['past_deadlines']} past deadline(s)")
    numbers = item["unsupported_numbers"]
    if isinstance(numbers, list) and numbers:
        flags.append("numbers " + ", ".join(str(n) for n in numbers))
    summary = str(item["summary"] or "").replace("|", "\\|").replace("\n", " ")
    category = _mark(item["category"], item["predicted_category"])
    deadline = _mark(item["deadline"], item["predicted_deadline"])
    follow_up = _mark(item["follow_up"], item["predicted_follow_up"])
    return (
        f"| {item['key']} | {item['sensitivity']} | {item['status']} | {category} "
        f"| {deadline} | {follow_up} | {actions} "
        f"| {item['summary_facts_found']}/{item['summary_facts']} | {'; '.join(flags) or '–'} "
        f"| {summary} |"
    )


def render_comparison(reports: Sequence[dict[str, object]]) -> str:
    """One table with a column per report."""
    names = [str(report["label"]) for report in reports]
    lines = [
        "| Measure | " + " | ".join(names) + " |",
        "| --- |" + " --- |" * len(names),
    ]

    def row(name: str, cells: Sequence[str]) -> None:
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    qualities = [report["quality"] for report in reports]
    for name, key in _QUALITY_ROWS:
        row(name, [_ratio(q[key]) if isinstance(q, dict) else "n/a" for q in qualities])
    for name, key in _FLAG_ROWS:
        row(name, [str(q[key]) if isinstance(q, dict) else "n/a" for q in qualities])
    row("Cold start", [_seconds(report["cold_start_seconds"]) for report in reports])
    row("All messages, warm", [_seconds(_warm(report, "pass_seconds")) for report in reports])
    row("First result, warm", [_seconds(_warm(r, "first_visible_seconds")) for r in reports])
    row("Call p50 / p95, warm", [_p50_p95(report) for report in reports])
    row("Generation tok/s", [_rate(_warm(r, "generation_per_second")) for r in reports])
    row("Prompt tok/s", [_rate(_warm(r, "prompt_per_second")) for r in reports])
    row("Longest email", [_probe(report) for report in reports])
    row("Peak RAM (server)", [_gib(_resource(r, "peak_memory_bytes")) for r in reports])
    row("GPU memory", [_gib(_resource(r, "gpu_memory_bytes")) for r in reports])
    row("CPU cores busy", [_rate(_resource(r, "cpu_cores_busy")) for r in reports])
    row("Non-loopback requests", [_refused(report) for report in reports])
    return "\n".join(lines) + "\n"


def _rate(value: object) -> str:
    return f"{value:.1f}" if isinstance(value, int | float) else "n/a"


def _p50_p95(report: dict[str, object]) -> str:
    return f"{_seconds(_warm(report, 'call_p50'))} / {_seconds(_warm(report, 'call_p95'))}"


def _probe(report: dict[str, object]) -> str:
    probe = report["long_probe"]
    if not isinstance(probe, dict):
        return "n/a"
    return f"{_seconds((probe['timing'] or {}).get('seconds'))} ({probe['status']})"


def _resource(report: dict[str, object], key: str) -> object:
    resources = report["resources"]
    return resources.get(key) if isinstance(resources, dict) else None


def _refused(report: dict[str, object]) -> str:
    network = report["network"]
    if not isinstance(network, dict):
        return "none (no network use)"
    return str(network.get("refused", "n/a"))


def _out_dir(base: Path, label: str) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label)
    path = base / f"{stamp}-{safe}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _write(out: Path, report: dict[str, object]) -> None:
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")


def command_baseline(args: argparse.Namespace) -> Path:
    fixture = load_fixture(args.fixture)
    passes, probe = asyncio.run(
        run_analyzer(
            fixture,
            BaselineAnalyzer(),
            repeats=args.repeats,
            batch_size=args.batch_size,
            long_probe=True,
        )
    )
    out = _out_dir(args.out, args.label)
    report = build_report(
        label=args.label,
        system="baseline",
        fixture_path=args.fixture,
        fixture=fixture,
        passes=passes,
        probe=probe,
        settings={"repeats": args.repeats, "batch_size": args.batch_size},
    )
    _write(out, report)
    return out


def command_model(args: argparse.Namespace) -> Path:
    fixture = load_fixture(args.fixture)
    label = args.label or (args.model_file.stem if args.model_file else args.model)
    out = _out_dir(args.out, label)
    template_kwargs: dict[str, object] = {"enable_thinking": False} if args.no_think else {}
    settings: dict[str, object] = {
        "repeats": args.repeats,
        "batch_size": args.batch_size,
        "max_output_tokens": args.max_output_tokens,
        "temperature": 0,
        "template_kwargs": template_kwargs,
        "prompt_version": "the Groq adapter's instructions and schema",
    }
    usage, usage_seconds, cold = UNKNOWN, None, None
    runtime: dict[str, object]
    model_info: dict[str, object]

    def run(
        base_url: str, name: str, api_key: str | None = None
    ) -> tuple[list[Pass], tuple[CallRecord, str] | None, dict[str, int], int]:
        return asyncio.run(
            run_local(
                base_url,
                name,
                fixture,
                repeats=args.repeats,
                batch_size=args.batch_size,
                max_output_tokens=args.max_output_tokens,
                template_kwargs=template_kwargs,
                long_probe=not args.no_long_probe,
                api_key=api_key,
            )
        )

    if args.server is not None:
        model_file: Path = args.model_file
        model_info = {
            "file": model_file.name,
            "bytes": model_file.stat().st_size,
            "sha256": sha256(model_file),
        }
        server = LlamaServer(
            args.server,
            model_file,
            port=args.port,
            context=args.context,
            gpu_layers=args.gpu_layers,
            threads=args.threads,
            extra=args.server_arg,
            log_path=out / "server.log",
        )
        runtime = {
            "server": "llama-server",
            "version": server_version(args.server),
            "command": public_command(server.command),
        }
        before = battery_sample() if args.battery else None  # After hashing the model.
        with server:
            cold = server.cold_start_seconds
            passes, probe, sent, refused = run(server.url, model_file.stem, server.api_key)
            usage, usage_seconds = server.usage()
        if usage is UNKNOWN:
            usage = finished_children_usage()
    else:
        model_info = {"name": args.model}
        runtime = {"server": "existing", "base_url": require_loopback(args.base_url)}
        before = battery_sample() if args.battery else None
        passes, probe, sent, refused = run(args.base_url, args.model)
    after = battery_sample() if args.battery else None
    report = build_report(
        label=label,
        system="model",
        fixture_path=args.fixture,
        fixture=fixture,
        passes=passes,
        probe=probe,
        settings=settings,
        runtime=runtime,
        model=model_info,
        network={"requests_by_host": sent, "refused": refused},
        cold_start_seconds=cold,
        usage=usage,
        usage_seconds=usage_seconds,
        battery=_battery(before, after),
    )
    _write(out, report)
    return out


def command_compare(args: argparse.Namespace) -> None:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    text = render_comparison(reports)
    if args.write is None:
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        args.write.write_text(text, encoding="utf-8")


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _batch(value: str) -> int:
    number = _positive(value)
    if number > 10:
        raise argparse.ArgumentTypeError("must be at most 10")
    return number


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    common.add_argument("--out", type=Path, default=DEFAULT_OUT)
    common.add_argument("--repeats", type=_positive, default=3)
    common.add_argument("--batch-size", type=_batch, default=1)

    base = commands.add_parser("baseline", parents=[common], help="run the rule-based baseline")
    base.add_argument("--label", default="baseline")

    model = commands.add_parser("model", parents=[common], help="run a local model")
    source = model.add_mutually_exclusive_group(required=True)
    source.add_argument("--server", type=Path, help="llama-server executable to start")
    source.add_argument("--base-url", help="an OpenAI-compatible server already on loopback")
    model.add_argument("--model-file", type=Path, help="GGUF file (with --server)")
    model.add_argument("--model", help="model name (with --base-url)")
    model.add_argument("--label")
    model.add_argument("--port", type=_positive, default=8091)
    model.add_argument("--context", type=_positive, default=8192)
    model.add_argument("--gpu-layers", type=int, default=99)
    model.add_argument("--threads", type=_positive)
    model.add_argument("--server-arg", action="append", default=[], help="extra server flag")
    model.add_argument("--max-output-tokens", type=_positive, default=2048)
    model.add_argument("--no-think", action="store_true", help="disable a hybrid model's thinking")
    model.add_argument("--no-long-probe", action="store_true")
    model.add_argument("--battery", action="store_true", help="read battery charge (Windows)")

    compare = commands.add_parser("compare", help="one table from several report.json files")
    compare.add_argument("reports", type=Path, nargs="+")
    compare.add_argument("--write", type=Path)
    return parser


def budget_problem(batch_size: int, context: int | None, max_output_tokens: int) -> str | None:
    """Why a batch this large won't fit the context or the answer limit, or None. A failure
    from either would be scored as the model's, so it is refused before the run."""
    answer = batch_size * ANSWER_TOKENS_PER_EMAIL
    if max_output_tokens < answer:
        return f"--batch-size {batch_size} needs --max-output-tokens of at least {answer}"
    needed = INSTRUCTION_TOKENS + batch_size * EMAIL_TOKENS + max_output_tokens
    if context is not None and context < needed:
        return f"--batch-size {batch_size} needs --context of at least {needed}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "model":
        if args.server is not None and args.model_file is None:
            parser().error("--server needs --model-file")
        if args.base_url is not None and args.model is None:
            parser().error("--base-url needs --model")
        # An existing server's context is its own, so only the answer limit is checked then.
        context = args.context if args.server is not None else None
        problem = budget_problem(args.batch_size, context, args.max_output_tokens)
        if problem is not None:
            parser().error(problem)
        print(command_model(args))
    elif args.command == "baseline":
        print(command_baseline(args))
    else:
        command_compare(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
