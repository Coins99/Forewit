# P0-B2 handoff: where the work stands

A working checklist for whoever continues P0-B2 on any computer. **Delete this file in the
commit that finishes Part 7**, and remove its mention from AGENTS.md. The plan itself,
`docs/p0-b2-local-analysis-plan.md`, stays: it has the design, the owner's four decisions
and the details of every part.

Parts 1–2 merged into `main` in PR #97. Part 3 continues on `feat/p0-local-consent`
from `main` at `d8e7b9b`. Review its diff and test evidence before continuing with
Part 4. When pushing any branch, name it explicitly; do not push unrelated branches.

## Parts

- [x] **Part 1 — local provider and side process** (`1c619d1`): `providers/local/`
  (`transport.py`, `provider.py`, `server.py`), tests with a stand-in server in
  `tests/unit/providers/local/`, and the benchmark running this code.
- [x] **Part 2 — runtime and model setup**: `providers/local/manifest.py` (pinned URLs,
  sizes, SHA-256, licenses), `services/local_setup.py` (status, resumable download, digest
  check, safe extraction, free-space check, remove), `providers/local/device.py` and the
  `forewit-gmail-diagnostic local-ai status | setup | remove` CLI.
- [x] **Part 3 — mode, consent and limit**: migration `20261101_0025` (`analysis_mode`,
  `local_message_limit`), the `local` consent (`local-1`), cleared by restore, and the
  `ai-consent local grant | revoke` CLI. Restore revokes every local disclosure without
  changing mode. Saving unrelated
  desktop preferences preserves mode/limit. See `p0-p2-implementation.md` for the review log.
- [x] **Part 4 — the brief in local mode**: `analysis_provider()` factory, a host that
  starts `LlamaServer` lazily and stops it after 5 idle minutes, no per-run question,
  revocation and mid-call cancel, the "Summarized on this computer" label, and the
  local-only boundary tests.
- [x] **Part 5 — desktop**: Settings > Preferences has email mode, local limit and setup;
  setup disclosure/progress/Stop, local run progress, revoke during work and joined quit.
  Gallery checked at 1x/2x under `/mailbrief-ui`; native/live acceptance remains open.
- [ ] **Part 6 — a local prompt of its own**, in progress: independently authored
  `dev-30.json`, a versioned local prompt and a real-model original-prompt comparison.
  Frozen acceptance fixtures remain unchanged; quality gate is not passed yet.
- [ ] **Part 7 — ADR 0023, docs and the live checklist**, then delete this file.

## Before starting on another computer

- Run `uv sync --locked --all-groups`; it installs the current Forewit command aliases.
- Real `llama-server` checks need the pinned runtime and model. Since Part 2, run
  `forewit-gmail-diagnostic local-ai setup` (about 2.5 GB, checked against the pins; add
  `--folder PATH` to keep them out of the app's data). A partial `.part` file in the folder's
  `downloads/` is resumed, so a copy of an already downloaded model saves the download.
  On the first computer they were in `../local-ai/`. Part 2 pins these files in the manifest.
- The server tests start a stand-in server as real processes, about 30 seconds in all.

## Facts Part 1 found (keep relying on them)

- llama-server b12015 answers only `/health` and `/v1/health` without the key, so
  `LlamaServer` treats a server as ours when a wrong key gets 401 from
  `/v1/chat/completions` and its key gets 200 from `/props`.
- The flag is `--no-ui` (`--no-webui` is deprecated).
- A prompt over the context size is HTTP 400 before streaming: a rejected request.
- Windows takes about 2 seconds to refuse a loopback connection, so checks connect with a
  0.5-second limit; a check that times out on a busy computer is retried, never read as
  "not ours".
