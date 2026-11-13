"""Rules for the source itself, so a slip shows up in the tests rather than on screen. Each
failure lists the offending lines as "path:line: text"."""

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from mailbrief.providers.gmail.oauth import SIGN_IN_SCOPES
from mailbrief.storage import tables

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
    for line in (
        "from mailbrief.providers.groq.provider import _usage",
        "    import mailbrief.providers.gmail.mapper as mapper",
        "from mailbrief import config, providers",
        "from ..providers.groq import provider",
        "from .. import providers",
        'module = importlib.import_module("mailbrief.providers.groq")',
    ):
        assert ADAPTER_IMPORT.search(line), line
    for line in (
        "from mailbrief.ports.ai_provider import AIProvider",
        "from mailbrief.services.brief import provider_display_name",
        "from mailbrief.domain import messages",
    ):
        assert not ADAPTER_IMPORT.search(line), line


# --- Privacy and boundary rules (QA slice B1) ---------------------------------------------

# A name that holds something private: a token, key, secret, password, a request's headers,
# an email's text, the owner's writing, search text or sender rules, or a prompt.
SENSITIVE_NAME: Final = re.compile(
    r"(token|secret|password|passwd|api_?key|credential|authorization|headers|body|snippet|"
    r"query|prompt|draft_?text|content|payload|subject|instructions|notes|evidence|senders|"
    r"sender_rules|(?:^|_)text$)",
    re.IGNORECASE,
)
# A name that is a size, a limit or a flag, not the private thing itself.
COUNT_NAME: Final = re.compile(
    r"(?:^max_|^total_|_(?:count|tokens|chars|length|size|limit|truncated|type)$)", re.IGNORECASE
)
LOG_METHODS: Final = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
# The only OAuth scopes the sign-in may ask for: Gmail read-only and two read-only Calendar ones.
ALLOWED_SCOPES: Final = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    }
)
GOOGLE_SCOPE: Final = re.compile(
    r"https://www\.googleapis\.com/auth/[A-Za-z0-9_.\-/]*[A-Za-z0-9_/]"
)
# Gmail's full-access scope is the one Google scope that is not under googleapis.com/auth.
FULL_MAIL_SCOPE: Final = re.compile(r"""["']https://mail\.google\.com/["']""")
# A writing request, as a client method or as the verb handed to request() or stream().
HTTP_WRITE: Final = re.compile(
    r"""\.(?:post|put|patch|delete)\(|["'](?:POST|PUT|PATCH|DELETE)["']"""
)
GMAIL_WRITE_PATH: Final = re.compile(
    r"/(?:send|modify|trash|untrash|batchModify|batchDelete|insert|import)\b"
)
NAIVE_CLOCK: Final = re.compile(
    r"\bdatetime\.utcnow\(|\.utcfromtimestamp\(|\bdatetime\.now\(\s*(?:\)|$|tz=None)|"
    r"\bdatetime\.fromtimestamp\([^,()]*\)|\bdatetime\.today\(|\bdatetime\.strptime\(|"
    r"\bdt\.now\(\s*\)"
)
# Network libraries belong to the adapters, never to the domain, storage or text helpers.
NETWORK_IMPORT: Final = re.compile(
    r"^\s*(?:from|import)\s+(?:[\w.]+\s*,\s*)*"
    r"(?:httpx|requests|aiohttp|urllib\.request|http\.client|socket)\b"
)
# An import of a provider adapter, capturing which one ("gmail.mapper", "local.manifest").
ADAPTER_IMPORT: Final = re.compile(
    r"^\s*(?:from|import)\s+(?:mailbrief\.|\.+)providers\b\.?([\w.]*)"
    r"|^\s*from\s+(?:mailbrief|\.+)\s+import\s[^#\n]*\bproviders\b"
    r"|import_module\(\s*[\"']mailbrief\.providers\b",
    re.MULTILINE,
)
# What makes a link safe to open: Gmail's or Calendar's host, checked in the same function.
LINK_GUARD: Final = re.compile(
    r"is_gmail_link\(|is_calendar_link\(|gmail_source\(|gmail_link\(|"
    r"host == \"mail\.google\.com\""
)
# The dependencies the project forbids (AGENTS.md "Dependencies"), as name prefixes, so
# "msgraph" also refuses "msgraph-sdk" and "google-auth" refuses "google-auth-oauthlib".
FORBIDDEN_DEPENDENCIES: Final = (
    "google-api-python-client",
    "google-auth",
    "msgraph",
    "python-dotenv",
    "tenacity",
    "backoff",
    "retrying",
    "beautifulsoup4",
    "bs4",
    "lxml",
    "html5lib",
    "llama-cpp-python",
    "ctransformers",
    "transformers",
)


