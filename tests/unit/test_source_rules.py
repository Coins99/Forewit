"""Rules for the source itself, so a slip shows up in the tests rather than on screen. Each
failure lists the offending lines as "path:line: text"."""

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent
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
# A "?" standing alone inside a string: what a non-ASCII character (a dot, a dash, an
# arrow) becomes when a tool that can't write it saves the file.
LOST_CHARACTER: Final = re.compile(r"""["'][^"'\n]*\s\?\s[^"'\n]*["']""")


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


def test_no_ui_text_has_a_question_mark_where_a_character_was_lost() -> None:
    """The Calendar's legend read "Your events    ? Google Calendar    ? Action dates"."""
    assert offending_lines(UI.rglob("*.py"), LOST_CHARACTER) == []


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


def plain_receivers(scope: ast.AST) -> set[str]:
    """What, under ``scope``, is promised to hold no rich text: the receivers of
    ``setTextFormat(...PlainText)``, which keep what they show plain, and of ``setPixmap``,
    and the widgets handed to ``follow_theme``, which paints their tinted icon. As source
    text: ``label``, ``self.glyph``."""
    found: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and (
            function.attr == "setPixmap"
            or (
                function.attr == "setTextFormat"
                and node.args
                and ast.unparse(node.args[0]).endswith("PlainText")
            )
        ):
            found.add(ast.unparse(function.value))
        elif isinstance(function, ast.Name) and function.id == "follow_theme" and node.args:
            found.add(ast.unparse(node.args[0]))
    return found


