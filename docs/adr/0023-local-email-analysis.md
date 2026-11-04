# ADR 0023: local email analysis

Status: accepted architecture; quality, native and owner acceptance remain tracked
in [local analysis](../p0-b2-local-analysis.md). Date: 2026-11-01.

## Decision

Use the existing analysis port and validation/cache pipeline with a separately
versioned local prompt. Run pinned llama.cpp b12015 as a side process, with pinned
Qwen3-4B-Instruct-2507 Q4_K_M weights. There is one retained process and one inference
lease per desktop runtime. Start lazily and stop after five idle minutes; stop and
join on cancelled/failed inference, removal, revocation and application shutdown.
CLI-owned hosts close at command exit. Do not add a model SDK or application dependency.

Cloud stays the default. The owner explicitly chooses local mode, sets its separate
1–20 message limit (default 20), and grants account-scoped `local-1` permission before
mail bodies are read for local AI. This permission covers manual and enabled open-app
automatic runs. Granting it does not select a mode or enable a schedule. Local and
Groq consents authorize separate processing. Mode changes clear old automatic cloud
allowances atomically. Restore revokes local permission and clears automatic grants.

The adapter factory's local branch never enters a cloud factory. Failure cannot change
provider. AI drafting and calendar planning remain explicit, separately approved Groq
purposes. Sender exclusions apply before body selection and cannot be overridden by
an AI result. The local cap does not broaden today's local-day or tracked-reply rules.

Before each body read/model call, and before publishing, check current local mode,
preferences revision and the current account's local consent. A watcher interrupts
in-flight work when that eligibility changes. Watchers use independent, short-lived
database sessions so cancellation cannot invalidate the run transaction. No watcher
or side process outlives its operation. Results already committed remain cached.

Keep bodies/prepared input and model process output in memory. Keep only existing
bounded, validated derived content and its established source/cache identities.
Each validated analysis commits independently. Project committed results into an
explicit in-progress UI, retaining stable suggestion IDs and selection; finalize the
canonical daily brief only under its cumulative coverage/carry rules. Owner tasks and
writing can be saved independently while the local run continues. Stop reconstructs
remaining work from the ranking/cache on the next explicit run; there is no durable
job database, lease protocol or exact pause/resume promise in this increment.

## Delivery and process boundary

Setup is an owner action with a size/source/license disclosure, progress and Stop.
It reaches pinned HTTPS hosts through the existing per-redirect allowlist, checks
SHA-256 and bounded sizes, and safely extracts a platform-specific runtime. Resumable
downloads are setup artifacts, not mail storage. Cancellation joins disk workers before
releasing the setup lock, preventing an installation/removal race. Setup is device-local
and excluded from backups. Updating the manifest requires renewed benchmark evidence.

The server binds a random port on 127.0.0.1 with a fresh per-launch API key, no web UI,
one inference slot and authenticated ownership checks. Keys and output never enter
application logs/SQLite. Windows uses a Job Object; the existing macOS leftover-process
record uses identity checks before termination. HTTP inference uses `LoopbackGuard`
and ignores proxy environment variables. These application controls do not establish
an OS firewall sandbox for the native child: observed child connections and firewall
acceptance must be recorded separately.

The runtime/model notices are bundled, shown in setup details, available offline through
`local-ai licenses`, and audited as package resources. Sources:
[llama.cpp b12015 MIT notice](https://github.com/ggml-org/llama.cpp/blob/b12015/LICENSE),
[Qwen Apache-2.0 notice](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/main/LICENSE).

## Pinned chat-template review

The downloaded GGUF matched the existing pinned model digest. Its 4,051-byte
`tokenizer.chat_template` has SHA-256
`c979e0e71a3e21b8f208e6ab120d5cb29327885f29d2a8b18fda67a723798e18`.
Read-only inspection found message/role formatting, string operations, loops and JSON
formatting. Forewit sends system/user messages and a response schema, without tools;
the template's tool branches are not enabled. Email content is data interpolated into
that fixed template, not a second template to evaluate. Forewit has no model tool
executor. This review does not assert a general safety property for arbitrary GGUFs
or future runtime/template versions; delivery pins remain essential.

## Tradeoffs and acceptance

The model uses several GB of RAM/GPU memory and may take minutes for a selection.
Device speed text is a benchmark reference, not a promise for every detected GPU.
The original prompt produced valid JSON while missing many useful actions/follow-ups
and echoing a code; validity alone is not acceptance. Tune on the independent `dev-30`
fixture, then freeze the candidate and compare the untouched acceptance fixtures.

Native closed-app scheduling, durable execution state and additional cloud enrichment
remain named deferred submilestones under the simplified implementation sequence.
Do not describe them, owner recovery trials, macOS execution or the full P0 roadmap
as complete because this local pipeline compiles or passes synthetic tests.
