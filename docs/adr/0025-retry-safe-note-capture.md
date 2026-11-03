# ADR 0025: retry-safe note capture

Date: 2026-11-03. Status: implemented locally; platform/owner acceptance is separate.

## Decision

Global capture saves an ordinary task or note without Gmail, AI or organization.
Tasks reuse ADR 0024. `DraftService.create_note(content, command_id=UUID)` saves a
nonempty note, its initial content/version and a `note_commands` receipt together.
Existing blank-draft/editor APIs retain their behavior.

The scoped receipt contains the canonical command UUID, `note.create`, a SHA-256
fingerprint of versioned canonical JSON (command UUID, operation and initial
DraftEdit), result public ID, initial revision and aware UTC creation time. It
contains no owner text. The command UUID salts input identity; the digest is not
encryption. Owner writing is preserved exactly by the existing writing store.

Identical retries return the current canonical note, including later owner edits,
without another note or version. Different input and soft-deleted/purged results
are refused. A receipt-key race rolls back the entire losing create before reading
the winner; unrelated integrity failures propagate. Ordinary rollback removes the
provisional note, version and receipt together.

## Capture lifecycle

Each capture session retains one UUID through uncertain/failed saves. The type is
locked after the first attempt, so an uncertain Task retry cannot become a separate
Note operation. Text remains editable after failure; a committed different-input
retry is refused while retaining that text. A new capture after success or explicit
discard gets a fresh UUID. Concurrent submissions are suppressed.

Capture saves use a separate runtime session/task so email/network work cannot
block them. Shutdown waits for that owner write before closing storage. A confirmed
commit remains confirmed even if refreshing a view fails. Opening the saved record
is explicit and goes through canonical navigation. Closing typed input requires
saving or explicit discard; quitting includes capture in the unsaved-input guard.

This does not promise recovery of text never saved before a process crash. No
automatic replay or generic command framework is introduced.

## Migration, export and recovery

Additive revision `20261103_0027` follows `20261101_0026`. Legacy writing remains
unchanged and gets no fabricated receipts. There is no note FK on receipts: they
survive permanent purge and are retained for the profile's lifetime, preventing
old command IDs from recreating removed notes.

Full backup, strict schema verification and separate-profile restore include the
table through the shared metadata contract. Portable writing exports retain owner
notes/versions and omit control receipts, as with task receipts; no writing-format
version change is needed. Export is not an import/replay protocol. A restored
snapshot predating a command contains neither that command's receipt nor its note.

Explicit downgrade drops only receipts and preserves writing/versions, losing retry
protection for those commands. An older executable alone is not a safe rollback.
No credentials or processing permissions are added or restored by this feature.
