# Local email analysis

Experimental: disabled by default. Set `MAILBRIEF_EXPERIMENTAL_LOCAL_AI=true`
before launching the app or CLI to enable local setup and inference. This does not
grant consent or start downloads. With the flag off, an existing local-mode profile
cannot run analysis and never falls back to cloud; saved work, revoke and removal
remain available. This experimental feature is not required to complete P1–P4.

Forewit can summarize selected Gmail messages on this computer. A brief covers
**today since midnight in your saved time zone**, or the past day you explicitly
choose in Briefs. It is not an unlimited overnight catch-up: yesterday's late mail
belongs to yesterday's brief. Use Inbox and Briefs to review other mail.

The local limit is 1–20 eligible messages per run, default 20, after exclusions and
ranking. Cloud mode has its existing separate limit of at most ten. Selecting local
mode never authorizes a cloud fallback.

Local calls analyze one message at a time with at most 2,048 output tokens, matching
the benchmark budget. A lower saved AI output limit still applies.

## Set up

1. Connect Gmail normally. In Settings > Preferences, choose **On this computer**
   under Email analysis and set Local messages per brief. Save preferences.
2. Choose **Set up local AI…**. Review the approximately 2.5 GB download, sources,
   licenses and device note. License texts are available in the dialog's details.
   Download and set up installs the pinned runtime/model; Stop cancels work and a
   subsequent setup resumes partial downloads.
3. Choose **Sync and summarize**. The first local run asks permission before any
   shortlisted body is read for AI. It names the local scope; accepting allows
   subsequent manual and enabled automatic local runs without repeated questions.
4. Read saved and completed summaries while the run progresses. You can capture/save
   a task or write while local analysis continues. Stop ends the current run; completed
   analyses stay cached, and the next run reconstructs remaining eligible work.

Setup alone does not grant mail-processing permission. Granting permission alone does
not switch mode or enable a schedule. Automatic runs use the existing refresh-on-launch
or hourly schedule only while the desktop is open. No native closed-app scheduler is
installed. Automatic cloud permission is a separate setting and is cleared on mode changes.

Drafting and calendar AI remain Groq features with their own approval. Choosing local
email analysis does not give them permission and does not require a Groq key for local
summaries. Choosing cloud again uses the existing Groq review/approval workflow.

## What you see and what is kept

- Metadata is synced/ranked first. Excluded senders are not read for analysis.
- Local permission is checked before body reads/calls and before publication. A change
  to the saved mode/preferences or revoked permission stops the current local run.
- The model starts on demand. Progress names local analysis and counts ready/remaining
  work in this bounded selection. Partial model tokens are never shown as a summary.
- The in-progress view includes saved and newly committed summaries, explicitly labelled
  as a run in progress. It is distinct from the canonical saved daily brief. A stopped
  run does not claim completion or replace that saved brief with a partial selection.
- Summary/action suggestions use existing validation and bounded evidence. Bodies and
  prepared model input stay in memory; full bodies, credentials and model stdout/stderr
  are not stored in the database or application logs. Gmail's existing preview snippet
  and bounded derived summaries remain under the established storage rules.
- Matching cached analyses work without starting a model. Changed input/model/prompt
  identities require fresh analysis. Saved briefs remain available offline.
- The model process stops after five idle minutes, on interrupted inference, revoke,
  removal, or quit. A failed model never starts a cloud provider.

## Revoke, remove and restore

Settings' **Revoke AI consent** stays available during work. It stops and joins active
AI work, preserves an in-flight durable owner save, then revokes consent. Saved summaries,
tasks and writing remain. The next local run asks permission again.

**Remove local AI files** stops the retained model and removes downloaded runtime/model
files. It does not remove saved briefs, actions or writing. Set up again to restore the
files. Keep the chosen mode explicit; missing files do not select cloud mode.

Backup/restore handles the application database, not model downloads or OS-vault
credentials. Restore preserves mode/limits but revokes local permission and clears
automatic cloud allowances. Reconnect the intended Gmail account and opt in again.

## Command line

Set the analysis mode and local limit in desktop Preferences; `preferences show` reports
them. Local permission is account-scoped: connect/sync that Gmail account first.

```text
forewit-gmail-diagnostic local-ai status
forewit-gmail-diagnostic local-ai setup
forewit-gmail-diagnostic local-ai licenses
forewit-gmail-diagnostic ai-consent local grant
forewit-gmail-diagnostic ai-consent status
forewit-gmail-diagnostic brief --show
forewit-gmail-diagnostic ai-consent local revoke
forewit-gmail-diagnostic local-ai remove
```

The first grant requires typed `yes`; `brief --yes` cannot grant first-use permission.
Local revoke needs no credential-vault access and revokes old local disclosure versions
too. Plain `ai-consent revoke` retains its explicitly Groq-only scope.

For a custom `--database`, local files are in `local-ai` beside that database. Point
`local-ai --folder` at that same directory. The desktop/CLI single-profile lock still
prevents concurrent database commands against an open desktop.

## Troubleshooting and speed

`LOCAL_AI_NOT_READY`: run setup/status; cached content remains readable. A failed or
damaged installation can be repaired by repeating setup. `LOCAL_AI_FAILED`: retry when
the computer is ready; there is no provider fallback. A request-budget message means
the saved request cap was reached; cache hits consume no model requests.

Windows x64 and Apple Silicon macOS have pinned manifests. Other platforms, Intel Macs
and Windows ARM are not enabled by this implementation. The reference measurements used
16 GB RAM; less memory can be slow or fail to load. A detected GPU does not guarantee
the reference speed. On this validation laptop's Intel UHD GPU, the original-prompt
development run had 28.3 s call p50 and 60.4 s p95 for short synthetic messages. These
are observations, not a promise for a different device, input or prompt.

## Acceptance register

Automated/synthetic verification is recorded in [implementation progress](p0-p2-implementation.md)
and the [staff review](p0-local-pipeline-review.md). The dedicated prompt's quality
comparison is still running. Do not infer acceptance from valid JSON alone.

The following owner/native checks remain open until their evidence is recorded:

- [ ] Windows packaged app: setup, interruption/resume, remove, mode changes and quit.
- [ ] Windows real account: twenty eligible messages, account isolation, sender
  exclusions, more than twenty Inbox messages, and yesterday 23:00/today 01:00 coverage.
- [ ] During real inference, sample the child process's network endpoints; verify
  loopback-only operation with outbound traffic blocked by the OS firewall.
- [ ] Revoke while reading a body and while inferring; confirm no late result is
  published and a subsequent run reuses completed cache entries.
- [ ] Cold/warm time to first summary and all twenty; p50/p95, memory and power behavior
  on the owner's actual Windows device, without treating estimates as measurements.
- [ ] macOS native execution: the equivalent lifecycle, privacy, cancellation,
  recovery and performance checks on a Mac; type checking is insufficient.
- [ ] Existing real-record backup/recovery and the 7–14-day owner trial in
  [M9 live validation](m9-live-validation.md).

Native scheduling and additional cloud enrichment remain deferred submilestones, not
completed features. The full P0 roadmap and live acceptance are not closed by this guide.