def label_slips(path: Path, root: Path) -> list[str]:
    """The ``QLabel`` constructions and subclasses of ``path`` with no such promise, as
    "path:line"."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    promised: dict[ast.AST, set[str]] = {}

    def promises(scope: ast.AST) -> set[str]:
        if scope not in promised:
            promised[scope] = plain_receivers(scope)
        return promised[scope]

    def enclosing(node: ast.AST, kinds: tuple[type[ast.AST], ...]) -> ast.AST:
        while node in parents:
            node = parents[node]
            if isinstance(node, kinds):
                return node
        return tree

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            ast.unparse(base).split(".")[-1] == "QLabel" for base in node.bases
        ):
            if "self" not in promises(node):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "QLabel":
            parent = parents[node]
            if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
                target = ast.unparse(parent.targets[0])
            elif isinstance(parent, ast.AnnAssign):
                target = ast.unparse(parent.target)
            else:
                lines.append(node.lineno)  # Held in no variable: nothing can promise for it.
                continue
            # A name is promised in its function, an attribute of self anywhere in its class.
            kinds: tuple[type[ast.AST], ...] = (
                (ast.ClassDef,)
                if target.startswith("self.")
                else (ast.FunctionDef, ast.AsyncFunctionDef)
            )
            if target not in promises(enclosing(node, kinds)):
                lines.append(node.lineno)
    return [f"{path.relative_to(root).as_posix()}:{line}" for line in sorted(lines)]


def unplain_labels(files: Iterable[Path], root: Path = ROOT) -> list[str]:
    """Every ``QLabel`` built without a promise that it shows plain text or only an icon,
    as "path:line". A ``QLabel(...)`` (bare or as ``module.QLabel``) is held in a variable
    that, in its function (a ``self.`` attribute: in its class), gets ``setTextFormat`` with
    ``PlainText``, ``setPixmap`` or ``follow_theme``; a subclass of ``QLabel`` does the
    same to ``self``. Labels are found by the name ``QLabel``, so one imported under another
    name is not, and neither are other widgets that read rich text (a message box, a text
    browser)."""
    return [slip for path in sorted(files) for slip in label_slips(path, root)]


def test_every_label_in_the_ui_is_plain_text_or_an_icon() -> None:
    """Mail and AI text reaches the screen through plain-text labels only: every ``QLabel``
    under ``ui`` is promised plain (or an icon) for its own variable, naming the line
    otherwise. A variable reused for two labels is promised once for both."""
    assert unplain_labels(UI.rglob("*.py")) == []


def test_the_label_rule_finds_what_it_is_for(tmp_path: Path) -> None:
    def slips(source: str) -> list[str]:
        (tmp_path / "widget.py").write_text(dedent(source), encoding="utf-8")
        return unplain_labels([tmp_path / "widget.py"], root=tmp_path)

    assert slips("def make(t):\n    return QLabel(t)\n") == ["widget.py:2"]
    assert slips("def make(t):\n    return QtWidgets.QLabel(t)\n") == ["widget.py:2"]
    assert slips("LABEL = QLabel('<b>x</b>')\n") == ["widget.py:1"]
    assert slips("class Row(QLabel):\n    pass\n") == ["widget.py:1"]
    # Plain text set on another label, or only written in a comment, promises nothing.
    assert slips(
        """\
        def make(a, b):
            one = QLabel(a)
            two = QLabel(b)
            one.setTextFormat(Qt.TextFormat.PlainText)
        """
    ) == ["widget.py:3"]
    assert slips("def make(t):\n    label = QLabel(t)  # PlainText\n    return label\n") == [
        "widget.py:2"
    ]
    # A label in a nested function is reported once.
    assert slips(
        """\
        def outer():
            def inner(t):
                return QLabel(t)
            return inner
        """
    ) == ["widget.py:3"]
    for promise in (
        "label.setTextFormat(Qt.TextFormat.PlainText)",
        "label.setPixmap(pixmap)",
        "follow_theme(label, paint)",
    ):
        assert (
            slips(f"def make(t):\n    label = QLabel(t)\n    {promise}\n    return label\n") == []
        )
    assert (
        slips(
            """\
        class Row(QWidget):
            def __init__(self):
                self.title = QLabel()

            def show(self):
                self.title.setTextFormat(Qt.TextFormat.PlainText)
        """
        )
        == []
    )
    assert (
        slips(
            """\
        class Row(QLabel):
            def __init__(self):
                super().__init__()
                self.setTextFormat(Qt.TextFormat.PlainText)
        """
        )
        == []
    )


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
    for line in ('"Your events    ? Google Calendar"', "'Today ? Tomorrow'"):
        assert LOST_CHARACTER.search(line), line
    for line in ('"Discard it?"', '"WHERE id = ?"', "a if b else c  # why? because", '"Who? Me."'):
        assert not LOST_CHARACTER.search(line), line
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
    r"\bdt\.now\(\s*\)|"
    # The system's day: date.today, called or handed over (default_factory=date.today), a
    # date imported under another name, which hides it, and Qt's own clock.
    r"\bdate\.today\b|\bimport\b[^#\n]*\bdate\s+as\b|"
    r"\bQDate\.currentDate\(|\bQDateTime\.currentDateTime\(|\bQTime\.currentTime\("
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
# Workload, free time and local suggestions (ADRs 0029 to 0031): worked out locally, never sent.
PLANNING_MODULES: Final = frozenset(
    {
        "mailbrief.domain.availability",
        "mailbrief.domain.planning_intervals",
        "mailbrief.domain.scheduling",
        "mailbrief.domain.scheduling_time",
        "mailbrief.domain.workload",
        "mailbrief.services.availability",
        "mailbrief.services.scheduling",
    }
)
# The only modules outside the desktop that read them: local planning, calendar and Insights
# code. None is a provider adapter, a port or a builder of an AI request.
PLANNING_READERS: Final = frozenset(
    {
        "domain/availability.py",
        "domain/scheduling.py",
        "domain/scheduling_time.py",
        "domain/workload.py",
        "services/availability.py",
        "services/metric_calculations.py",
        "services/scheduling.py",
        "services/work_blocks.py",
        "storage/metric_sources.py",
    }
)


def imported_modules(source: str, package: tuple[str, ...]) -> set[str]:
    """Every module ``source`` imports, by full name, for a file in ``package``: plain and
    ``from`` imports however they are wrapped (each imported name is also tried as a
    submodule), relative ones, and import_module() of a literal."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = ".".join((*base, *filter(None, [node.module])))
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            called = node.func
            name = (
                called.attr
                if isinstance(called, ast.Attribute)
                else called.id
                if isinstance(called, ast.Name)
                else ""
            )
            first = node.args[0]
            if (
                name == "import_module"
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                found.add(first.value)
    return found


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


LOGGER_NAMES = {"logger", "log", "logging", "_logger", "LOGGER"}


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
                and node.func.value.id in LOGGER_NAMES
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


def _is_text_of(node: ast.AST, name: str) -> bool:
    """Whether ``node`` puts the text of the exception called ``name`` in a log line: the
    exception itself, ``str()`` or ``repr()`` of it, or an f-string that formats it."""
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"str", "repr"} and any(_is_text_of(a, name) for a in node.args)
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(part, ast.FormattedValue) and _is_text_of(part.value, name)
            for part in node.values
        )
    return False


