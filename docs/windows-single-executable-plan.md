# Windows single executable: implementation and release plan

Date: 2026-10-11. Status: implemented and locally verified; owner acceptance remains pending.
Baseline: GitHub `main` at `e122fa8a7fff4d1123183b003e174e91ed2ef29f`.
The local checkout is older and has an existing edit to `m8-daily-operation.md`.
Implement from an updated `main` in an isolated branch; preserve that local work.

Implementation is in the isolated `feat/windows-single-executable` worktree. The original
checkout is unchanged. Build/check options, embedded audit, synthetic lifecycle scenarios,
CI integration and a draft-release workflow are implemented. Local validation passed;
the new workflows have not yet run on GitHub.
The icon is generated at build time from checked-in SVG/theme inputs rather than storing
another binary asset. See [actual usage](windows-executable.md).

## Implementation validation (2026-10-11, after code review)

- Windows 11 x64; Python 3.13.15, PySide6 6.11.2 and PyInstaller 6.22.2 from the lockfile.
- Single windowed EXE: 52,143,345 bytes (49.7 MiB), with embedded icon/version/notices.
  SHA-256: `0518466e3c5d2076dd06d97f96bc6d06496a09ce23576ed9f3bd43247a375801`.
  This is an uncommitted local candidate; its manifest correctly records dirty source.
- Full test suite: 3,154 passed, 3 platform/render skips; overall coverage 97.05%.
  Required services passed the 90% combined statement/branch gate.
- Final audit/checkpoint and cleanup changes were covered by additional focused tests.
  See the [review report](windows-executable-review.md) for findings and exact scope.
- Ruff lint/format and mypy native, Windows and macOS targets passed.
- Both Windows folder and single-file builds passed their package checkers. The single
  EXE was copied alone to a spaces/Unicode path and launched from an unrelated directory.
  Embedded resource/private-file audits, 100%/200% offscreen rendering, native Windows
  rendering, cross-process settings/writing persistence, graceful/forced lock recovery,
  old-schema upgrade with retained backup, unknown-schema refusal and normal-exit
  extraction cleanup passed using synthetic disposable profiles.
- Lock scenarios exercise the shared production lock factory, with runtime open/close.
  They do not prove the entire normal desktop shutdown/restore flow or owner profile
  resolution after relocation; those remain native human acceptance checks.
- UI gallery checks passed at 100%/200% (41 passed, 1 render skip each). Dark and light
  galleries were inspected against the reference; packaging introduced no layout change.
- Both workflow files passed actionlint 1.7.12. Review corrected release checkout depth
  so tag ancestry verification receives full history. Remote CI, macOS packaging and
  draft-upload/rerun/download behavior remain unexecuted in this implementation session.

Scenario wall times include extraction, initialization and test work, and were measured
alongside other checks; they are not cold-start measurements. Normal-security startup
latency, peak temporary disk use, clean-machine and live-account acceptance remain open.
Broader redistribution still requires confirming all dependency license requirements.

## Outcome and scope

Deliver a Windows x64 `MailBrief.exe` that the owner can download, put on the Desktop,
and double-click. It opens the current desktop UI without a console, Python, uv,
the source checkout, an installer, or a companion dependency folder. It runs as a
standard user. Windows 11 x64 is the first acceptance target; broader Windows support
requires checking the locked native dependencies and testing the intended OS version.

The executable contains the application and dependencies. Saved work remains in the
existing Windows user profile. Copying the executable to another computer transfers
the application only; use backup/restore and configure credentials separately for data.

First-use setup remains explicit: select the owner's Google Desktop OAuth client file,
configure Groq, connect Gmail and approve AI use. Packaging does not supply those items.
The OAuth client file remains at its selected stable path; the current settings store
saves its path rather than importing the file. Moving that file requires selecting it again.

This work adds Windows single-file packaging and its delivery checks. Keep the Windows
folder build for diagnosis and the existing macOS `.app` workflow. Installers, automatic
updates, background services, ARM64 builds, Gmail writes and new email features are later
work. Packaging acceptance does not close the M4-M9 live product checklists.

