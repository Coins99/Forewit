# P0-B2 handoff: where the work stands

A working checklist for whoever continues P0-B2 on any computer. **Delete this file in the
commit that finishes Part 7**, and remove its mention from AGENTS.md. The plan itself,
`docs/p0-b2-local-analysis-plan.md`, stays: it has the design, the owner's four decisions
and the details of every part.

Branch: `feat/local-ai-pipeline`, one pull request to `main` for all seven parts (P0-B1 is
already on `main`). Commit each part with its tests, then push only this branch: this
checkout's `push.default` is `matching`, so a plain `git push` also tries every other branch.

## Parts

- [x] **Part 1 — local provider and side process** (`1c619d1`): `providers/local/`
  (`transport.py`, `provider.py`, `server.py`), tests with a stand-in server in
  `tests/unit/providers/local/`, and the benchmark running this code.
- [x] **Part 2 — runtime and model setup**: `providers/local/manifest.py` (pinned URLs,
  sizes, SHA-256, licenses), `services/local_setup.py` (status, resumable download, digest
  check, safe extraction, free-space check, remove), `providers/local/device.py` and the
  `forewit-gmail-diagnostic local-ai status | setup | remove` CLI.
- [ ] **Part 3 — mode, consent and limit**: migration 0025 (`analysis_mode`,
  `local_message_limit`), the `local` consent (`local-1`), cleared by restore, and the
  `ai-consent local grant | revoke` CLI.
- [ ] **Part 4 — the brief in local mode**: `analysis_provider()` factory, a host that
  starts `LlamaServer` lazily and stops it after 5 idle minutes, no per-run question,
  revocation and mid-call cancel, the "Summarized on this computer" label, and the
  local-only boundary tests.
- [ ] **Part 5 — desktop**: Settings > AI, setup progress, run progress and quit (use the
  `/mailbrief-ui` skill).
- [ ] **Part 6 — a local prompt of its own**, tuned on a new development fixture.
- [ ] **Part 7 — ADR 0023, docs and the live checklist**, then delete this file.

## Before starting on another computer

- Run `uv sync --locked --all-groups`. A venv made before the Forewit rename h- Real `llama-server` checks need the pinned runtime and model. Since Part 2, run
  `forewit-gmail-diagnostic local-ai setup` (about 2.5 GB, checked against the pins; add
  `--folder PATH` to keep them out of the app's data). A partial `.part` file in the folder's
  `downloads/` is resumed, so a copy of an already downloaded model saves the download.
he first computer they are in `../local-ai/`. Part 2
  pins these same files in the manifest.
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
