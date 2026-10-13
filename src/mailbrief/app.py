"""Desktop application entry point."""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from qasync import QEventLoop

from mailbrief.infra.data_lock import create_data_lock
from mailbrief.paths import AppPaths, configure_qt_identity
from mailbrief.storage.recovery import BackupValidationError, restore_backup_holding_lock
from mailbrief.ui.appearance import APPEARANCE_FILE, AppearanceStore
from mailbrief.ui.diagnostics import configure_logging, log_failure, logger
from mailbrief.ui.glass.profile import log_summary as log_paint_summary
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import ThemeMode, apply_theme, current_scheme


def use_english_locale() -> None:
    """Make English the default locale, so Qt's own widgets (the calendar pop-ups' month
    and weekday names) read in English like the rest of the window, whatever the system
    language. A widget takes the default locale when it is created, so this runs before
    any widget exists."""
    QLocale.setDefault(QLocale(QLocale.Language.English))


class MailBriefApplication(QApplication):
    """Lets the window answer a quit request (Cmd+Q, the Dock's Quit) before Qt closes any
    window. Qt closes every window it can for a quit, so a dialog holding unsaved input
    would otherwise be closed, and the input lost, before the main window could ask."""

    def __init__(self, arguments: list[str]) -> None:
        super().__init__(arguments)
        # True means "not now": the window is asking the owner first.
        self.quit_check: Callable[[], bool] | None = None

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Quit and self.quit_check is not None and self.quit_check():
            event.ignore()  # On macOS, the quit is cancelled.
            return True
        return QApplication.event(self, event)


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Create or return the process-wide Qt application."""
    configure_qt_identity()
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    application = MailBriefApplication(list(arguments) if arguments is not None else sys.argv)
    icon_path = Path(__file__).parent / "resources" / "MailBrief.ico"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    return application


def apply_saved_scheme(store: AppearanceStore, *, themed: bool) -> None:
    """The owner's colours, once the data folder is locked; the theme applied before
    then uses the default scheme. Nothing changes when that theme failed."""
    application = QApplication.instance()
    if not themed or not isinstance(application, QApplication):
        return
    scheme = store.load().color_scheme
    if scheme is current_scheme():
        return
    try:
        apply_theme(application, ThemeMode.DARK, scheme)
    except Exception as exc:
        log_failure(exc)  # apply_theme has put back the default scheme.


async def run_desktop(theme_failure: Exception | None = None) -> None:
    """Keep the loop alive until pending work and resources have shut down.

    ``theme_failure`` is why the theme couldn't be applied, logged once desktop.log is open.
    """
    paths = AppPaths.from_qt()
    lock = create_data_lock(paths.data_dir)
    if not lock.tryLock(0):
        message = (
            "MailBrief is already running, or a MailBrief diagnostic command is using its "
            "data. Close it and retry."
            if lock.error() == QLockFile.LockError.LockFailedError
            else "MailBrief could not lock its data folder. Check folder access and retry."
        )
        QMessageBox.information(None, "MailBrief", message)
        return
    handler = None
    try:
        handler = configure_logging(paths.data_dir)
        if theme_failure is not None:
            log_failure(theme_failure)
        appearance = AppearanceStore(paths.data_dir / APPEARANCE_FILE)
        apply_saved_scheme(appearance, themed=theme_failure is None)
        window = MainWindow(
            DesktopRuntime(paths.database_path),
            database_path=paths.database_path,
            appearance=appearance,
        )
        closed = asyncio.Event()
        window.closing.connect(closed.set)
        application = QApplication.instance()
        if isinstance(application, MailBriefApplication):
            application.quit_check = window.ask_before_quitting
        window.show()
        window.start(window.initialize)
        try:
            await closed.wait()
        finally:
            await window.shutdown()
            if isinstance(application, MailBriefApplication):
                application.quit_check = None
        if window.pending_restore is not None:
            # The folder lock stays held across shutdown and publication. No editor,
            # autosave task or pooled connection can write to the restored database.
            try:
                previous = await asyncio.to_thread(
                    restore_backup_holding_lock,
                    window.pending_restore[0],
                    paths.database_path,
                    replace=True,
                    expected=window.pending_restore[1],
                )
            except BackupValidationError as exc:
                # Fixed, safe text: archive content never appears in these messages.
                log_failure(exc)
                QMessageBox.warning(
                    None, "MailBrief recovery", f"{exc} The current database was kept."
                )
            except Exception as exc:
                log_failure(exc)
                QMessageBox.warning(
                    None,
                    "MailBrief recovery",
                    "Restore could not finish. "
                    "The current database was kept. Check free space, folder "
                    "access and other database clients, then retry.",
                )
            else:
                retained = f"Previous database: {previous}\n" if previous is not None else ""
                QMessageBox.information(
                    None,
                    "MailBrief recovery",
                    "Backup restored. "
                    f"{retained}"
                    "Relaunch MailBrief. Automatic AI permission is off.",
                )
    except Exception as exc:
        log_failure(exc)
        QMessageBox.warning(None, "MailBrief", "MailBrief could not start or close cleanly.")
    finally:
        if handler is not None:
            log_paint_summary()  # Only with MAILBRIEF_PAINT_PROFILE=1.
            logger.removeHandler(handler)
            handler.close()
        lock.unlock()


def main(arguments: Sequence[str] | None = None) -> int:
    """Launch Qt and asyncio on one responsive event loop."""
    parser = argparse.ArgumentParser(prog="mailbrief")
    parser.add_argument(
        "--smoke-test-dir",
        type=Path,
        help="Run an offline package check using only this test directory, then exit.",
    )
    parser.add_argument(
        "--smoke-scenario",
        choices=("full", "verify", "hold-lock", "try-lock", "upgrade"),
        default="full",
        help="Isolated package scenario; requires --smoke-test-dir.",
    )
    parser.add_argument("--export-notices", type=Path, help="Export bundled third-party notices.")
    options = parser.parse_args(arguments)
    if options.smoke_scenario != "full" and options.smoke_test_dir is None:
        parser.error("--smoke-scenario requires --smoke-test-dir")
    if options.export_notices is not None:
        source = Path(__file__).parent / "resources" / "THIRD_PARTY_NOTICES.txt"
        if not source.is_file():
            parser.error("Notices are available in the Windows package.")
        # Never overwrite an existing owner file.
        try:
            with options.export_notices.open("x", encoding="utf-8") as stream:
                stream.write(source.read_text("utf-8"))
        except OSError:
            parser.error("Cannot export notices; select a new file in a writable folder.")
        return 0
    application = create_application([sys.argv[0]])
    use_english_locale()  # Before any widget: each takes the default locale when created.
    theme_failure: Exception | None = None
    try:
        apply_theme(application, ThemeMode.DARK)
    except Exception as exc:
        # The theme is cosmetic: apply_theme has put back what was there, with tokens that
        # suit it, so the app runs unthemed rather than not at all. desktop.log isn't open
        # yet, so the failure is logged once it is.
        theme_failure = exc
    application.setQuitOnLastWindowClosed(False)
    with QEventLoop(application) as loop:
        asyncio.set_event_loop(loop)
        if options.smoke_test_dir is not None:
            from mailbrief.ui.smoke import run_scenario

            try:
                loop.run_until_complete(
                    run_scenario(options.smoke_test_dir, options.smoke_scenario)
                )
            except Exception as exc:
                # Avoid an unattended frozen GUI error dialog and secret-bearing tracebacks.
                report = options.smoke_test_dir / "smoke-result.json"
                report.parent.mkdir(parents=True, exist_ok=True)
                failure = {"ok": False, "error": type(exc).__name__}
                if isinstance(exc, ModuleNotFoundError):
                    failure["missing_module"] = exc.name
                report.write_text(json.dumps(failure), encoding="utf-8")
                return 1
        else:
            loop.run_until_complete(run_desktop(theme_failure))
    return 0