## Existing implementation to reuse

| File on current main | Existing behavior | Planned change |
| --- | --- | --- |
| `scripts/build_desktop.py` | PyInstaller folder build; explicit migrations, QtSvg, fonts/icons, tzdata and dynamic imports | Add a Windows single-file option and separate output/work/spec locations |
| `scripts/desktop_entry.py` | Calls the desktop entrypoint | Reuse; change only if an identified frozen startup defect requires it |
| `src/mailbrief/paths.py` | Qt identity `MailBrief` / `MailBrief`; `AppLocalDataLocation` | Preserve identity, location and database filename |
| `src/mailbrief/storage/migrate.py` | Bundled revisions via `_MEIPASS`; pre-upgrade SQLite backup | Verify in a single-file process; preserve upgrade protections |
| `src/mailbrief/ui/theme/assets.py` | Fonts and SVGs beside the module | Verify both assets in the extracted bundle |
| `src/mailbrief/app.py`, `infra/data_lock.py` | Profile lock; orderly shutdown; restore while lock held | Exercise frozen processes, including renamed executables |
| `scripts/check_package.py` | Directory audit; two offline smoke processes at 100%/200% | Add explicit executable selection, embedded-content audit and copy-only checks |
| `src/mailbrief/ui/smoke.py` | Synthetic storage, settings, fonts/icons, rendering, backup/restore/export/cleanup | Reuse; add only scenarios required to close frozen-process gaps |
| `tests/unit/test_release_checks.py` | Rejects private files in folder distributions | Add failure tests for embedded forbidden files and missing required resources |
| `.github/workflows/ci.yml` | Windows/macOS quality, builds, smoke checks and seven-day artifacts | Add single-file Windows verification and limit temporary artifact retention |

## Build design

1. Keep PyInstaller and Python 3.13 from the locked environment. Do not replace the
   packaging tool or upgrade dependencies just to enable this mode.
2. Add `--onefile` to `build_desktop.py`; reject it on macOS with a clear build-time
   message. Keep the current default folder build during rollout. Windows CI explicitly
   builds both modes; macOS follows its current path.
3. Use these deterministic output locations, refining the earlier informal path proposal:

   | Mode | Executable | Work/spec directories |
   | --- | --- | --- |
   | Existing Windows folder | `out/dist/MailBrief/MailBrief.exe` | Keep existing locations |
   | Windows single file | `out/dist/onefile/MailBrief.exe` | `out/build/onefile`, `out/spec/onefile` |
   | Diagnostic single file | `out/dist/onefile-console/MailBrief.exe` | Separate console work/spec subdirectories |
   | macOS | `out/dist/MailBrief.app` | Existing workflow |

4. In single-file mode use `--onefile --windowed`, retain `--noupx`, and keep the explicit
   resource/import inclusion list on current main. Add a checked-in application `.ico`
   derived from existing project artwork and Windows file-version metadata. Preserve the
   default standard-user execution level; do not request administrator privileges.
5. Read the version from project metadata and validate it against `mailbrief.__version__`.
   Fail a release build if they disagree. Generate the Windows metadata file under `out/`
   with the numeric file version, product version, product name and executable name.
   Initially accept stable `major.minor.patch` releases only: `v0.2.0` maps to file version
   `0.2.0.0`. Validate each numeric component fits the Windows version field. Release asset
   names and tags use the same validated version; prereleases need a defined mapping later.
6. Validate every generated output target stays under resolved `out/`, including the new
   executable and mode directories. A redirected path must fail before PyInstaller's
   replacement step. Publish nothing after a failed build; stale artifacts cannot pass
   as a successful build. Stage release files only from the completed build's manifest.
7. Keep asset inclusion explicit. Include migration source files, the Qt native platform
   plugin and SVG support, the bundled fonts/icons and their notices, timezone/TLS data,
   SQLite/SQLAlchemy dynamic imports, and the Windows vault backend. Inventory embedded
   help and third-party notices. Do not recursively collect the checkout or owner's files.

