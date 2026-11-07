# P3 scheduling add-ons

Base: merged main `54e27c9` (#105–#108). Branch: `feat/p3-scheduling-addons`.
Contract: [ADR 0031](adr/0031-scheduling-addons.md).

## Use

1. Calendar → Working hours: retain weekly hours, optionally enter independent before/
   after buffers and dated replacements. Each replacement is one line such as
   `2026-11-09 = 10:00-12:00, 13:00-16:00`; `2026-11-10 =` means no work that day.
2. Open a scheduled block and choose Lock block. Existing time remains reserved;
   unlock to edit or unschedule it. Task dates, estimates and activity are unchanged.
3. Calendar → Tasks to schedule → Suggest time: choose the first/last date, find tasks,
   add selected tasks,
   optionally set total remaining demand and splitting, then Review. A manual duration
   is total remaining demand before crediting valid future reservations.
4. Read the times, unplaced reasons, conflicts and knowledge limitations. Choose whole
   proposed tasks to Apply. Incomplete external knowledge requires acknowledgement;
   it never means Google is known to be empty. The planner itself makes no network call.
5. Undo removes an unchanged applied batch together. A stale review must be regenerated.
   After an uncertain save, Retry save uses the same command and freezes edits until the
   result is confirmed. The manual block drawer provides the same retry protection.

Existing direct manipulation, Busy/Free, daily workload and Plan with AI retain their
roles. The local suggestion review does not replace the daily capacity display.

## Database compatibility

The first run upgrades a main-line profile through additive migrations 0034–0036,
retaining the pre-upgrade snapshot behavior. Backup/restore and writing exports include
new owner fields; command receipts remain backup-only. Google knowledge is cleared on
restore and remains excluded from writing exports and cleanup fingerprints.

The divergent local candidate needs the separate-copy conversion described in ADR 0031.
Keep its original database and checkout available until the converted copy is verified.
No real owner database was used for automated engineering checks.

## Verification register

Final source, migration, UI, coverage and package results are recorded here after checks
complete. Owner acceptance and native macOS execution are not implied by automated results.

## Hands-on acceptance still required

- Native macOS/Cocoa package and offline lifecycle on an actual macOS session.
- Narrator/VoiceOver reading of dates, task choices, warnings and review controls.
- Physical Windows 125%/150% display interaction.
- Ordinary week with split hours, a dated day off, Busy/Free events, a locked block,
  stale review, uncertain retry, batch Undo and a backup/restore round trip.
- If needed, conversion of an actual old development profile to a separate destination,
  verifying records and hour-zone assumptions before using the converted profile.
