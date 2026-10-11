"""Generate Windows packaging resources from the project's existing artwork and version."""

import importlib.metadata
import os
import struct
import sys
from pathlib import Path

from packaging.requirements import Requirement

from scripts.package_manifest import project_version


def runtime_packages() -> list[str]:
    """Follow installed runtime requirements, including selected extras and OS markers."""
    pending: list[tuple[str, frozenset[str]]] = [("mailbrief", frozenset())]
    visited: set[tuple[str, frozenset[str]]] = set()
    names = {"pyinstaller"}  # The bootloader is distributed; its build tools are not.
    while pending:
        name, extras = pending.pop()
        name = name.lower().replace("_", "-")
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        names.add(name)
        for raw in importlib.metadata.requires(name) or ():
            requirement = Requirement(raw)
            if requirement.marker is None or any(
                requirement.marker.evaluate({"extra": extra}) for extra in extras | {""}
            ):
                pending.append((requirement.name, frozenset(requirement.extras)))
    return sorted(names - {"mailbrief"})


def write_resources(root: Path, directory: Path) -> tuple[Path, Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    from mailbrief.ui.theme.tokens import DARK

    application = QApplication.instance() or QApplication([])
    directory.mkdir(parents=True, exist_ok=True)
    svg = (root / "src/mailbrief/ui/theme/icons/mail.svg").read_text("utf-8")
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", DARK.accent_fg).encode()))
    images: list[bytes] = []
    for size in (16, 32, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(DARK.panel))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size / 6, size / 6)
        renderer.render(painter, QRectF(size / 6, size / 6, size * 2 / 3, size * 2 / 3))
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        # PySide6 6.11 accepts str here at runtime, despite its bytes-only type stub.
        if not image.save(buffer, "PNG"):  # type: ignore[call-overload]
            raise ValueError("Unable to generate the application icon.")
        images.append(bytes(buffer.data().data()))
    icon = directory / "MailBrief.ico"
    offset = 6 + 16 * len(images)
    content = struct.pack("<HHH", 0, 1, len(images))
    for size, image_bytes in zip((16, 32, 48, 64, 128, 256), images, strict=True):
        content += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image_bytes), offset
        )
        offset += len(image_bytes)
    icon.write_bytes(content + b"".join(images))
    version = project_version(root)
    numeric = tuple(int(part) for part in version.split(".")) + (0,)
    metadata = directory / "windows-version.txt"
    metadata.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers="
        + repr(numeric)
        + ", prodvers="
        + repr(numeric)
        + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0', ["
        f"StringStruct('FileVersion', '{version}'), StringStruct('ProductVersion', '{version}'), "
        "StringStruct('ProductName', 'MailBrief'), StringStruct('FileDescription', 'MailBrief'), "
        "StringStruct('OriginalFilename', 'MailBrief.exe')])]), "
        "VarFileInfo([VarStruct('Translation', "
        "[1033, 1200])])])\n",
        encoding="utf-8",
    )
    notices = ["MailBrief\n", (root / "LICENSE").read_text("utf-8")]
    for path in ("src/mailbrief/ui/theme/fonts/OFL.txt", "src/mailbrief/ui/theme/icons/LICENSE"):
        notices.extend([f"\n{path}\n", (root / path).read_text("utf-8")])
    for package in runtime_packages():
        distribution = importlib.metadata.distribution(package)
        for item in distribution.files or ():
            if ".dist-info/" in str(item) and any(
                word in item.name.lower() for word in ("license", "copying", "notice")
            ):
                text = Path(str(distribution.locate_file(item))).read_text(
                    "utf-8", errors="replace"
                )
                notices.extend([f"\n{package}: {item.name}\n", text])
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if license_file.is_file():
        notices.extend(["\nPython\n", license_file.read_text("utf-8")])
    (directory / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    # Keep the Qt application alive until all paint resources are released.
    del application
    return icon, metadata
