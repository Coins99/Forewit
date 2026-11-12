"""Rules for the source itself, so a slip shows up in the tests rather than on screen. Each
failure lists the offending lines as "path:line: text"."""

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
SOURCE: Final = ROOT / "src" / "mailbrief"
UI: Final = SOURCE / "ui"
TOKENS: Final = UI / "theme" / "tokens.py"
LISTS: Final = UI / "lists.py"
DATE_PICKER: Final = UI / "date_picker.py"

# strftime directives that print words or formats of the system language: weekday and month
# names (%a %A %b %B), AM or PM (%p), and the system's date and date-time formats (%c %x).
# Qt sets the C library's locale from the system language, so they would follow it. "%%" is
# a literal percent sign, and a directive followed by a letter or digit is something else,
# such as "%B3" in a percent-encoded URL or "%abc%" in a LIKE pattern.
SYSTEM_LANGUAGE_DATE: Final = re.compile(r"(?<!%)%[aAbBpcx](?![0-9A-Za-z])")
# A colour written as six or eight hex digits after "#".
HEX_COLOUR: Final = re.compile(r"#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})(?![0-9A-Za-z])")
# Qt's own list widget made or subclassed: its clear() can crash on Cocoa (see ui/lists.py).
PLAIN_LIST: Final = re.compile(r"\bQListWidget\(|\(QListWidget\)")
# A date field's own drop-down calendar, which is Qt's unstyled one.
CALENDAR_POPUP: Final = re.compile(r"\.setCalendarPopup\(")


def offending_lines(files: Iterable[Path], pattern: re.Pattern[str]) -> list[str]:
    """Every line of ``files`` that ``pattern`` matches, as "path:line: text"."""
    found: list[str] = []
    for path in sorted(files):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if pattern.search(line):
                found.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()}")
    return found


def test_no_date_format_follows_the_system_language() -> None:
    """Weekday and month names come from the English tables in ``ui/deadline_text.py``."""
    assert offending_lines(SOURCE.rglob("*.py"), SYSTEM_LANGUAGE_DATE) == []


def test_ui_colours_come_only_from_the_tokens() -> None:
    """Every colour under ``ui`` is a design token from ``ui/theme/tokens.py``."""
    files = (path for path in UI.rglob("*.py") if path != TOKENS)
    assert offending_lines(files, HEX_COLOUR) == []


def test_ui_lists_clear_safely() -> None:
    """Every list widget under ``ui`` is a ``SafeList`` from ``ui/lists.py`` (an
    ``ActivatingList`` is one), whose ``clear()`` is ``clear_list()``."""
    files = (path for path in UI.rglob("*.py") if path != LISTS)
    assert offending_lines(files, PLAIN_LIST) == []


def test_date_fields_use_the_date_picker() -> None:
    """Every date field's drop-down calendar is a ``DatePicker``, through
    ``calendar_popup()`` in ``ui/date_picker.py``."""
    files = (path for path in UI.rglob("*.py") if path != DATE_PICKER)
    assert offending_lines(files, CALENDAR_POPUP) == []


def test_the_rules_find_what_they_are_for() -> None:
    """The patterns catch the slips they exist for, and pass what only looks like them."""
    for line in ('f"{day:%b} {day.day}"', 'when.strftime("%a %d %B")', '"%I:%M %p"', '"%x"'):
        assert SYSTEM_LANGUAGE_DATE.search(line), line
    for line in ('"%Y-%m-%d %H:%M"', '"100%%a"', '"%B3%A9"', "LIKE '%abc%'", '"%s" % name'):
        assert not SYSTEM_LANGUAGE_DATE.search(line), line
    for line in ('QColor("#1a1a19")', "color: #193567;", '"#FF193567"'):
        assert HEX_COLOUR.search(line), line
    for line in ("QListView#briefList", "# PR #38 and #2041", '"#1a1a1"', "#accent_fg"):
        assert not HEX_COLOUR.search(line), line
    for line in ("self.missed = QListWidget()", "QListWidget(parent)", "class Rows(QListWidget):"):
        assert PLAIN_LIST.search(line), line
    for line in ("QListWidgetItem(title)", "listing: QListWidget", "isinstance(x, QListWidget)"):
        assert not PLAIN_LIST.search(line), line
    assert CALENDAR_POPUP.search("self.day.setCalendarPopup(True)")
    assert not CALENDAR_POPUP.search("calendar_popup(self.day)")


# --- Privacy and boundary rules (QA slice B1) ---------------------------------------------

