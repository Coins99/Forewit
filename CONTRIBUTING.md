# Contributing to Forewit

Bug reports, documentation improvements and focused pull requests are welcome.
For a bug, include your operating system, how you launched Forewit, steps to
reproduce, and expected versus actual behavior. Use synthetic examples and
redacted screenshots; keep credentials and private mail out of issues and commits.

## Run from source

You need Git, Python **3.13**, and
[uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.7 or newer,
below 0.13**. Windows and macOS are supported; the macOS package targets macOS
15 or later. Keep the checkout and its `.venv` outside folders synced by
iCloud Drive, OneDrive or Dropbox, which can interfere with Python and Qt files.

Run these commands in PowerShell on Windows or Terminal on macOS:

```sh
git clone https://github.com/Coins99/Forewit.git
cd Forewit
uv python install 3.13
uv sync --locked --all-groups
uv run forewit
```

## Build a desktop package

Build on the target operating system after installing the dependencies above:

```sh
uv run python scripts/build_desktop.py
uv run python scripts/check_package.py
```

For a single Windows EXE, build with `--onefile`; the result is
`out/dist/onefile/Forewit.exe`. See
[Windows executable instructions](docs/windows-executable.md) for verification
and updates, or [desktop packaging](docs/m5-desktop.md#build-and-check-packages)
for the Windows folder package and macOS app. Packages are development
candidates; release signing and macOS notarization are not configured.

## Submit a change

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

## Find your way around

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