def logged_names(node: ast.AST) -> set[str]:
    """The names an expression would put in a log line. What ``len()`` only measures is left
    out: a log line may carry counts."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        return set()
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    for child in ast.iter_child_nodes(node):
        names |= logged_names(child)
    return names


def logging_calls_with_private_names(files: Iterable[Path]) -> list[str]:
    """Logging calls whose message or arguments name something private."""
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
            first = node.args[:1]
            message = [] if first and isinstance(first[0], ast.Constant) else first
            arguments = [*message, *node.args[1:], *(keyword.value for keyword in node.keywords)]
            names = set[str]().union(*(logged_names(argument) for argument in arguments))
            private = sorted(
                name
                for name in names
                if SENSITIVE_NAME.search(name) and not COUNT_NAME.search(name)
            )
            if private:
                shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                found.append(f"{shown.as_posix()}:{node.lineno}: logs {private}")
    return found


def forbidden_dependencies(pyproject: str) -> list[str]:
    """The forbidden libraries a ``pyproject.toml`` declares, whatever follows the name."""
    text = pyproject.lower().replace("_", "-")
    return [
        name
        for name in FORBIDDEN_DEPENDENCIES
        if re.search(rf'"{re.escape(name)}(?![a-z0-9])', text)
    ]


def unguarded_url_opens(files: Iterable[Path]) -> list[str]:
    """Functions that open a URL without checking, in the same function, whose it is."""
    found: list[str] = []
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        if "QDesktopServices.openUrl(" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            body = ast.get_source_segment(text, node) or ""
            if "QDesktopServices.openUrl(" in body and not LINK_GUARD.search(body):
                shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                found.append(f"{shown.as_posix()}:{node.lineno}: {node.name}")
    return found


def test_no_log_call_passes_a_token_key_body_query_or_prompt() -> None:
    """Tokens, keys, headers, bodies, drafts, sender rules and search text never reach a log
    line; a log line gets codes and counts (AGENTS.md invariants). The scan goes by name, so
    it cannot see an exception's text or a private value under a plain name."""
    assert logging_calls_with_private_names(SOURCE.rglob("*.py")) == []


def test_the_source_names_only_the_read_only_google_scopes() -> None:
    """Gmail is ``gmail.readonly``; Calendar adds two read-only scopes and nothing wider. The
    sign-in asks for exactly these, and no adapter names Gmail's full-access scope."""
    assert set(SIGN_IN_SCOPES) == ALLOWED_SCOPES
    named: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            named += [
                f"{path.relative_to(ROOT).as_posix()}:{number}: {scope}"
                for scope in GOOGLE_SCOPE.findall(line)
                if scope not in ALLOWED_SCOPES
            ]
    assert named == []
    assert offending_lines((SOURCE / "providers").rglob("*.py"), FULL_MAIL_SCOPE) == []


def test_google_adapters_make_no_writing_request() -> None:
    """The Gmail and Google Calendar adapters and the request code they share only read: no
    POST, PUT, PATCH or DELETE, by method or by verb, and no send, modify or trash path. The
    token endpoint (``providers/google/tokens.py``) is the one POST."""
    files = [
        path
        for folder in ("gmail", "gcal", "google")
        for path in (SOURCE / "providers" / folder).glob("*.py")
        if path != SOURCE / "providers" / "google" / "tokens.py"
    ]
    assert offending_lines(files, HTTP_WRITE) == []
    assert offending_lines(files, GMAIL_WRITE_PATH) == []