def logging_calls_with_exception_text(files: Iterable[Path]) -> list[str]:
    """Logging calls in an ``except ... as name`` block that log the exception's text."""
    found: list[str] = []
    for path in sorted(files):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or handler.name is None:
                continue
            for node in ast.walk(handler):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in LOG_METHODS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in LOGGER_NAMES
                    and any(_is_text_of(argument, handler.name) for argument in node.args)
                ):
                    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                    found.append(f"{shown.as_posix()}:{node.lineno}: logs {handler.name}")
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


def test_no_log_call_passes_an_exception_s_text(tmp_path: Path) -> None:
    """An exception's text can hold whatever raised it was handling, so a log line names its
    type or a code, never the exception itself. The scan sees an exception passed as it is,
    through ``str()`` or ``repr()``, or in an f-string, in its own ``except`` block; a
    traceback (``logger.exception``, ``exc_info``) is not looked for."""
    assert logging_calls_with_exception_text(SOURCE.rglob("*.py")) == []
    sample = tmp_path / "sample.py"
    sample.write_text(
        "try:\n    pass\nexcept ValueError as exc:\n"
        "    logger.warning('failed: %s', exc)\n"
        "    logger.warning(f'failed: {exc}')\n"
        "    logger.warning('failed: %s', str(exc))\n"
        "    logger.warning('failed: %s', type(exc).__name__)\n",
        encoding="utf-8",
    )
    found = logging_calls_with_exception_text([sample])
    assert [line.rsplit(":", 2)[1] for line in found] == ["4", "5", "6"]


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
    one-argument ``fromtimestamp`` on ``datetime``, and none of the system's day, which is
    not the owner's: ``date.today`` (called or not), ``date`` imported under another name,
    and Qt's ``currentDate``, ``currentDateTime`` and ``currentTime``. A naive
    ``datetime(...)`` literal is not looked for."""
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


def test_only_local_planning_code_reads_workload_or_scheduling() -> None:
    """Workload, free time and local suggestions never leave the machine: outside the desktop
    only the listed local modules import them, so no adapter, port or builder of an AI request
    does. A new reader has to be listed here on purpose. What a module reaches through another
    module is not followed."""
    readers = set()
    for path in SOURCE.rglob("*.py"):
        parts = path.relative_to(SOURCE).parts
        modules = imported_modules(path.read_text(encoding="utf-8"), ("mailbrief", *parts[:-1]))
        if parts[0] != "ui" and modules & PLANNING_MODULES:
            readers.add("/".join(parts))
    assert readers == PLANNING_READERS
    assert not any(name.startswith(("providers/", "ports/")) for name in PLANNING_READERS)
    wrapped = (
        "from mailbrief.domain import (\n    events,\n    workload,\n)\n"
        "from ..domain import scheduling\n"
        "from . import availability\n"
        "import_module('mailbrief.domain.planning_intervals')\n"
    )
    assert imported_modules(wrapped, ("mailbrief", "services")) >= {
        "mailbrief.domain.workload",
        "mailbrief.domain.scheduling",
        "mailbrief.services.availability",
        "mailbrief.domain.planning_intervals",
    }


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
        "date.today()",
        "datetime.date.today()",
        "created: date = Field(default_factory=date.today)",
        "from datetime import date as d",
        "from datetime import UTC, date as day, datetime",
        "QDate.currentDate()",
        "QDateTime.currentDateTime()",
        "QTime.currentTime()",
    ):
        assert NAIVE_CLOCK.search(line), line
    for line in (
        "datetime.now(UTC)",
        "datetime.fromtimestamp(stamp, UTC)",
        "datetime.now(zone).date()",
        "from datetime import UTC, date, datetime",
        "# the date as the owner reads it",
        "today = self.today()",
        "if target <= details.today:",
        '"today": request.today.isoformat(),',
        "QDate(day.year, day.month, day.day)",
    ):
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