# A name that holds something private: a token, key, secret, password, an email's text, the
# owner's writing or search text, or a prompt that carries them.
SENSITIVE_NAME: Final = re.compile(
    r"(token|secret|password|passwd|api_?key|credential|body|snippet|query|prompt|"
    r"draft_?text|content|payload)",
    re.IGNORECASE,
)
LOG_METHODS: Final = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
# The only OAuth scopes the source may name: Gmail read-only and two read-only Calendar ones.
ALLOWED_SCOPES: Final = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    }
)
GOOGLE_SCOPE: Final = re.compile(r"https://www\.googleapis\.com/auth/[A-Za-z0-9_.\-/]+")
HTTP_WRITE: Final = re.compile(r"\.(?:post|put|patch|delete)\(")
GMAIL_WRITE_PATH: Final = re.compile(
    r"/(?:send|modify|trash|untrash|batchModify|batchDelete|insert|import)\b"
)
NAIVE_CLOCK: Final = re.compile(r"\bdatetime\.utcnow\(|\.utcfromtimestamp\(|\bdatetime\.now\(\s*\)")
# Network libraries belong to the adapters, never to the domain, storage or text helpers.
NETWORK_IMPORT: Final = re.compile(r"^\s*(?:from|import)\s+(?:httpx|requests|aiohttp)\b")
# The dependencies the project forbids (AGENTS.md "Dependencies").
FORBIDDEN_DEPENDENCIES: Final = (
    "google-api-python-client",
    "google-auth",
    "msgraph",
    "python-dotenv",
    "tenacity",
    "backoff",
    "retrying",
    "beautifulsoup4",
    "lxml",
    "html5lib",
    "llama-cpp-python",
    "ctransformers",
    "transformers",
)


def logging_calls_with_private_names(files: Iterable[Path]) -> list[str]:
    """Logging calls whose arguments (after the message) name something private."""
    found: list[str] = []
    for path in sorted(files):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"logger", "log", "logging", "_logger", "LOGGER"}
            ):
                continue
            arguments = [*node.args[1:], *(keyword.value for keyword in node.keywords)]
            names = {
                inner.id if isinstance(inner, ast.Name) else inner.attr
                for argument in arguments
                for inner in ast.walk(argument)
                if isinstance(inner, ast.Name | ast.Attribute)
            }
            if any(SENSITIVE_NAME.search(name) for name in names):
                shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                where = f"{shown.as_posix()}:{node.lineno}"
                found.append(f"{where}: logs {sorted(names)}")
    return found


def test_no_log_call_passes_a_token_key_body_query_or_prompt() -> None:
    """Tokens, keys, bodies, drafts and search text never reach a log line; a log line gets
    codes and counts (AGENTS.md invariants)."""
    assert logging_calls_with_private_names(SOURCE.rglob("*.py")) == []


def test_the_source_names_only_the_read_only_google_scopes() -> None:
    """Gmail is ``gmail.readonly``; Calendar adds two read-only scopes and nothing wider."""
    named: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            named += [
                f"{path.relative_to(ROOT).as_posix()}:{number}: {scope}"
                for scope in GOOGLE_SCOPE.findall(line)
                if scope not in ALLOWED_SCOPES
            ]
    assert named == []


def test_google_adapters_make_no_writing_request() -> None:
    """Gmail and Google Calendar adapters only read: no POST, PUT, PATCH or DELETE, and no
    send, modify or trash path. The token endpoint (``providers/google/tokens.py``) is the one
    POST, and it is outside these folders."""
    files = [
        *(SOURCE / "providers" / "gmail").glob("*.py"),
        *(SOURCE / "providers" / "gcal").glob("*.py"),
    ]
    assert offending_lines(files, HTTP_WRITE) == []
    assert offending_lines(files, GMAIL_WRITE_PATH) == []


def test_timestamps_are_never_naive() -> None:
    """No ``datetime.utcnow()``, ``utcfromtimestamp`` or zone-less ``datetime.now()``."""
    assert offending_lines(SOURCE.rglob("*.py"), NAIVE_CLOCK) == []


def test_only_adapters_and_local_setup_import_a_network_library() -> None:
    """Provider JSON stays inside its adapter: the domain, storage and text packages and the
    services (but local setup's download) never import an HTTP library."""
    allowed = {SOURCE / "services" / "local_setup.py"}
    files = [
        path
        for folder in ("domain", "storage", "text", "services", "ports")
        for path in (SOURCE / folder).rglob("*.py")
        if path not in allowed
    ]
    assert offending_lines(files, NETWORK_IMPORT) == []


def test_no_forbidden_dependency_is_declared() -> None:
    """``pyproject.toml`` declares none of the libraries AGENTS.md rules out."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    declared = [
        name for name in FORBIDDEN_DEPENDENCIES if re.search(rf'"{re.escape(name)}[<>=\[ ",]', text)
    ]
    assert declared == []


def test_every_url_open_is_behind_a_link_check() -> None:
    """A file under ``ui`` that opens a URL also checks it against Gmail's or Calendar's host."""
    unguarded = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(UI.rglob("*.py"))
        if "QDesktopServices.openUrl(" in path.read_text(encoding="utf-8")
        and not re.search(
            r"is_gmail_link|is_calendar_link|mail\.google\.com",
            path.read_text(encoding="utf-8"),
        )
    ]
    assert unguarded == []


def test_the_privacy_rules_find_what_they_are_for(tmp_path: Path) -> None:
    """The scans catch the slips they exist for, and pass what only looks like them."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import logging\nlogger = logging.getLogger('x')\n"
        "def f(body, count, api_key):\n    logger.info('got %s', body)\n"
        "    logger.info('n=%d', count)\n"
        "    logger.info('k', extra={'k': api_key})\n",
        encoding="utf-8",
    )
    found = logging_calls_with_private_names([bad])
    assert len(found) == 2
    assert HTTP_WRITE.search("await client.post(url)")
    assert not HTTP_WRITE.search("posted = 1")
    assert NAIVE_CLOCK.search("datetime.now()")
    assert not NAIVE_CLOCK.search("datetime.now(UTC)")
