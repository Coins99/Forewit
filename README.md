# MailBrief

MailBrief is a personal desktop app for Windows and macOS that turns important
Gmail messages into a short daily brief with summaries, suggested actions and
deadlines. Review the messages you want it to read, then choose whether to send
their prepared text to Groq for AI analysis.

![MailBrief showing a daily brief and the selected email](docs/ui/workspace-dark.png)

## What you can do

- Review ranked Inbox messages and open the originals in Gmail.
- Keep track of actions, plans, target dates and follow-up replies.
- Write local drafts and notes, with optional AI drafting from context you choose.
- Browse saved briefs and cached mail metadata offline.
- Set your time zone, sender exclusions and refresh preferences.
- Back up and restore local data, and export your writing.

Gmail is the active mailbox provider, with read-only access. MailBrief does not
send mail or change your mailbox; drafts stay local until you copy or export them.
Outlook support is retained in the codebase for later work.

**Status:** this is a personal-use project under development. Features are
implemented, but live-account and packaged-app acceptance checks remain open.
See [project status](docs/project-status.md) for the milestone record and
[platform acceptance](docs/m5-acceptance.md) for outstanding checks.

## Get started

### Windows: download, extract and run

1. Open [MailBrief's CI builds](https://github.com/Coins99/MailBrief/actions/workflows/ci.yml)
   and choose the latest successful run on **main**.
2. Under **Artifacts**, download **MailBrief-Windows-onefile-...**. GitHub
   requires you to sign in to download build artifacts.
3. Right-click the downloaded ZIP and choose **Extract All**.
4. Open the extracted folder and double-click **MailBrief.exe**. Allow a few
   seconds for the window to appear, then follow the Gmail and AI setup below.

The EXE includes Python and all application dependencies. No separate Python,
uv or Git installation is needed. GitHub's **Code > Download ZIP** contains the
source code; use the build artifact above to get the runnable app.

Windows build artifacts are kept for three days. These are development builds;
when a release is published, it will appear on the
[Releases page](https://github.com/Coins99/MailBrief/releases).
The current Windows package targets Windows 11 x64. See
[Windows executable instructions](docs/windows-executable.md) for updates and
saved-data details.

For macOS packages, see [desktop packaging](docs/m5-desktop.md#build-and-check-packages).
To run from source or build your own package, use the contributor instructions below.

### Connect Gmail and set up AI

1. Follow [Gmail setup](docs/gmail-setup.md) to enable the Gmail API and download
   a Google **Desktop app** OAuth client JSON file. Keep that file outside the
   repository in a stable location; MailBrief saves its path.
2. Open **Settings** in MailBrief, select the OAuth file and save it. Choose
   **Connect Gmail** and complete sign-in in your browser.
3. For AI summaries or drafting, follow [AI setup](docs/ai-analysis.md#setup),
   then save your Groq model and API key in **Settings**. Enable Groq Zero Data
   Retention before sending private mail.
4. Choose **Sync and review**. Select the messages MailBrief may read, then
   approve or decline sending their prepared text to Groq.

You can view saved briefs and cached metadata offline. New AI summaries and AI
drafting need a configured Groq key and your approval. Automatic analysis needs
separate explicit permission in the app.

## Your data

MailBrief stores cached metadata, saved briefs, actions, drafts and preferences
locally in SQLite. Gmail tokens and Groq keys use Windows Credential Manager or
macOS Keychain. Downloaded incoming email bodies are prepared in memory rather
than saved as a body cache.

AI processing happens on Groq's servers. You review the messages or drafting
context and approve sending it; local storage does not make AI processing local. See
[AI analysis and consent](docs/ai-analysis.md) and
[AI drafting](docs/m7-drafts.md) for the data sent in each workflow.
Use **Data** in the sidebar for backup, export and recovery; see
[data and recovery](docs/m9-durability.md).

## Contributing

Bug reports, documentation improvements and focused pull requests are welcome.
For a bug, include your operating system, how you launched MailBrief, steps to
reproduce, and expected versus actual behavior. Use synthetic examples and
redacted screenshots; keep credentials and private mail out of issues and commits.

### Run from source

You need Git, Python **3.13**, and
[uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.7 or newer,
below 0.13**. Windows and macOS are supported; the macOS package targets macOS
15 or later. Keep the checkout and its `.venv` outside folders synced by
iCloud Drive, OneDrive or Dropbox, which can interfere with Python and Qt files.

Run these commands in PowerShell on Windows or Terminal on macOS:

```sh
git clone https://github.com/Coins99/MailBrief.git
cd MailBrief
uv python install 3.13
uv sync --locked --all-groups
uv run mailbrief
```

### Build a desktop package

Build on the target operating system after installing the dependencies above:

```sh
uv run python scripts/build_desktop.py
uv run python scripts/check_package.py
```

For a single Windows EXE, build with `--onefile`; the result is
`out/dist/onefile/MailBrief.exe`. See
[Windows executable instructions](docs/windows-executable.md) for verification
and updates, or [desktop packaging](docs/m5-desktop.md#build-and-check-packages)
for the Windows folder package and macOS app. Packages are development
candidates; release signing and macOS notarization are not configured.

### Submit a change

For a change:

1. Fork the repository if you do not have write access, then create a branch
   from the latest `main`.
2. Keep the change focused. For larger features, discuss the scope in an issue
   first and check the [project scope](docs/mvp-scope.md).
3. For code changes, add or update relevant tests and run the checks below.
   Tests use fake providers and temporary databases, without real credentials,
   live mailboxes or paid AI.
4. Open a pull request against `main` describing the behavior changed and how
   you verified it. Use a conventional commit prefix such as `docs:` or `fix:`.

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run mypy --platform win32 src tests
uv run mypy --platform darwin src tests
uv run pytest
uv run python scripts/check_coverage.py
```

CI checks Windows and macOS, including native packages. Coverage targets are
80% overall and 90% for each service module. For documentation-only changes,
check links, commands and accuracy against the current code and docs.

### Find your way around

| Path | Purpose |
| --- | --- |
| `src/mailbrief/domain/` | Shared data models |
| `src/mailbrief/ports/` | Provider interfaces |
| `src/mailbrief/providers/` | Gmail, Groq and retained Microsoft adapters |
| `src/mailbrief/services/` | Sync, ranking, briefs, actions and drafting logic |
| `src/mailbrief/storage/` | SQLite persistence and repositories |
| `src/mailbrief/ui/` | PySide6 desktop interface |
| `src/mailbrief/diagnostics/` | Developer command-line tools |
| `tests/` | Tests mirroring the application structure |
| `migrations/` | Alembic database migrations |
| `scripts/` | Build, package and coverage checks |
| `docs/` | Setup guides, design decisions and validation records |

Start with the [file change map](docs/change-map.md) and
[architecture diagram](docs/architecture.puml) for implementation work.
[AGENTS.md](AGENTS.md) contains detailed repository guidance for coding agents.

## Further reading

- [Desktop usage and keyboard navigation](docs/m5-desktop.md)
- [Actions and plans](docs/m6-actions.md)
- [Drafts, notes and AI writing](docs/m7-drafts.md)
- [Preferences, brief history and daily operation](docs/m8-daily-operation.md)
- [Project status and milestone documentation](docs/project-status.md)
- [Scope](docs/mvp-scope.md) and [implementation plan](docs/email-implementation-plan.md)
- [Architecture decisions](docs/adr/)

## License

MailBrief is licensed under the [MIT License](LICENSE). Bundled fonts and icons
have their own notices included with desktop packages.
