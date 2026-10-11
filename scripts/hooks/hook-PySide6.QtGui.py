"""Keep Qt's upstream hook, excluding two unused Windows plugin dependency trees."""

from pathlib import Path

from PyInstaller.compat import is_win
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
if is_win:
    # This Widgets app has no PDF image reader or QML virtual keyboard. Removing their
    # entry plugins before dependency analysis also avoids QtPdf/Quick/Qml libraries.
    binaries = [
        (source, destination)
        for source, destination in binaries
        if Path(source).name.lower() not in {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}
    ]