Proposed developer commands after implementation:

```powershell
uv sync --locked --all-groups
uv run python scripts/build_desktop.py --onefile
uv run python scripts/check_package.py --executable out/dist/onefile/MailBrief.exe --layout onefile
```

These options are proposed interfaces and do not exist yet. Paths supplied to package
checks are explicitly validated; choose the default platform package only when no path
is given. `--console` remains a developer-only troubleshooting option.

## Runtime files and persistence

| Item | Location / rule |
| --- | --- |
| Application | The downloaded EXE, wherever the owner places it |
| Bundled libraries and static resources | PyInstaller's per-launch temporary extraction directory |
| SQLite database, desktop settings, rotating logs and profile lock | Existing `AppPaths.from_qt()` user-data directory |
| Gmail tokens and Groq key | Existing Windows Credential Manager entries |
| Google OAuth client JSON | Stable owner-selected path; never a build input |
| Exports and backups | Destinations explicitly chosen by the owner |

PyInstaller one-file mode extracts bundled dependencies before Python starts. Normal
exit removes its extraction folder; a killed process can leave it behind. Startup is
slower than a folder build. Use the default per-user temporary location and measure the
cost; do not introduce a shared fixed extraction directory or a custom extraction cache.
[PyInstaller execution model](https://pyinstaller.org/en/stable/operating-mode.html#how-the-one-file-program-works)

Resolve static resources from bundled module paths or `_MEIPASS`, as appropriate for
the current code. Never resolve persistent state from `_MEIPASS`, the EXE directory or
the working directory. Module `__file__` paths support bundled resources in both modes.
[Runtime paths](https://pyinstaller.org/en/stable/runtime-information.html)

Check behavior when the EXE is moved, renamed, launched through a shortcut, or copied
to a Desktop with spaces/non-ASCII characters. The profile identity must remain stable.
Two differently named copies must still exclude each other through the same profile lock.
Retain the current asynchronous save/shutdown/restore ordering through process exit.

Deleting the EXE removes the application file, not saved work or credentials. Document
the existing data controls for those. Stale extraction cleanup must never delete other
applications' temporary directories; automatic cleanup is outside this initial change.

## Package audit and automated validation

### Inspect what is inside the executable

The current directory scan cannot establish what a one-file EXE embeds. Add a build-side
archive inventory using the locked PyInstaller archive reader/viewer, without executing
archive contents. Inspect nested Python archives where relevant and record member names,
sizes and required resource presence in a build manifest. This tool runs in the build
environment; it is not an extra runtime dependency.

Reject private/development artifacts using existing rules plus SQLite sidecars and
known credential-cache filenames. Require migrations, native Qt plugin, theme resources,
timezone/TLS resources and notices. Also audit the small staged release directory.
Names alone cannot detect a secret disguised as an ordinary asset: use explicit build
inputs and a clean CI checkout, and never provide owner data or vault access to builds.
Do not claim that an inventory scan proves the absence of all secrets.

Add meaningful negative tests: an embedded forbidden member fails; a missing native
plugin or migration fails; a malformed executable inventory fails closed. Do not add
tests that simply compare the exact PyInstaller command string.

### Run only a copied executable

Extend `check_package.py` to copy just the EXE into a fresh directory outside the checkout,
with no `_internal` sibling. Separate the executable folder, working directory and
disposable data directory. Include spaces and Unicode in a test path. Remove inherited
`MAILBRIEF_*`, `PYTHONPATH`, `PYTHONHOME` and conflicting Qt configuration; select Qt
platform/scaling intentionally. CI runtime checks must never read the owner's profile.

Run the existing synthetic smoke at 100% and 200%. Require a newly written, valid report
and successful process exit each time. Reuse tests of preferences, cached metadata,
writing versions, backup verification/restore, export and cleanup. Require fonts/icons
explicitly: cosmetic fallback must not hide missing build inputs.

Add these frozen-process scenarios using the explicit smoke directory only:

- Process A creates known synthetic writing/settings; process B checks them before
  modifying anything. Existing smoke seeds some values before checking, so it is not
  sufficient proof of cross-process persistence by itself.
- A normal-lifecycle test harness exercises the shared profile-lock and shutdown path;
  a second copied/renamed EXE is refused while the first holds the same test profile.
  Test recovery of a dead-process lock separately. Never use a normal owner profile.
- Upgrade a synthetic older schema with the new EXE; verify owner records and retained
  pre-upgrade backup. A newer/unknown schema is refused without modification.
- Run one smoke pass with native `QT_QPA_PLATFORM=windows` on the Windows runner. A
  file-existence check for `qwindows.dll` and offscreen screenshots do not prove it loads.
- Use a unique extraction-temp root for checks, separate from the test profile. Observe
  cleanup after normal exit; after a forced stop, retained extraction files are allowable
  but the owner-data fixture must remain intact. Terminate only that test process tree.

Bound every subprocess check. Start with a 180-second limit for one-file smoke, report
elapsed times, and adjust using measured runs. On timeout, stop both bootloader and child
processes and retain safe failure evidence. Never dump private exception text or credentials.

If frozen checks require UI-module edits, follow current main's `.claude/skills/mailbrief-ui/`
instructions and its gallery checks; do not redesign the UI as part of packaging.

## Human acceptance on a clean Windows profile

Use a separate OS user or machine with no development environment. Copying an EXE to a
new folder alone does not create a clean profile. Keep default security settings enabled.
Current CI excludes its workspace/temp directories from Defender scanning; its startup
timings therefore cannot establish ordinary Desktop performance. Test the downloaded
candidate on the acceptance machine without those exclusions. Record security blocks
separately from application defects, and do not advise disabling protections.

| Check | Required result |
| --- | --- |
| Copy only EXE to Desktop; double-click | Current UI opens without console, Python or companion files; no UAC prompt |
| Launch moved/renamed EXE and via shortcut | Same saved profile; all assets, fonts and help available |
| First-use settings, Gmail connect, restart | Owner-selected OAuth file and vault credentials work; setup persists |
| Daily brief and drafting | Existing review/consent flow and source links behave correctly |
| Save edits, close, reopen from another folder | Briefs, actions, drafts, versions and preferences are intact |
| Start two copies, including renamed copies | One active writer; understandable already-running notice |
| Go offline after syncing | Saved views and writing available; retry works when back online |
| Data recovery and cleanup | Restore/export works; retained original can be verified; writing survives cache cleanup |
| 100%, 125%, 200% scaling; keyboard and file pickers | Controls usable; native dialogs and icon work |
| Downloaded EXE with Windows security checks | Record warnings, publisher status and any block; do not disable protections |
| Insufficient/unwritable temporary storage | Launch failure is understandable where possible; no profile corruption |
| Replace closed EXE with newer build | Saved work persists; required migrations retain a backup |

Record OS build, architecture, commit/version, EXE SHA-256 and results. Measure five
ordinary launches on the owner's machine and record median/worst time to visible window
and ready UI, plus compressed EXE size and peak extraction disk use. Initial usability
targets: median visible-window time at most 10 seconds and worst at most 20 seconds on
the acceptance machine. These are proposed targets, not measured claims. If missed,
investigate included dependencies and antivirus overhead before declaring readiness.

Failures before Python/Qt starts cannot reliably reach `desktop.log` or the application's
error dialogs. Document that distinction and use an isolated console build to diagnose
missing libraries/extraction failures. Logs after startup retain current redaction rules.

## Updating and recovery

Initial updates are manual: download the matching architecture, verify its checksum,
make an application-data backup, close MailBrief completely, then replace the Desktop EXE.
Keep the previous EXE until the new version is verified. If Windows says the file is in
use, finish shutdown before replacement; do not overwrite a running image.

A previous EXE is not sufficient rollback after a database upgrade. Older code may reject
the new schema. Recovery uses a compatible executable and the pre-upgrade/M9 backup,
following documented restore steps; preserve the upgraded database and any new writing
before restoring. Test this with disposable records. Never silently downgrade the schema.

## CI and release delivery

Implement delivery in two parts so local packaging can be validated before release automation.

1. Extend existing PR/main CI on standard runners. Retain all current Windows/macOS
   checks. Windows builds/checks both folder and one-file modes; macOS remains an app
   bundle. Keep stale-run cancellation and locked dependency caching. Upload the Windows
   single-file artifact with commit identity and short retention (three days initially).
   Do not upload large folder/debug builds on every success unless needed for diagnosis.
2. Add a tag-triggered release workflow for validated version tags, e.g. `v0.2.0`.
   Checkout the tag's exact commit. Verify tag/project/runtime versions match and the
   commit belongs to `main`, using sufficient fetched history for the ancestry check.
   Require the Windows build interpreter and artifact to be x64; record Python, locked
   PyInstaller/Qt versions, runner OS, architecture and lockfile hash in the manifest.
   Reuse the quality/build/check logic for that commit rather
   than publishing whichever previous artifact is newest. No PR artifacts are release inputs.
3. Build/audit/check with read-only repository permissions. Use a separate publishing
   job only after all required jobs succeed. Pass exact artifacts and verify their hashes
   against the build manifest. Only that job receives `contents: write` with the workflow's
   `GITHUB_TOKEN`; no personal access token, Gmail key or Groq key is required.
   [Workflow token permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
4. Stage `MailBrief-v<version>-windows-x64.exe`, `SHA256SUMS.txt`, a nonsecret build manifest,
   release notes/setup-update instructions and necessary notices. Include the macOS asset
   if that release is advertised as cross-platform. Hash the final downloadable bytes;
   if signing is added later, sign and check before hashing.
5. Create a draft GitHub Release for the existing tag using GitHub CLI and verified assets.
   Concurrency is per tag. A rerun may repair only the matching draft after verifying its
   tag/commit; refuse to replace a published release or silently clobber different assets.
   Complete native acceptance before manually publishing. This is a deliberate initial
   release policy, not a requirement for the local build.
   [GitHub release CLI](https://cli.github.com/manual/gh_release_create)

Standard hosted runners are free for public repositories. GitHub Free currently includes
500 MB artifact storage and 10 GB cache per repository; private repos have a 2,000-minute
allowance. Use short-lived CI artifacts, monitor actual usage, and configure the owner's
Actions budget to stop paid overages. Account billing settings are separate from workflow
YAML. Code-signing identity/certificates are separate from a free build pipeline.
[Current GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

The initial personal-use build may be unsigned; release notes must say so. Do not claim
SmartScreen warnings disappear. For broader distribution, evaluate signing and verify
the bundled Python/Qt/PySide6/font/icon license and redistribution requirements, including
any obligations beyond displaying notices. Keep necessary notices available from the
single-file app or bundled help as well as release assets. Inventory these requirements
during the first build step, so any packaging constraint is known before release work.

## Work sequence and checkpoints

One packaging PR can contain steps 1-4 as separate commits. Release automation is a
follow-up PR after those package checks pass; neither requires changing the email schema.

| Step | Deliverable | Exit checkpoint |
| --- | --- | --- |
| 1. Baseline and build option | Current-main branch; isolated one-file build; version/icon inputs | A real windowed EXE builds; existing folder/macOS scripts still work |
| 2. Embedded audit and copy-only checks | Inventory/manifest and checker extensions | Forbidden/missing members fail; copied-only EXE passes resource/storage checks |
| 3. Frozen lifecycle checks | Cross-process persistence, locks, native Qt, upgrades and timeout handling | Synthetic acceptance matrix passes without owner data or external services |
| 4. CI and owner instructions | Windows CI artifact; documented setup/move/update/recovery | CI passes; clean-profile Desktop acceptance is recorded |
| 5. Release workflow | Version-gated draft release with final hashes and assets | A dry run fails safely on version mismatch; a real candidate draft matches tested bytes |
| 6. First release | Owner-reviewed candidate and recorded limitations | Native acceptance passes; publish only the verified candidate |

Update `docs/m5-desktop.md`, `docs/m5-acceptance.md`, README and the agent guide with actual
paths and commands when implemented. Keep changes to `pyproject.toml`/`uv.lock` limited to
intentional version/dependency edits, updated together when needed. No code tests are
required for this planning document; implementation runs the repository's full quality
commands, both mypy platform targets, package checks and relevant native UI checks.

## Completion criteria

- The owner launches only the downloaded EXE from the Desktop under a standard account.
- It needs no source checkout, Python environment or dependency directory.
- Moving, renaming and replacing it preserves the existing user profile and credentials.
- Embedded-resource audit, copied-file smoke, frozen persistence/lock/upgrade checks and
  native Windows acceptance pass; existing macOS packaging remains functional.
- CI provides a tested file; release delivery identifies version/commit and exact hash.
- Setup, startup tradeoffs, updates and data recovery are documented truthfully.
- Product live acceptance remains tracked separately; no unexecuted check is marked passed.

## Plan review

Reviewed on 2026-10-11 against the current-main build/check scripts, entrypoint, path
resolver, migration code, theme loader, smoke implementation, profile lock, release tests
and CI workflow. This is a design review by the same author, not an independent code
review or a claim that a frozen executable has been tested.

Verdict: ready for the build prototype and packaging implementation. No known unresolved
design blocker remains for a personal-use prototype. The implementation and release
gates below are still mandatory; an EXE must demonstrate them before delivery is accepted.

| Finding | Priority | Resolution incorporated into this plan |
| --- | --- | --- |
| The local branch lacks current theme resources and recent fixes | High | Implement from current main; preserve the existing local edit |
| Scanning the distribution folder does not inspect embedded contents | High | Archive inventory, explicit required resources and negative audit tests |
| Current smoke writes settings before checking them | High | A separate verification process checks existing values before any writes |
| Smoke uses explicit DB paths and bypasses the normal desktop profile-lock entrypoint | High | Add frozen shared-lifecycle lock/shutdown checks with disposable data only |
| Replacing an EXE does not reverse a schema upgrade | High | Explicit compatible-code plus backup recovery, preserving newly written data |
| A release could accidentally select a stale/different build | High | Build exact tag commit, gate all jobs, manifest/hash transfer, no published-asset overwrite |
| Executable movement could change profile or resource resolution | High | Preserve Qt identity; test moved/renamed copies, unrelated working directory and duplicate-instance exclusion |
| Offscreen checks do not establish native plugin loading or Desktop usability | Medium | Native Windows smoke plus clean OS-profile acceptance |
| CI Defender exclusions make startup results unrepresentative | Medium | Measure downloaded candidate with normal security settings |
| One-file startup can fail before application logging is available | Medium | Bound process trees; document bootloader failure limits and isolated console diagnosis |
| Versions and architecture could disagree with release naming | Medium | Stable-version mapping, metadata consistency, x64 verification and build identity |
| Notices alone may not satisfy all bundled dependency distribution requirements | Medium | Early inventory; verify requirements before broader distribution |

Remaining evidence, rather than unresolved design choices:

- Actual EXE size, startup time, temporary disk use and normal-exit extraction cleanup.
- Native Qt rendering and frozen profile-lock behavior, including renamed copies and dead processes.
- Clean-machine OS/dependency compatibility and downloaded-file security behavior.
- Real-account setup, persistence and real-record recovery on the acceptance profile.
- Draft-release upload/rerun behavior and a downloaded asset matching the tested SHA-256.

If the prototype exposes a failing assumption, revise this document before broadening
scope. A slower cold start may justify dependency trimming or a later splash screen;
it does not justify removing persistence, audit or recovery checks. A security-policy
block may require trusted signing/distribution, and is not guaranteed to be fixable by
changing the bundler. Keep the existing folder build available while resolving either.
