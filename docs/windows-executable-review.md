# Windows executable implementation review

Reviewed 2026-10-11. Scope: the uncommitted single-executable implementation against
`main` at `e122fa8`, including build/check scripts, archive provenance, resource generation,
release staging/workflows, application entrypoint, shared locks and frozen smoke scenarios.
This is an author review with regression checks, not an independent review of every
Forewit feature or evidence of owner live acceptance.

## Conclusion

The Windows one-file approach fits this application. Keep PyInstaller, the existing Qt
profile identity and manual replacement updates. A new launcher, installer, updater or
service would add complexity without helping the requested Desktop executable.
The initial implementation needed release-integrity fixes before it could be trusted.
Those fixes and focused reductions are implemented below.

## Findings and changes

| Priority | Finding and failure case | Resolution |
| --- | --- | --- |
| High | The checker called `write_manifest()` after testing. An EXE from another checkout could receive the current commit/version and appear to be a new build. | Only the builder writes provenance. Checking and staging validate the original manifest, commit, version, lockfile and EXE hash. Regression tests reject stale identity and verify the manifest stays unchanged. |
| Medium | Staging copied notices from `out/resources`; another build could replace that file while the tested EXE still contained different notices. | Read notices directly from the frozen archive, without running the EXE. A regression test changes the external notice file and verifies the embedded copy wins. |
| Medium | The handwritten license-package list omitted runtime dependencies including cryptography, PyJWT and pydantic-settings. | Resolve installed runtime dependencies with platform markers and selected extras. Include the bootloader's notices, excluding its development dependency tree. This improves inventory; it does not certify broader redistribution compliance. |
| Medium | Default Qt collection pulled in the PDF reader and virtual keyboard, along with QtPdf, QtQuick and QtQml. | Retain the upstream QtGui hook and filter only the two unused Windows entry plugins before binary dependency analysis. Keep Windows/offscreen rendering, native styles and the graphics fallback libraries. |
| Medium | A writing-version check only required a nonempty version list, allowing a lost checkpoint's contents to go unnoticed. | Share one writing verifier that checks current text and the latest saved checkpoint's text/title. A corrupted-checkpoint test now fails. |
| Medium | Archive privacy checks missed `.env.production` and SQLite journal/WAL variants. | Expand the shared rules and add negative cases for those filenames. The audit remains a filename/resource check, not a disguised-secret scanner. |
| Medium | A failure before the smoke PID handshake could leave the bootloader child/error dialog after parent termination. | Add a Windows process-tree termination fallback for that path, with a bounded taskkill call. OS permission failures can still prevent forced cleanup; this is a test harness, not a production process supervisor. |
| Low | `--layout onefile` without `--executable` selected the folder build. | Select the matching layout's default path; final package checks use this shorter command. |
| Low | Staging duplicated build-manifest identity/hash validation. | Reuse one verifier rather than maintaining two policies. |

## Complexity retained deliberately

- Embedded inventory and checksum verification catch missing resources, stale builds and
  changed transfer assets; removing them would weaken delivery guarantees.
- Separate windowed/console outputs prevent diagnostic builds from replacing the Desktop
  candidate. The existing folder build remains useful for regression checks and diagnosis.
- The small ICO encoder preserves multiple icon sizes from existing artwork without a
  new application dependency. The new Qt hook contains only a narrow Windows filter.
- No application feature modules, migration history or dormant Microsoft functionality
  were removed to reduce the executable size.

## Evidence and limits

- Full suite: 3,154 passed, 3 skipped; 97.05% coverage. Required services pass the 90%
  combined statement/branch gate. Four later audit/checkpoint cases are included in the
  final focused run with the existing release gates and two additional process-cleanup
  failure cases: 54 passed.
- Ruff lint/format and mypy native/Windows/macOS targets passed. Both workflows passed
  actionlint. UI galleries passed at 100%/200% (41 passed, one render skip per scale).
  Dark/light renders remain readable; reference differences in the header, saved-mail/Data
  links and action content predate these changes. No production layout was changed.
- Rebuilt EXE: 52,143,345 bytes (49.7 MiB), down from 60,698,355 bytes: 14.1% smaller.
  QtPdf/Quick/Qml/VirtualKeyboard libraries are absent from the embedded inventory.
  SHA-256: `0518466e3c5d2076dd06d97f96bc6d06496a09ce23576ed9f3bd43247a375801`.
- Final Windows single-file and folder package checks passed after rebuilding. No remote
  CI, macOS build or GitHub draft publication was run
  during this review.

Remaining release gates are a clean Windows profile with normal security settings,
live-account setup/recovery, full desktop shutdown/restore behavior and actual GitHub
asset upload/download. A workflow rerun may rebuild different bytes: it intentionally
refuses to replace an existing draft with changed checksums. A successful repeat build
does not establish byte-for-byte reproducibility.