def test_timestamps_are_never_naive() -> None:
    """No ``utcnow``, ``utcfromtimestamp``, ``today``, ``strptime``, zone-less ``now()`` or
    one-argument ``fromtimestamp`` on ``datetime``. A naive ``datetime(...)`` literal and
    ``date.today()`` are not looked for."""
    assert offending_lines(SOURCE.rglob("*.py"), NAIVE_CLOCK) == []


def test_core_packages_import_no_network_library() -> None:
    """The domain, storage, text and ports packages and the services (but local setup's
    download) import no network library. That keeps requests in the adapters; it does not
    by itself show that provider JSON stays there."""
    allowed = {SOURCE / "services" / "local_setup.py"}
    files = [
        path
        for folder in ("domain", "storage", "text", "services", "ports")
        for path in (SOURCE / folder).rglob("*.py")
        if path not in allowed
    ]
    assert offending_lines(files, NETWORK_IMPORT) == []


def test_core_packages_import_no_adapter() -> None:
    """Provider JSON stays in its adapter because nothing in the domain, storage, text, ports
    or services packages imports one: they see domain models through the ports. Local setup
    alone reads the local adapter's pinned manifest and its download guard, never a reply."""
    setup = SOURCE / "services" / "local_setup.py"
    files = [
        path
        for folder in ("domain", "storage", "text", "services", "ports")
        for path in (SOURCE / folder).rglob("*.py")
        if path != setup
    ]
    assert offending_lines(files, ADAPTER_IMPORT) == []
    imported = ADAPTER_IMPORT.findall(setup.read_text(encoding="utf-8"))
    assert sorted(imported) == ["local.manifest", "local.transport"]


def test_no_forbidden_dependency_is_declared() -> None:
    """``pyproject.toml`` declares none of the libraries AGENTS.md rules out."""
    assert forbidden_dependencies((ROOT / "pyproject.toml").read_text(encoding="utf-8")) == []


