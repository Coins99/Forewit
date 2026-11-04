# ADR 0024: retry-safe task creation

Date: 2026-11-01. Status: implemented; native/owner acceptance remains separate.

## Problem

Optimistic revisions protect updates, but a create whose response is lost after commit
can be retried as a second task. A failed editor save must keep both the owner's input
and the identity of the request being retried.

## Decision

`ActionService.create(..., command_id=UUID)` records a receipt in `task_commands`, in
the same transaction as the task, steps, recurrence and activity. A unique command ID
permits one effective creation. The optional argument preserves existing callers;
calls without it continue to mean independent creates.

The input fingerprint is SHA-256 over canonical versioned JSON containing the normalized
command UUID, operation, complete action edit, ordered steps, recurrence and deadline
request. The random command ID salts the fingerprint. This is an input-identity check,
not encryption or a promise that low-entropy input cannot be guessed. No title, notes,
step text, email body or credential is stored in the receipt.

A receipt contains only command ID, operation (`task.create`), fingerprint, canonical
result public ID, initial result revision and UTC creation time. Retrying identical input
returns the current canonical task, retaining later owner edits/completion and making no
new activity. Changed input is refused. Replay is checked before new-task project/date
validation, so later project archival cannot turn the retry into a new task.

On a concurrent unique-key conflict the entire losing transaction rolls back before
the winning receipt is read. Unrelated integrity failures still propagate. Receipt and
owner records both roll back on ordinary failed writes; a retry after a successful commit
with a lost response finds the receipt.

## Editor and lifecycle

The editor generates one UUID for each new-task session. Failed saves retain it;
starting a genuinely new task generates another. A changed-input conflict preserves
typed fields and asks the owner to check saved tasks. It never silently creates another
record or overwrites the first one. Existing shared save/close guards remain in use.

Receipts have no task foreign key and are retained for the profile's lifetime, including
after permanent task purge. A retry of a soft-deleted or purged result is refused, never
restored or recreated. Explicit owner restoration of an existing task remains independent.
No receipt-cleanup window is introduced that could make old command IDs create again.

## Migration, portability and recovery

Additive migration `20261101_0026` follows `20261101_0025`; existing tasks receive no
invented receipts. Full database backups, verification and restore include the table
through the shared schema contract. Receipts survive restart and restoration of a
snapshot that contains them. A snapshot predating a command naturally contains neither
its receipt nor its task; this is not a distributed command/replay protocol.

Writing export deliberately excludes this control metadata. It remains a portable owner
record export, not an idempotent import/replay mechanism. Manual deleted-writing cleanup
does not delete receipts. Downgrade drops the receipt table while preserving owner tasks;
retry protection is lost by that explicit downgrade and is not reconstructed on re-upgrade.

No provider, network, background queue, new application dependency or second task model
is introduced. Other create operations can adopt scoped receipts when their retry paths
are implemented; this change covers manual task capture only.
