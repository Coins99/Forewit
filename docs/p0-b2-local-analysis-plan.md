# P0-B2: local email analysis pipeline — implementation plan

Date: 2026-10-30. Status: approved by the owner on 2026-10-30 with the four recommended
decisions below. Part 1 is implemented; Parts 2–7 are not.
Branch: `feat/local-ai-pipeline`, cut from `feat/local-ai-benchmark` (P0-B1, not yet on
`main`); rebase onto `main` once P0-B1 merges. One pull request, built in parts, as M8 was.

Inputs: [P0-B in future-implementations](future-implementations.md#71-p0-b-local-first-email-privacy-and-responsiveness-d09)
and the [P0-B1 benchmark](local-ai-benchmark.md), whose recommendation this plan follows.

## Outcome

With local analysis turned on once, a brief, whether from Sync and review or a timed
refresh while the desktop is open, summarizes up to 20 of the day's messages on the
owner's computer, with no per-run question, and nothing about them reaches Groq or any
other AI service. Every result goes through the existing validation and cache. Revoking
stops new body reads and analysis at once, and nothing falls back to the cloud: not when
the model is missing, fails or is turned off. Owners who don't turn it on see no change.

Not in P0-B2:

- P0-B3: results appearing one by one while a run continues, cached-first startup, a
  priority queue beyond rank order, OS-scheduled precomputation before the window opens,
  pause. Until then a 20-message local run takes about 5 minutes inside the run, and the
  UI says so.
- P0-B4: cloud enrichment of local results, deny rules, risk gates.
- Asking for steps only when a suggestion is opened (a schema change), the rule-based
  baseline as a labelled preview on unsupported computers, and a Gmail history cursor.
  Today's sync already lists the day's Inbox within its bounds, and the input-hash cache
  sends the model only new or changed messages, which is enough for 20 messages a day.

## Decisions to confirm before Part 1

Each has a recommendation; the plan below assumes it.

1. **Runtime delivery.** Recommended: download llama.cpp's `llama-server` on first use,
   like the model: one pinned llama.cpp release, the Vulkan build for Windows x64 and the
   Metal build for macOS arm64, each checked against a pinned SHA-256. It keeps
   `Forewit.exe` at about 50 MB and works the same from source. Alternative: bundle
   `llama-server` in the native packages (tens of MB more per platform, and a second
   executable to sign on macOS).
2. **Model provenance.** Recommended: for this POC, the unsloth Q4_K_M GGUF of
   Qwen3-4B-Instruct-2507 (Apache-2.0), pinned by repository revision and SHA-256, with its
   provenance recorded in the ADR. Before a public release, quantize Qwen's own weights and
   publish that file as a release asset (a follow-up, not P0-B2).
3. **How many messages.** Recommended: a separate local limit, 1–20 messages per brief,
   default 20. The existing messages-per-brief preference (1–10) stays Groq's limit, so the
   cloud's bound doesn't move.
4. **Consent scope.** Recommended: per Gmail account, as Groq's consent is, and one consent
   covers manual and automatic runs (that is the point of D09). Restore clears it, as M9
   clears the automatic-analysis permission, because it allows unattended body reads.

## Design

- **Mode.** A new owner preference, `analysis_mode`: `cloud` (default, today's behavior)
  or `local`. In local mode the brief composes only the local provider; the Groq factory
  is never entered, so no fallback can happen by accident.
- **Consent.** A row in `ai_consents` with provider `local` and disclosure version
  `local-1`, independent of Groq's row. The disclosure says what is read (the bodies of up
  to N of the day's picked messages, from Gmail into memory), what is kept (summaries,
  short quotes, suggestions; never bodies), that nothing goes to an AI service, and the
  model's size and speed on this computer. Local mode never uses `auto_send_limit`.
- **Provider.** `LocalModelProvider` implements the existing `AIProvider` port with
  provider name `local`, so the analysis service, `validate_candidate`, the cache key
  (input hash, provider, model, prompt version, schema version), the brief and the
  proposals work unchanged, and local and Groq results never count as each other's hits.
  One email per call, as the benchmark measured.
- **Side process.** `llama-server` on 127.0.0.1 only (so Windows shows no firewall
  prompt), on a free port, with a fresh `--api-key` per launch, `--no-ui` and
  `--parallel 1`. Its output goes to a bounded in-memory buffer, never to Forewit's logs or
  files, and only known startup failures (model won't load, out of memory) become static
  error codes; it never runs with verbose logging, which would print prompts. It starts on
  the first message a run needs to send, stays loaded between runs and stops after 5 idle
  minutes or at quit. On Windows it runs in a Job Object that kills it when Forewit exits,
  crashes included; on macOS Forewit records its PID and start time and ends a leftover
  server at the next launch.
- **Jobs.** The brief run stays the job: one local analysis at a time per data folder
  (the desktop lock already keeps the CLI and the desktop apart, and the desktop's runs
  coalesce). Deduplication is the cache: a message is analyzed once per input hash, model,
  prompt and schema version. A persistent queue arrives with P0-B3's precomputation, which
  needs it for duplicate scheduler runs.
- **Revocation and cancellation.** The local consent is read again before bodies are
  downloaded and before each model call; revoking in the desktop also cancels the running
  job. Cancel aborts the call in progress (closing the stream stops generation), not only
  the next one, because a local call takes 15 seconds or more.

## Parts

Each part is one or more commits with its tests, and leaves the app working.

### Part 1 — Local provider and side process (no visible change)

- `src/mailbrief/providers/local/`: `transport.py` (`LoopbackGuard` and
  `require_loopback`, moved from `scripts/local_ai`), `provider.py` (`LocalModelProvider`:
  the streamed call from `scripts/local_ai/local_model.py`, its errors mapped to
  `ports.errors` types whose messages carry no email or model text) and `server.py`
  (`LlamaServer` as an async context manager with the hardening above, health wait with a
  timeout, the context size from the benchmark's batch budget).
- The benchmark imports these from `src`, so there is one implementation.
- Tests: a stand-in `llama-server` (a small Python program on loopback that serves
  `/health` and `/v1/chat/completions`) for start, stop, interrupted start, a taken port,
  a crash and a missing key; the Job Object test runs on Windows only.

Done. What the real `llama-server` b12015 showed, and the code now relies on:

- Only `/health` and `/v1/health` answer without the key; `/v1/models`, `/props`,
  `/tokenize` and the chat endpoint answer HTTP 401 to a missing or wrong key. So "ours"
  means: a wrong key gets 401 from `/v1/chat/completions` and Forewit's key gets 200 from
  `/props`, which runs no model. A program holding the port is sent `{}` and never an email.
- `--no-webui` is deprecated; the flag is `--no-ui`.
- A prompt over the context size gets HTTP 400 before the stream starts, so it is a
  rejected request (that message alone fails), not a broken server.
- At the default verbosity its output holds no prompt text; it is kept in memory anyway.
- It listens before it loads the model, and fails to bind within 0.2 s ("couldn't bind HTTP
  server socket"). Windows takes about 2 seconds to refuse a loopback connection nothing
  listens on, so health checks connect with a 0.5-second limit.
- Through `LlamaServer` and `LocalModelProvider`, the 4B model was ready in 3.7 seconds on
  the benchmark laptop, and one email (376 output tokens) passed `validate_candidate`, with
  requests to 127.0.0.1 only. The benchmark's 10 held-out emails, run through the same code
  with the 1.7B model, all validated.

### Part 2 — Runtime and model setup

- `providers/local/manifest.py`: per platform, each file's pinned URL, size, SHA-256,
  license and provenance.
- `services/local_setup.py`: status (not set up, downloading, ready, damaged,
  unsupported), a resumable download to a temporary file, size and SHA-256 checks, then an
  atomic move into `AppLocalDataLocation/local-ai/`; archive extraction that refuses
  absolute paths and `..`; a free-space check first; Remove frees the disk. A ready file is
  checked by size and recorded digest at launch, and hashed again only when it changed.
- A device check: total memory (`GlobalMemoryStatusEx`, `sysctl hw.memsize`) and the GPUs
  `llama-server --list-devices` reports, to say "about 15 seconds per email" or "about 30",
  and to warn below the benchmark's 16 GB, where nothing has been measured yet.
- Setup is the only local-AI step that uses the internet: only on the owner's action, only
  to the manifest's hosts (redirects limited to them), and it carries no mail data.
- CLI: `forewit-gmail-diagnostic local-ai status | setup | remove`.
- Tests with respx and synthetic archives; no real downloads.

### Part 3 — Mode, consent and limit

- Migration 0025 (additive): `analysis_mode` (`cloud`/`local`, server default `cloud`) and
  `local_message_limit` (1–20, default 20) on the preferences row; the preferences domain,
  service, Settings model and `preferences show`.
- `services/consent.py`: read, grant and revoke the local consent; `LOCAL_DISCLOSURE_VERSION`
  and its disclosure lines, shared by the CLI and the desktop. Restore clears it.
- CLI: `ai-consent local grant` (shows the disclosure, needs a typed `yes`) and
  `ai-consent local revoke`; `ai-consent status` lists it.

### Part 4 — The brief in local mode

- One factory, `analysis_provider(settings, preferences)`, gives Groq in cloud mode and the
  local provider in local mode; `compose_brief`, the desktop runtime and the CLI use it.
- `BriefService` in local mode: the shortlist limit is `local_message_limit` (ranking
  accepts up to 20 then); no per-run question once the local consent is active. Without
  it, a manual run asks once with the local disclosure (first use) and an automatic run
  syncs, ranks and reports what is ready, downloading no body. Without a ready model, new
  messages are left out with `LOCAL_AI_NOT_READY` and cached ones still make a brief, as a
  missing Groq key does today.
- `AnalysisService.execute` takes an optional `allowed` check, run before each call, and
  aborts the call in progress on cancel. A revocation during a run stops it like Cancel;
  results already validated stay cached.
- The brief's coverage records provider `local` and the model; the brief, the Briefs page
  and `brief --show` say "Summarized on this computer". A message not analyzed shows its
  metadata and why, never an invented summary.
- **Local-only boundary tests**, over a synthetic 20-message overnight fixture with
  sensitive, ordinary and ambiguous emails (built from `overnight-20.json`):
  - every HTTP request outside 127.0.0.1 fails the test (respx), and the Groq factory is
    replaced by one that fails the test if entered;
  - revoking mid-run: no further body download or model call;
  - model missing or failing: nothing goes to Groq, the run reports `LOCAL_AI_NOT_READY`;
  - Cancel during a call ends it within a second;
  - a restart reuses every cached result and analyzes nothing again; a new model or prompt
    version analyzes once more;
  - no fixture body text in logs, exceptions or the database beyond the bounded derived
    fields (marker words, as in M4's live check 9).

### Part 5 — Desktop

- Settings > AI: **Where email is analyzed**: On this computer, or With Groq. Choosing
  this computer shows the disclosure, then setup: the download with its size, progress and
  Cancel, the device note, then "Ready: Qwen3-4B, about 15 seconds per email", with Remove
  model and Turn off (revoke).
- A run in local mode shows no consent dialog; progress reads "Starting the local model…",
  then "Analyzing on this computer: 3 of 12"; Cancel works within the email in progress.
- Quit stops the server without waiting on a dialog.
- Built with the `/mailbrief-ui` skill; gallery screenshots and the contrast tests.

### Part 6 — A local prompt of its own

- A new synthetic development fixture (`scripts/local_ai/dev-30.json`), kept apart from
  the two acceptance fixtures.
- The benchmark's prompt findings: action required decided apart from the category,
  follow-up examples, no one-time codes in summaries. A Python check of relative dates (the
  baseline's `find_deadline` as a cross-check) is kept only if it improves the held-out
  deadlines without new failures.
- A new local prompt version, so cached local results are redone once.
- Acceptance with the benchmark: on overnight-20 and held-out-10, no fewer valid results
  than with Groq's prompt, no echoed secret or injected text, and follow-up signals found.

### Part 7 — ADR, docs and validation

- ADR 0023: the side process, delivery and integrity, the mode, the separate consent and
  no cloud fallback.
- `docs/p0-b2-local-analysis.md`: setup, what is read and kept, switching modes, revoking,
  troubleshooting and the live acceptance checklist:
  1. 20 new overnight emails, sensitive, ordinary and ambiguous, are summarized with no
     per-message question.
  2. During that run, `llama-server`'s connections (`Get-NetTCPConnection -OwningProcess`
     on Windows, `lsof -i -a -p` on macOS) are loopback only; with its outbound traffic
     blocked by the OS firewall, analysis still works.
  3. Revoking mid-run stops body reads; a restart reuses the results.
  4. Cold and warm runs' times to first result and to all 20, p50 and p95, are recorded
     for P0-B3's thresholds.
- AGENTS.md: status, layout (`providers/local/`, `services/local_setup.py`) and
  invariants: local mode never composes a cloud provider; the local consent is separate
  from Groq's; the side-process hardening; `llama-server` output never reaches logs or
  files; downloads only on the owner's action, from pinned hosts, with digests checked.
- `docs/project-status.md` and the P0-B entry in `future-implementations.md`.

## Verification

Each part: Ruff, mypy for native, Windows and macOS, and pytest with the coverage gate
(90% for every module in `services/`). The whole PR: the UI gallery, CI on the pull
request, the benchmark rerun for Part 6, and Part 7's live checklist on the owner's
Windows laptop. macOS stays "awaiting acceptance" until it is run on a Mac.

## Risks

- macOS is unmeasured. A file Forewit downloads itself isn't quarantined, but Gatekeeper
  policy could change; confirm on a Mac before claiming macOS support.
- Antivirus may scan or hold a newly downloaded executable on Windows; setup must report
  that plainly rather than as a damaged download.
- About 2.6 GB of disk, and 16 GB of RAM for the measured speed. Smaller computers get a
  warning, and an honest "unsupported" when the model won't load, until a better small
  model is measured.
- Updating llama.cpp or the model is a manifest change plus a benchmark rerun, never
  automatic.
- Until P0-B3, a local run holds the run state for minutes; the progress text and Cancel
  are what make that acceptable.
