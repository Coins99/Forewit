# Windows desktop executable

The Windows single-file build is `out/dist/onefile/Forewit.exe`. Copy **only that file**
to your Desktop and double-click it. It bundles Python and the application's libraries;
no Python installation, source checkout, installer or `_internal` folder is required.
Initial acceptance targets Windows 11 x64. The build is unsigned.

## Setup and saved data

Configure your Google Desktop OAuth client file and Groq model/key in Settings, then
connect Gmail. Keep the selected OAuth client JSON in a stable location: Forewit saves
its path, not a copy of the file. No owner configuration or credentials are in the EXE.
See [Gmail setup](gmail-setup.md) and [AI setup/consent](ai-analysis.md).

Saved work stays in the existing Qt `AppLocalDataLocation` for the `Forewit` organization
and application. It is independent of the Desktop, executable filename and working
directory. Tokens and the API key remain in Windows Credential Manager. Moving/renaming
the EXE retains the same profile; two copies cannot write that profile concurrently.

Copying the EXE to another computer transfers the application only. Use Data and recovery
for backups/restores and configure credentials separately. Deleting the EXE does not
delete your profile or credentials.

## Startup and updates

The single-file build extracts dependencies into a temporary folder at each launch.
Allow a few seconds for the window to appear. Normal exit removes extracted files;
force-stopping a process may leave temporary files. Saved work is never kept there.

For an update, download the Windows x64 file, verify the release's SHA-256, make a data
backup, close Forewit completely, then replace the Desktop EXE. Keep the previous
executable until the new build is verified. Windows may refuse replacement until shutdown
finishes. A release filename such as `Forewit-v0.1.0-windows-x64.exe` can be renamed to
`Forewit.exe`.

Replacing the EXE does not reverse a database upgrade. An older version may refuse a
newer database. Use a compatible executable and the retained pre-upgrade/M9 backup;
preserve the upgraded database and any new writing before restoring. See
[recovery](m9-durability.md).

Windows may report an unrecognized publisher for this unsigned candidate. Record any
security block during acceptance; do not disable system protections. A bootloader failure
before Python starts may produce no `desktop.log`. Diagnose those failures with an
isolated console build rather than logging credentials or email content.

## Build and verify

Run on Windows from the locked Python 3.13 development environment:

```powershell
uv sync --locked --all-groups
uv run python scripts/build_desktop.py --onefile
uv run python scripts/check_package.py --layout onefile --executable out/dist/onefile/Forewit.exe
```

The build generates its multi-size icon from the bundled Tabler mail artwork and current
theme tokens, plus Windows version information and bundled notices. It creates a
`Forewit.manifest.json` with the EXE hash, architecture, dependency/build identity and
embedded inventory. A local build records whether the source is dirty; release staging
refuses dirty or mismatched builds. The inventory checks filenames and required resources,
not arbitrary disguised secrets; build only from explicit, clean inputs.

The checker copies only the EXE to a temporary path containing spaces/Unicode and launches
it from another working directory. It uses disposable synthetic data, offscreen 100%/200%
rendering and native Windows rendering, cross-process persistence, live/released/dead
profile locks, backup/export/cleanup, and old/unknown schema checks. No live Gmail, vault
credentials or paid AI are used. Normal-exit extraction cleanup is checked separately.

`--onefile --console` produces the diagnostic build under `out/dist/onefile-console/`.
The existing default build still produces the full folder at `out/dist/Forewit/`; its
dependency folder must stay together. macOS continues using `Forewit.app`.

Notices are embedded and also accompany releases. Export them without overwriting an
existing file:

```powershell
.\Forewit.exe --export-notices .\Forewit-notices.txt
```

## CI and releases

PR/main CI builds and checks both Windows layouts and the macOS app. Windows one-file
artifacts are retained for three days and named with their source commit. They are build
candidates, not evidence of the owner's live acceptance.

The Windows draft-release workflow runs for version tags. A stable tag must match both
project/runtime versions and identify a commit on main. It reuses the full CI workflow,
stages the tested EXE, manifest, notices, instructions and checksums, and verifies those
assets again after transfer. Only the final draft job has repository write permission.
Reruns verify an identical existing draft; they refuse changed or published assets.
A rebuild can produce different bytes, in which case the rerun deliberately fails rather
than replacing the existing draft. Byte-for-byte reproducibility is not established.

The release remains a **draft** until native clean-profile and live acceptance are
recorded and the owner publishes it. The workflow does not install updates on your PC.
It creates a Windows candidate; it does not advertise a new cross-platform release.

## Validation record

Implementation checks and human acceptance are recorded separately in
[the implementation plan](windows-single-executable-plan.md) and
[the platform checklist](m5-acceptance.md). After [code review](windows-executable-review.md),
the locally verified EXE is 49.7 MiB; 3,154 tests passed with 97.05% coverage.
Both Windows package layouts and workflow syntax checks
passed. Remote CI/release execution and macOS packaging are still pending.
Clean-machine startup, real-account recovery, Windows security
behavior and the product's 7-14-day journal still require owner acceptance.
