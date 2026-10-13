# Google sign-in and the full Inbox

Configured desktop builds provide **Sign in with Google** without asking the user
to download an OAuth file. The app opens the system browser, uses the existing
PKCE and loopback flow, and stores each user's refresh credential only in the OS
vault. Gmail remains read-only; optional Google Calendar access uses the same sign-in.

After sign-in, and on restoring a session at launch, MailBrief syncs the current
Inbox's metadata across all received dates. The **Inbox** page shows it newest first,
100 messages per page, with the sender, subject, received time and Gmail's short
preview. **Refresh Inbox**, today's **Sync and review**, and scheduled refreshes
check the full Inbox again. No email body is downloaded or sent to AI by this sync.
Daily briefs still cover one local day and retain their existing consent rules.

Select an email and choose **Should have been in my brief** when the review should have
suggested it: MailBrief then ranks that sender's mail higher, and the page counts the emails
you wanted in the last 14 days that the review didn't suggest. The mark stays on this
computer; see [Ranking](ranking.md#should-have-been-in-my-brief).

Only a complete scan reconciles cached Inbox membership. Failed, partial or cancelled
scans retain unobserved cached entries and show an incomplete-sync message. The existing
active-work and rate-limit budgets apply. Partial scans retain an account-scoped pagination
checkpoint and observed message IDs in SQLite, so the next refresh continues across restarts.
Only the final, error-free scan reconciles membership; invalid cursors restart safely on the
next refresh. Disconnect and backup restore discard these transient checkpoints.
Migration 0017 follows the unchanged project migration 0016 (PR #53 must merge first).
Gmail changes during enumeration may require another refresh. This is a snapshot,
not push delivery, and periodic refresh runs only while the app is open according
to the owner's refresh preference. Disconnect clears the live Inbox view; saved local
records remain available through Saved mail.

## App registration and packaging

The developer must register a **Desktop app** OAuth client, enable Gmail API (and
Calendar API for optional Calendar access), configure the consent screen, and keep
the client identity stable across builds. Personal testing uses designated test users.
Public distribution requires Google's applicable OAuth verification for Gmail access.
Bundling the client does not bypass Google's testing, token-lifetime or consent rules.
Native app registration credentials are distributed in the executable; they must not
be treated as a confidential server credential. Never bundle user tokens or API keys.

For a local configured build:

```powershell
uv run python scripts/build_desktop.py --onefile --google-oauth-client C:\private\desktop-client.json
```

The build validates the Desktop client and stages **only** its client ID and client
secret into `out/resources/google-oauth.json`, bundled as
`mailbrief/resources/google-oauth.json`. The downloaded file is never copied wholesale
or committed. The runtime resolves this resource relative to its module, so it works
after copying the executable and from PyInstaller's temporary extraction directory.

For CI, configure the repository Actions secret **MAILBRIEF_GOOGLE_OAUTH_JSON** with
the registered Desktop client JSON. Build steps pass it only to the build process via
**MAILBRIEF_BUILD_GOOGLE_OAUTH_JSON**. An unset secret produces a development build
that still supports the optional custom OAuth file. A malformed configured client
fails the build with static guidance rather than silently making a broken sign-in build.

Settings retains **Custom Google client (optional)** for development and existing
installations. An explicitly selected custom file takes precedence; clear that field
and Save settings to use the bundled client. Changing client IDs requires disconnecting
and signing in again. Credentials from one client must never be reused for another.

## Validation

Tests cover bundled configuration and explicit overrides, all-date Inbox pagination,
archival reconciliation only after a complete scan, cancellation and malformed metadata,
runtime account isolation, sign-in-triggered sync, and plain-text rendering. Live Google
sign-in and public distribution acceptance still require the configured Cloud project
and the owner's browser interaction.
