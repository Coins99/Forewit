# P3 scheduling add-ons

Base: merged main `54e27c9` (#105–#108). Branch: `feat/p3-scheduling-addons`.
Engineering verification completed 2026-11-07 on Windows; owner acceptance remains open.
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

| Check | Result |
| --- | --- |
| Frozen-source Windows suite, Qt offscreen | 5,357 passed, nine platform/capability skips, 26 warnings; 859.30 seconds |
| Combined statement/branch coverage | 94.78%; every service meets its 90% gate |
| Focused scheduling/manual service checks | 53 passed; 98.45% combined coverage (scheduling 98%, work blocks 99%) |
| Ruff lint / formatting / whitespace | Passed |
| Strict mypy, Windows and macOS targets | Passed, 539 source/test/script files |
| Light/dark gallery and all-scheme contrast | 303 passed, one redundant-render skip at each of 1x and 2x |
| Final drawer follow-up | 20 checks passed at each of 1x/2x; covers short-window scrolling, dated inputs and existing Calendar controls |
| Windows one-file package | Passed full/restart at 1x/2x offscreen and native Windows; copied/renamed executable locks, upgrade, export and separate-profile restore |
| Packaged add-on scenario | Passed suggestions, acknowledgement, apply/retry, batch Undo/retry, manual retry, locking and recovery of buffers/overrides/receipts in a separate synthetic profile |
| Frozen archive comparison | All 30 affected application modules and three migration resources match current source |

The final full run uses the frozen application source. An earlier broad run found old
migration-head expectations and Google-cache fixtures that omitted the newly required zone
or treated attempted reads as successful coverage. Those fixtures were corrected before
the final passing run. Final UI review moved Suggest time into the existing task panel and
made the hours fields scroll while keeping Save/Cancel reachable on short windows.

Local evidence is under ignored `scratch/` and root `scratch-*.log` files, including
`scratch-final-full-tests.log`, `scratch-package-final.log`, `scratch/frozen-verification.json`
and the gallery images. All records and provider responses are synthetic; no real profile,
mailbox, vault credential, provider request or paid AI was used.

Development executable: `out/dist/onefile/Forewit.exe`, SHA-256
`aa1aaf7062edf971c627dd214cf25127de4995d8e72457686e31af29b8992b3f`.
This is a local development artifact, not a staged release. Native macOS/Cocoa execution
and owner acceptance are not established by cross-target typing or Windows checks.

## PR 109 review follow-up (2026-11-07)

The five review findings are addressed: project choices survive reload/search/paging;
definitive retry rejections release the uncertain-save freeze; Calendar shading, Day labels
and initial scrolling resolve dated hours; scheduled totals retain individually clipped
bookings while occupancy counts their union; invalid override containers fall back to
unset hours with the saved revision so the owner can repair them.

Final review also found and fixed all-day summary counts including adjacent dates or
outside-range credit dates. A regression reproduced four commitments where only one was
inside the selected range. These fixes add 13 regression cases. The targeted run passes
64 tests; Ruff and strict Windows/macOS typing pass. Gallery and contrast runs pass
307 tests with one intentional gallery skip at each of 1x/2x. Evidence is in ignored
`scratch-pr109-*.log` files and `scratch/pr109-ui-shots/`.

The executable and frozen-source comparison above predate these review fixes; this
follow-up is source validation, and that executable has not been rebuilt. Native macOS
execution and owner acceptance remain open. The earlier PR CI's Windows job passed;
its macOS job timed out fetching the uv installer manifest before checking application code.

## Hands-on acceptance still required

- Native macOS/Cocoa package and offline lifecycle on an actual macOS session.
- Narrator/VoiceOver reading of dates, task choices, warnings and review controls.
- Physical Windows 125%/150% display interaction.
- Ordinary week with split hours, a dated day off, Busy/Free events, a locked block,
  stale review, uncertain retry, batch Undo and a backup/restore round trip.
- If needed, conversion of an actual old development profile to a separate destination,
  verifying records and hour-zone assumptions before using the converted profile.
