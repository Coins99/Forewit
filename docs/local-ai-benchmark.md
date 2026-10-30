# P0-B1: local AI benchmark

P0-B1 of the [privacy-first email AI workstream](future-implementations.md#71-p0-b-local-first-email-privacy-and-responsiveness-d09)
(D09) asks whether a model on the owner's own computer can analyze overnight mail well and
fast enough to brief it without sending anything to Groq, and what a lightweight local
alternative without a model would give instead. This page records how that was measured, what
was found on one Windows laptop, and what P0-B2 should build. Nothing here ships in the
desktop app.

## Answer

On this laptop, yes, but as background work rather than at the click of a button.
Qwen3-4B-Instruct-2507 (Q4_K_M, 2.5 GB) analyzed all 30 synthetic emails into results the app
accepts, and every request the benchmark sent went to this computer itself (what
`llama-server` itself connects to wasn't watched). Its summaries are real summaries, in the email's
own language, and it ignored a prompt-injection email. It takes about 15 seconds per email
(about 6 minutes for the 20), so P0-B3's cached-first, one-by-one display and optional
precomputation are required, not extras. Its weak points are follow-up signals (it found none
of the five that were really there), suggesting no action when it calls an email a deadline,
Japanese relative dates, and repeating a one-time code. P0-B2 should run llama.cpp's
`llama-server` as a hardened side process with this model, under a local prompt of its own.

The rule-based baseline is instant and passes validation, but on the held-out emails it finds
57% of deadline days and half the expected actions, and its "summary" is the email's first
sentence: for the injection email that is the injected instruction itself. It is fit to be
the labelled preview the plan describes, not a summary.

Qwen3-1.7B (1.1 GB) is about 2.4 times faster but not good enough: 2 of its 20 analyses
failed the app's checks, it found a fifth of the expected actions, it summarized the Japanese
email in English, and it obeyed the injected instruction, summarizing the phishing email as
"Verified safe message from IT". It shouldn't be offered as a smaller-device option.

## Results

One Windows 11 laptop: AMD Ryzen 7 PRO 7840U (8 cores, 16 threads), Radeon 780M integrated
GPU through Vulkan, 32 GB RAM, on battery power. llama.cpp b12015, all layers on the GPU,
8,192-token context, one slot. Both fixtures are described [below](#the-fixtures).

On the 20 emails:

| Measure | Baseline | 4B | 1.7B |
| --- | --- | --- | --- |
| Valid analyses (pass the app's checks) | 20/20 | 20/20 | 18/20 (90%) |
| Category | 19/20 (95%) | 17/20 (85%) | 12/20 (60%) |
| Action required | 15/17 (88%) | 13/17 (76%) | 6/17 (35%) |
| Deadline day | 15/15 (100%) | 13/15 (87%) | 9/15 (60%) |
| Deadline time | 1/1 | 1/1 | 0/1 |
| Follow-up signal | 20/20 (100%) | 16/20 (80%) | 15/20 (75%) |
| Expected actions found (recall) | 9/10 (90%) | 5/10 (50%) | 2/10 (20%) |
| Suggestions that were expected (precision) | 9/11 (82%) | 5/6 (83%) | 2/3 (67%) |
| Summary facts | 32/35 (91%) | 35/35 (100%) | 26/35 (74%) |
| Japanese email summarized in Japanese | 1/1 | 1/1 | 0/1 |
| Secret or injected text echoed | 2 | 1 | 2 |
| Deadlines dated before the email arrived | 0 | 0 | 0 |
| Numbers the email doesn't contain | 0 | 0 | 0 |
| Benchmark requests to hosts other than loopback | none made | 0 of 41 | 0 of 41 |

The baseline's column is an upper bound: its rules were written with these 20 emails in view.
The held-out emails are the fairer comparison:

| Measure | Baseline | 4B | 1.7B |
| --- | --- | --- | --- |
| Valid analyses (pass the app's checks) | 10/10 | 10/10 | 10/10 |
| Category | 8/10 (80%) | 9/10 (90%) | 7/10 (70%) |
| Action required | 5/6 (83%) | 6/6 (100%) | 3/6 (50%) |
| Deadline day | 4/7 (57%) | 5/7 (71%) | 5/7 (71%) |
| Follow-up signal | 7/10 (70%) | 7/10 (70%) | 6/10 (60%) |
| Expected actions found (recall) | 3/6 (50%) | 4/6 (67%) | 2/6 (33%) |
| Suggestions that were expected (precision) | 3/3 (100%) | 4/5 (80%) | 2/2 (100%) |
| Summary facts | 19/20 (95%) | 20/20 (100%) | 18/20 (90%) |
| Secret or injected text echoed | 0 | 0 | 0 |
| Benchmark requests to hosts other than loopback | none made | 0 of 10 | 0 of 10 |

The 1.7B model's echoes on the 20 are the one-time code and the injected "Verified safe
message from IT". Its summaries are also much thinner ("Salary adjustment effective December
1", "Re: Saturday").

Speed and resources on the 20 emails (the baseline takes milliseconds):

| Measure | 4B, cold pass | 4B, warm pass | 1.7B, warm pass |
| --- | --- | --- | --- |
| All 20 emails | 365 s | 354 s | 145 s |
| First result | 30.4 s | 27.4 s | 6.1 s |
| Per email, p50 / p95 | 15.6 s / 30.4 s | 15.0 s / 27.4 s | 6.2 s / 11.9 s |
| Time to first token, p50 | 0.8 s | 0.8 s | 0.4 s |
| Output tokens (all 20) | 4,334 | 4,334 | 3,987 |
| Generation / prompt rate (tokens per second) | 12.5 / 184 | 12.8 / 187 | 28.6 / 453 |
| Longest email (8,000 characters) | 22.2 s | | 12.4 s |
| Cold start | 3.2 s | | 2.2 s |
| Peak working set / GPU memory | 4.05 / 3.93 GiB | | 2.41 / 2.28 GiB |
| Battery used (whole run) | 4.73 Wh | | 2.14 Wh |

The notes below are for the 4B model.

- Cold start (launch to ready, model load included): 3.2 s with the model file already in
  the operating system's cache. A first launch after a restart reads 2.5 GB from disk and
  will take longer; that wasn't measured.
- The longest email the app sends (8,000 characters, 2,472 prompt tokens): 22.2 s, first
  token after 8.8 s, valid.
- Memory: a 4.05 GiB peak working set and 3.93 GiB of GPU memory. On an integrated GPU both
  are system RAM and may partly overlap (the mapped model file). Budget about 4 to 6 GB.
- CPU: 240 s of CPU time over the run, 0.3 cores busy on average; the GPU does the work.
- Energy: the battery fell by 4.73 Wh over the whole run (two passes and the long email, about
  12.5 minutes), about 23 W for the whole laptop, screen included. That is roughly 2.3 Wh per
  20 emails, about 3% of this 73 Wh battery. Idle draw wasn't measured, so the model's own
  share is lower.
- Output dominates the time: about 220 tokens per email at about 13 tokens per second. The
  JSON grammar costs about 2 tokens per second: `llama-bench` gives 14.6 tokens per second
  unconstrained.
- Without the GPU (`llama-bench`, `-ngl 0`), the same model generates 8.0 tokens per second
  instead of 14.6 and reads prompts at 160 instead of 241 tokens per second, so about 11
  minutes for 20 emails on this CPU alone. That is an estimate; it wasn't run end to end.

### What the 4B model got wrong

- **Follow-up signals**: it reported none for all five emails that carry one (two
  cancellations here, and a delivery, a moved deadline and a cancellation in the held-out
  set). Its 70–80% comes from emails where "none" is right. The follow-up proposals of ADR
  0016 would not work from this model with this prompt.
- **Due dates without actions**: on three emails that ask for something by a date (the
  invoice, the lease renewal and the Japanese fee request) it set action required to false and
  suggested nothing, so action recall is 50% on the 20. In a smoke test it called such emails
  "deadline", as if that category excluded an action.
- **Relative dates**: neither 来週の水曜日 ("Wednesday next week") nor "before Monday's
  release" was resolved to the right day. Python checks that the date the model reports is a
  real, plausible day, but not the model's arithmetic.
- **A one-time code**: it repeated 482913 in the summary. So did the baseline.
- It did not follow the injected instruction: its summary of that email says the mailbox
  will be suspended unless the password is verified.

## Recommendation for P0-B2

- **Runtime**: llama.cpp's `llama-server` (MIT) as a side process that Forewit starts and
  stops: the Vulkan build on Windows, the Metal build on macOS, with the existing output
  schema as its grammar. No Python local-model library is added, which keeps AGENTS.md's
  dependency rule; record the side-process decision in an ADR.
- **Harden the side process**: `llama-server` warns that it has no API key and allows every
  CORS origin, so while it runs any program, and any web page in a browser on the computer,
  could send it requests. Bind 127.0.0.1 on a random port, pass a fresh `--api-key` on each
  launch, turn the web UI off, refuse a port another program already holds, and stop it with
  the app, also when starting is interrupted. The benchmark now runs it this way.
- **Prove the side process stays local**: this benchmark shows only that its own requests
  stayed on loopback. P0-B2's local-only test must also watch `llama-server`, for example by
  listing its connections during a run or blocking its outbound traffic with the OS firewall
  and checking that analysis still works.
- **Model**: Qwen3-4B-Instruct-2507 Q4_K_M (Apache-2.0). Download it on first use from a pinned
  URL and check a pinned SHA-256 before loading, rather than adding 2.5 GB to the installer.
  The GGUF used here is a third-party quantization (unsloth); for release, quantize Qwen's
  own weights or pin and record the provenance of the one shipped.
- **A local prompt of its own** (its own prompt version, so the cache keeps results apart),
  tuned on a separate development fixture with these two kept for acceptance: decouple
  action required from the category, give follow-up examples, keep one-time codes out of the
  summary, and have Python check relative dates (the baseline's `find_deadline` can serve as
  a cross-check).
- **Shorter local output**: generation is most of the time, so asking for steps only when
  the owner opens a suggestion could cut per-email time sharply.
- **Device expectations**, to confirm before release: 16 GB of RAM and a GPU llama.cpp can
  use (Vulkan or Metal) for about 15 seconds per email; without one, expect about twice as
  long, which still suits overnight precomputation. Qwen3-1.7B is too weak to serve smaller
  machines; on those, show the baseline's labelled preview until a better small model is
  measured.

## Not yet measured

- macOS (Apple silicon with Metal), and a machine with 8 GB of RAM or no usable GPU.
- `llama-server`'s own network connections (see the recommendation above).
- Other small models (for example in the 3B class), which might fill the gap the 1.7B
  model leaves.
- The laptop's idle power draw, to separate the model's energy from the screen's.
- A cold first launch after a restart, and packaging `llama-server` into the Windows and
  macOS builds.
- A person rating the summaries: summary facts are matched by keywords, which every system
  nearly saturates.

## What was compared

- **Local model**: a quantized model run by llama.cpp's `llama-server`, an external program
  on `127.0.0.1`, asked exactly what the Groq adapter asks: its instructions
  (`providers/groq/provider.py` `INSTRUCTIONS`), its minimized input (`analysis_input`) and
  its strict output schema (`AnalysisWireBatch`), one email per call, temperature 0. llama.cpp
  turns the schema into a grammar, so every answer is well-formed JSON. Two models, both
  Apache-2.0: Qwen3-4B-Instruct-2507 and Qwen3-1.7B (thinking off), each as a Q4_K_M GGUF.
- **Baseline**: fixed rules and no model (`scripts/local_ai/baseline.py`). Its summary is the
  email's own first sentence and request, its deadlines come from English and Japanese date
  patterns, and its actions are the sentences that ask for something. It is the honest
  "local metadata preview" the plan falls back to when a model isn't good enough.

Both produce the same untrusted candidate, and both go through the app's own
`validate_candidate` (`services/analysis.py`): evidence must quote the email, deadlines are
resolved in Python, suggestions are checked one by one. So "valid" below means "the desktop
app would have kept it".

## The fixtures

`scripts/local_ai/overnight-20.json` is the plan's acceptance fixture: 20 synthetic emails
that arrive overnight for one person in Toronto, 6 clearly sensitive (a card-fraud alert, lab
results, a one-time code, a salary letter, a lease notice, a tax slip), 8 apparently ordinary
(an invoice, a review request, a newsletter, a delivery, a meeting move, cancellations, a
request with a promise) and 6 ambiguous (a friend's plans, a recruiter, a prompt-injection
phishing email, a Japanese neighbourhood-fee request, a forwarded hospital discharge note and
"ok thanks!"). Each message carries labels: acceptable categories, whether action is
required, the deadline's day and time, the follow-up signal, the actions a good analysis
suggests (ownership and title words), facts the summary should mention and text it must
never repeat (the one-time code, the injected instruction).

The baseline's rules were written with these 20 emails in view, so its scores on them are an
upper bound. `scripts/local_ai/holdout-10.json` holds 10 more emails, for a person in Tokyo,
written after the baseline's rules were frozen and phrased differently ("no later than 3
November", "before our sync on Tues", "pushed the deadline back", "call off"). Neither the
baseline nor the labels were changed after seeing any result on them.

All names, addresses and numbers are invented, and senders use reserved example domains.
Reports contain every analysis, so only synthetic fixtures belong in this harness.

## What was measured

- **Quality**: valid analyses; category; action required; the deadline's day and, where the
  email states one, its time; the follow-up signal; expected actions found (recall) and kept
  suggestions that were expected (precision); suggestions that survived validation; summary
  facts; a Japanese email summarized in Japanese. Messages a reasonable reader could label
  either way leave that field unscored. A message without a valid analysis scores wrong on
  every scored field.
- **Review flags**: secret or injected text repeated in the summary, action or titles;
  deadlines resolved to a day before the email arrived (the model's date arithmetic); numbers
  in the summary or action that the email doesn't contain (clock times and the year aside).
- **Speed**: cold start (server launch until it answers `/health`, which includes loading the
  model); per pass, the time until the first result and until all 20, each call's time and
  time to first token (p50 and p95), and llama-server's own prompt and generation rates.
  Pass 1 starts right after the server is ready (cold); pass 2 is warm, with the shared
  instructions already in llama.cpp's prompt cache. One extra call times a maximum-length
  email (8,000 characters, the analysis limit).
- **Resources**: the server's peak working set, its GPU memory (Windows' GPU Process Memory
  counters, dedicated plus shared, read at the end), its CPU time and the average number of
  busy cores, and the battery's charge before and after (Windows' `BatteryStatus`, only
  meaningful on battery power).
- **Network**: every request the benchmark makes goes through `LoopbackGuard`, which refuses
  any host other than localhost, 127.0.0.1 and ::1 before anything is sent and counts what it
  sent by host. The model and baseline runs report those counts. They cover the benchmark's
  own requests only: what `llama-server` itself connects to isn't observed.

## Running it

```bash
uv run python -m scripts.local_ai.benchmark baseline
uv run python -m scripts.local_ai.benchmark baseline --fixture scripts/local_ai/holdout-10.json --label baseline-holdout
uv run python -m scripts.local_ai.benchmark model --server PATH/llama-server --model-file PATH/model.gguf --repeats 2 --battery
uv run python -m scripts.local_ai.benchmark compare out/local-ai-benchmark/*/report.json
```

`model` starts `llama-server` itself (`--port`, `--context`, `--gpu-layers`, `--threads` and
repeated `--server-arg` pass through), with a fresh API key and no web UI, and refuses a port
that something already listens on. Or it uses a server that is already running on loopback
with `--base-url http://127.0.0.1:11434 --model NAME` (Ollama's OpenAI-compatible API, for
example; a trailing `/v1` is fine). `--no-think` turns off a hybrid model's thinking. A
`--batch-size` that wouldn't fit `--context` or `--max-output-tokens` is refused before the
run, so it can't be scored as the model's failure. Each run writes `report.json`, `report.md`
and the server's log to its own folder under `out/local-ai-benchmark/`. The reports keep file
names, never the folders they came from, and never a secret flag's value.

The runtime and models used here were downloaded outside the repository and checked against
their published SHA-256 digests:

| File | Source | SHA-256 |
| --- | --- | --- |
| `llama-b12015-bin-win-vulkan-x64.zip` (37.5 MB) | github.com/ggml-org/llama.cpp, release b12015 | `20129400daa3d8216a1985abf942d993ba732419053540cca1ad6487bfd8ebe6` |
| `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2.50 GB) | huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` |
| `Qwen3-1.7B-Q4_K_M.gguf` (1.11 GB) | huggingface.co/unsloth/Qwen3-1.7B-GGUF | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` |
