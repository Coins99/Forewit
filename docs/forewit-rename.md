# Forewit rename: compatibility and verification

Forewit is the public name of the existing MailBrief project. The rename changes
presentation and adds commands; it does not create a new application profile.

## Naming and dependency audit

| Surface | Change or intentional retention | Verification |
|---|---|---|
| Desktop UI and accessible names | Forewit, including the uppercase summary heading | Synthetic light/dark, narrow/desktop renders at 1x/2x; UI tests |
| Startup, busy, settings, consent, recovery and provider messages | Forewit; configuration variable names remain literal | Existing unit/integration/error-path tests |
| Browser OAuth callback | Return to Forewit on success or cancellation | Local loopback OAuth tests |
| Canonical commands | `forewit`, `forewit-gmail-diagnostic`, `forewit-ms-diagnostic` | Installed help, invalid arguments and synthetic preferences outside the checkout |
| Legacy commands | All three `mailbrief*` commands still call the same implementations | Same installed-command checks; equivalent nested help/preferences output |
| Module commands | `python -m mailbrief` and backup module retained | Subprocess help from a temporary working directory |
| Qt display identity | Forewit | Repeated application creation and display-name assertions |
| Qt persistence identity | Organization/application both remain `MailBrief` | Before/after normal Qt data-location comparison |
| Python distribution, imports and internal classes | `mailbrief` / `MailBriefApplication` retained | Locked installation, type checks and full suite |
| Data and locks | Original SQLite/settings/appearance/token-cache paths and `desktop.lock` | Existing persistence, recovery and competing-process scenarios |
| Credentials | `MailBrief.Gmail`, `MailBrief.GoogleCalendar`, `MailBrief.Groq` and entry keys retained | Unchanged credential modules and mocked-vault tests |
| Configuration and build secret | `MAILBRIEF_*` retained, including `MAILBRIEF_GOOGLE_OAUTH_JSON` | Unchanged precedence/configuration and OAuth-build tests |
| Backup/export formats | Existing versions, archive members, `mailbrief-writing` and `mailbrief-diagnostics` retained | Backup/recovery/export tests |
| AI system prompts and cache versions | Unchanged | AST comparison against the pre-rename commit |
| Draft export fallback | `forewit-draft`, same sanitization and extensions | Draft filename tests |
| Windows application | `Forewit.exe`, Forewit product metadata | Build configuration tests, native build and package checks |
| macOS application | `Forewit.app`, Forewit bundle display/name | Build configuration tests; native macOS CI |
| macOS bundle identifier | `com.mailbrief.desktop` retained | Build configuration test and bundle inspection in native CI |
| Internal resources | `mailbrief/` paths and `MailBrief.ico` retained; same icon artwork | Embedded resource audit and startup smoke checks |
| Notices | Forewit application heading; original licenses/copyright | Embedded notices and notice-export checks |
| Release identity | Forewit names throughout staging, manifests, checksums and workflows | Synthetic release verification and negative tampering tests |
| Current docs/screenshots | Forewit prose and current README/Studio renders | Link audit, text inventory and visual review |
| Historical sources | ADRs, archive, original MVP plans and old releases retain MailBrief | Intentional historical references; no history rewriting |
| Shared launcher | Forewit prose; `Launch-MailBrief.ps1`, checkout names and Python invocation retained | Explicit-checkout preview and source/package startup checks |

The before/after occurrence inventories and detailed local logs are generated under
`scratch/` and are not distributed. The initial inventory contains 3,017 tracked-file
occurrences, including imports, historical records and compatibility identifiers.
Remaining runtime strings were inspected separately from comments/docstrings.
They are restricted to the identities, paths, formats and module commands above,
internal worker/temp/log names, and the dormant Microsoft request user agent.
AI instruction strings also intentionally retain their original wording.

## Local verification

- Locked dependencies are unchanged. Both platform type checks pass.
- Forewit display identity preserves the original normal Qt data path.
- All six installed commands pass help/invalid-argument checks outside the checkout.
- Windows/macOS build configuration tests cover directory, single-file and console layouts.
- Synthetic screenshots were inspected in both modes and scales; current README and
  Studio images were regenerated from the actual widgets.
- Full local Windows suite: 4,272 passed, four platform/environment skips, and
  three Git-ownership infrastructure errors (the elevated user differs from the
  sandbox worktree owner). All three pass with a command-scoped `safe.directory`
  setting, together with the other focused checks (53 passed). No global Git
  configuration was changed. CI provides the clean-owner full-suite check.
- Final formatting, lint and both platform type checks pass (417 source files).
- Documentation audit found no newly broken local links or heading anchors.

## External rollout and outstanding acceptance

The existing repository is renamed in place after the implementation PR passes and
merges. No release tag or public release is created as part of this change.
Repository metadata inspection found no Pages site, webhooks or custom social image.

Google OAuth branding must be updated in the existing Google Auth Platform project.
Preserve its client, scopes, audience, publishing status and grants. Verify whether
the saved name is live or only draft branding; do not create a replacement OAuth client.

Local browser and Windows inspection runtimes currently fail to start with
`The system cannot find the path specified (os error 3)`. Therefore Google branding
and visible-window/personal-account acceptance are pending; automated and synthetic
results do not claim those live checks. Do not force sign-out or reset saved data.
