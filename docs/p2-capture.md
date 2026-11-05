# P2-02: global Task/Note capture

Implemented locally on 2026-11-03, continuing the P2 navigation work on
`feat/p2-navigation`. Windows engineering checks passed for this checkpoint;
native macOS and owner acceptance remain open. This is not full P2 acceptance.

## Owner workflow

Choose **Capture** from any page, press **Ctrl+Shift+N** (**Cmd+Shift+N** on macOS),
or choose **Capture task or note** in the command palette.

- A Task needs only a title. **Add notes** expands optional notes; other task
  organization and planning fields are available through **Open** after saving.
- A Note can contain a title, text, or both. Plain text, including literal Markdown,
  uses the existing writing store and version/editor/export behavior.
- **Save** creates an ordinary owner record. **Open** navigates to it, **Capture
  another** starts a fresh request, and **Done** returns to the current page.
- P2-03 adds **Add to today** for a saved Task, selecting that canonical task in
  [My focus](p2-focus.md) without changing its dates or creating it again.
- Failed or uncertain saves retain text and request identity. Retrying the same
  capture is safe. If committed input differs or the saved result was removed, the
  application keeps the current input and explains why it cannot reuse that save.
- Closing typed input offers saving or explicit **Discard and close**. Empty cancel
  creates nothing. The main quit guard also includes an unsaved capture.

Gmail/AI setup and an account are unnecessary. Saves remain available while email
work is waiting, without replacing the current task/note editor. Existing New task,
New note and their shortcuts remain available. Ctrl+N / Cmd+N keeps its meaning.

## Implementation and durability

`domain/capture.py` validates owner input; `ui/capture_view.py` provides the guarded
form. `DesktopRuntime.capture` uses an independent local session and delegates task
creation to the existing retry-safe action command. Notes use
`DraftService.create_note`, saving initial text/version/receipt atomically; see
[ADR 0025](adr/0025-retry-safe-note-capture.md).

Capture has its own bounded in-flight owner write rather than joining the network
operation's cancellation/foreground slot. Shutdown waits for it before closing
storage. Failed view refresh cannot turn a confirmed save into an uncertain save.
Successful capture refreshes local lists while preserving current owner writing;
opening another record still uses the existing save/conflict/navigation guards.

Migration 0027 adds only note-create control receipts. Backup/verify/restore covers
them; portable writing exports omit them; deleted-writing cleanup retains them.
The packaged synthetic recovery check now creates a captured note and retries its
command after restoration, requiring one canonical note to remain.

## Verification register

- Domain/service/recovery checks: 110 passed and one Windows symlink skip.
- Migration/package-scenario checks: 38 passed.
- Initial UI/app checks: 48 passed; final capture-specific checks: 20 passed,
  including confirmed-save/failed-refresh and preservation of existing owner edits.
- Gallery/theme checks: 293 passed and one intentional render skip at each of 1x
  and 2x before the two additional regression cases. Final capture-specific checks
  passed all 20 cases; both 2x dialog renders passed separately. Task/Note forms
  were visually inspected in light/dark at 1x/2x, along with the global header action.
- Keyboard checks: 18 passed, including actual Ctrl+Shift+N activation distinct from
  Ctrl+N. The initial full run's six old Tab-order expectations were updated to
  include Capture; no production source change was needed for those assertions.
- Final full Windows regression: **5,022 passed**, nine platform/intentional skips,
  25 warnings; **95.87%** combined coverage. Every required service clears the 90%
  combined statement/branch gate. Ruff lint/format and win32/darwin strict typing
  passed for the source/test tree (475 files in each typing run).
- Windows x64 single-file build and package checks passed: offscreen 1x/2x,
  native Windows startup, repeated persistence/verification, renamed-copy profile
  locking, old-schema upgrade and synthetic backup/verify/restore/export/cleanup.
  Restored capture retry preserved one canonical note. Checks used disposable data.

Verified local executable SHA-256:
`469ea8cf14d952b5e1f02e30ab08da28d18f3282de1e0be6b1054554dcce607a`.
This is an engineering build from the local working tree, not a published release.

No automated check uses a real mailbox, credential vault, paid AI or owner profile.
Native macOS and owner usability acceptance remain open. Safe logs live under ignored
`out/p2-capture-*`, renders under `scratch/p2-capture-shots/`.

## Later checkpoints

Daily focus/day plans come next (P2-03); Add to today will use that service once it
exists. Optional capture review/processed state belongs to P2-06. Current captures
are usable ordinary records with no mandatory processing step or inferred dates.
A captured note is organized later on the Notes page ([P2-04a](p2-notes.md)). Capture
review (P2-06), the Projects page, the Mail/sidebar migration and broader search followed in
PR #102 ([the workspace register](p2-workspace.md)). No P1/M4–M9 owner acceptance or
experimental AI acceptance is implied.