def test_every_url_open_is_behind_a_link_check() -> None:
    """A function that opens a URL checks it against Gmail's or Calendar's host itself, and
    only the Google sign-in opens the system browser another way."""
    assert unguarded_url_opens(SOURCE.rglob("*.py")) == []
    browser = [
        path.relative_to(SOURCE).as_posix()
        for path in sorted(SOURCE.rglob("*.py"))
        if re.search(r"^\s*import webbrowser\b", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert browser == ["providers/gmail/oauth.py"]


def test_the_privacy_rules_find_what_they_are_for(tmp_path: Path) -> None:
    """The scans catch the slips they exist for, and pass what only looks like them."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import logging\nlogger = logging.getLogger('x')\n"
        "def f(body, count, api_key, excluded_senders, headers, usage):\n"
        "    logger.info('got %s', body)\n"
        "    logger.info('n=%d', count)\n"
        "    logger.info(f'x {body}')\n"
        "    logger.info('k', extra={'k': api_key})\n"
        "    logger.info('%d characters', len(body))\n"
        "    logger.info('%d tokens', usage.total_tokens)\n"
        "    logger.info('rules %s', excluded_senders)\n"
        "    logger.debug('%s', headers)\n",
        encoding="utf-8",
    )
    found = logging_calls_with_private_names([bad])
    assert [line.split(": logs")[0].rsplit(":", 1)[1] for line in found] == [
        "4",
        "6",
        "7",
        "10",
        "11",
    ]

    for line in ("await client.post(url)", 'http.stream("POST", url)', "request('PATCH', url)"):
        assert HTTP_WRITE.search(line), line
    for line in ("posted = 1", 'http.stream("GET", url)'):
        assert not HTTP_WRITE.search(line), line
    assert GMAIL_WRITE_PATH.search('f"{BASE}/messages/{id}/trash"')
    assert not GMAIL_WRITE_PATH.search('f"{BASE}/messages/{id}"')

    for line in (
        "datetime.now()",
        "datetime.now(",
        "datetime.now(tz=None)",
        "datetime.utcnow()",
        "datetime.fromtimestamp(stamp)",
        "datetime.today()",
        "datetime.strptime(text, FORMAT)",
    ):
        assert NAIVE_CLOCK.search(line), line
    for line in ("datetime.now(UTC)", "datetime.fromtimestamp(stamp, UTC)", "date.today()"):
        assert not NAIVE_CLOCK.search(line), line

    scope = "https://www.googleapis.com/auth/gmail.readonly"
    assert GOOGLE_SCOPE.findall(f"Asks for {scope}.") == [scope]
    assert GOOGLE_SCOPE.findall('"https://www.googleapis.com/auth/gmail.modify"')
    assert FULL_MAIL_SCOPE.search('SCOPE = "https://mail.google.com/"')
    assert not FULL_MAIL_SCOPE.search('url.startswith("https://mail.google.com/mail/")')

    for line in ("import httpx", "from httpx import Client", "import os, httpx", "import socket"):
        assert NETWORK_IMPORT.search(line), line
    for line in (
        "from urllib.parse import quote",
        "from mailbrief.providers.google.requests import x",
    ):
        assert not NETWORK_IMPORT.search(line), line

    for declared in (
        '"msgraph-sdk>=1.0"',
        '"google-auth-oauthlib>=1"',
        '"tenacity~=8.2"',
        "\"lxml; sys_platform == 'win32'\"",
        '"python_dotenv>=1"',
        '"bs4"',
    ):
        assert forbidden_dependencies(f"dependencies = [{declared}]"), declared
    assert forbidden_dependencies('dependencies = ["httpx>=0.28", "msal>=1.38"]') == []

    opens = tmp_path / "opens.py"
    opens.write_text(
        "def guarded(url):\n    if is_gmail_link(url):\n"
        "        QDesktopServices.openUrl(QUrl(url))\n"
        "def unguarded(url):\n    QDesktopServices.openUrl(QUrl(url))\n",
        encoding="utf-8",
    )
    assert [line.rsplit(" ", 1)[1] for line in unguarded_url_opens([opens])] == ["unguarded"]


OWNER_TABLES: Final = [
    "Action",
    "ActionTag",
    "ActionActivity",
    "ActionStep",
    "ActionSource",
    "ActionSeries",
    "ActionProposal",
    "SuggestionDecision",
    "Draft",
    "DraftVersion",
    "DraftSource",
    "Project",
    "LifeArea",
    "OwnerPreferences",
    "CalendarEvent",
    "WorkBlock",
    "WorkingHours",
    "DayPlan",
    "DayPlanItem",
    "MetricDefinition",
    "MetricEntry",
    "MetricEntryChange",
    "Dashboard",
    "TaskCommand",
    "NoteCommand",
    "BlockCommand",
    "PlanningCommand",
    "MetricCommand",
    "DashboardCommand",
]
BULK_OWNER_WRITE: Final = re.compile(
    rf"(?:update|delete|insert)\(\s*(?:{'|'.join(OWNER_TABLES)})Table\b"
)


def test_owner_records_change_only_through_orm_objects() -> None:
    """A bulk UPDATE or DELETE leaves loaded rows stale and skips the revision, so none may
    target an owner-record table. Statements spelled other ways are not seen."""
    assert offending_lines((SOURCE / "storage").rglob("*.py"), BULK_OWNER_WRITE) == []
    assert offending_lines((SOURCE / "services").rglob("*.py"), BULK_OWNER_WRITE) == []
    assert all(hasattr(tables, f"{name}Table") for name in OWNER_TABLES)
    for statement in ("delete(ActionTable)", "sqlite_insert(CalendarEventTable)"):
        assert BULK_OWNER_WRITE.search(statement)
    assert BULK_OWNER_WRITE.search("update(OwnerPreferencesTable).values(")
    assert not BULK_OWNER_WRITE.search("delete(ActionSuggestionTable).where(x)")
