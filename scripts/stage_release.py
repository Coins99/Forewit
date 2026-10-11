"""Stage the exact tested Windows executable and verify release assets before upload."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.package_manifest import (  # noqa: E402
    audit_names,
    embedded_notices,
    project_version,
    sha256,
    verify_manifest,
)


def validate_tag(root: Path, tag: str) -> str:
    if tag != "v" + project_version(root):
        raise ValueError("Tag must match the stable project and runtime version.")
    commit = subprocess.check_output(
        ["git", "rev-parse", f"{tag}^{{commit}}"], cwd=root, text=True
    ).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if (
        commit != head
        or subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "origin/main"],
            cwd=root,
            check=False,
        ).returncode
    ):
        raise ValueError("Release must build the exact tagged commit from main.")
    return commit


def stage(root: Path, executable: Path, destination: Path, tag: str) -> None:
    commit = validate_tag(root, tag)
    manifest = verify_manifest(root, executable)
    if manifest.get("dirty") is not False:
        raise ValueError("Release manifest does not match the clean tagged executable.")
    destination.mkdir(parents=True, exist_ok=False)
    name = f"MailBrief-{tag}-windows-x64.exe"
    shutil.copy2(executable, destination / name)
    manifest["executable"] = name
    (destination / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    (destination / "THIRD_PARTY_NOTICES.txt").write_bytes(embedded_notices(executable))
    (destination / "README.txt").write_text(
        f"MailBrief {tag} - Windows x64\n\n"
        "Copy the EXE to your Desktop and double-click. Python and an installer are not required.\n"
        "This candidate is unsigned. Windows may show an unrecognized-publisher warning.\n"
        "Gmail and Groq setup remain in Settings. "
        "Keep the selected OAuth client file in a stable location.\n"
        "Saved work lives in your existing Windows application-data profile, not beside this EXE.\n"
        "To update: back up your data, close MailBrief, replace the EXE and reopen.\n"
        "An older EXE may not read an upgraded database. "
        "Retain backups and any newer writing for recovery.\n"
        "Copying the EXE to another PC does not transfer data or credentials.\n"
        "Owner live-account, clean-profile and security acceptance "
        "must be recorded before publication.\n",
        encoding="utf-8",
    )
    files = sorted(path for path in destination.iterdir() if path.is_file())
    (destination / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    verify(destination, commit, tag)


def verify(directory: Path, commit: str, tag: str) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((directory / "build-manifest.json").read_text("utf-8"))
    name = f"MailBrief-{tag}-windows-x64.exe"
    expected = {name, "build-manifest.json", "THIRD_PARTY_NOTICES.txt", "README.txt"}
    actual = {path.name for path in directory.iterdir()}
    if actual != expected | {"SHA256SUMS.txt"}:
        raise ValueError("Release contains missing or unexpected assets.")
    audit_names(actual)
    if (
        manifest.get("format") != 1
        or manifest.get("commit") != commit
        or manifest.get("version") != tag[1:]
        or manifest.get("executable") != name
        or manifest.get("architecture") != "x64"
        or manifest.get("dirty") is not False
    ):
        raise ValueError("Release identity does not match the requested tag and commit.")
    checksums: dict[str, str] = {}
    for line in (directory / "SHA256SUMS.txt").read_text("utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        if (
            filename in checksums
            or filename not in expected
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("Invalid release checksum list.")
        checksums[filename] = digest
    if checksums.keys() != expected or any(
        sha256(directory / file) != digest for file, digest in checksums.items()
    ):
        raise ValueError("Release asset checksum mismatch.")
    if manifest.get("executable_sha256") != checksums[name]:
        raise ValueError("Executable and manifest checksum mismatch.")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--executable", type=Path, default=Path("out/dist/onefile/MailBrief.exe"))
    parser.add_argument("--destination", type=Path, default=Path("out/release"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--commit")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if options.verify_only:
        if not options.commit:
            parser.error("--verify-only requires --commit")
        verify(options.destination, options.commit, options.tag)
    else:
        stage(root, options.executable.resolve(), options.destination.resolve(), options.tag)
    print("Release asset identity and hashes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
