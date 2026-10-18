"""Published candidates must identify and hash exactly the tagged, tested build."""

import json
from pathlib import Path

import pytest

from scripts.package_manifest import sha256
from scripts.stage_release import validate_tag, verify


def candidate(directory: Path) -> None:
    name = "Forewit-v0.1.0-windows-x64.exe"
    executable = directory / name
    executable.write_bytes(b"Synthetic release executable")
    manifest = {
        "format": 1,
        "commit": "synthetic-commit",
        "version": "0.1.0",
        "dirty": False,
        "architecture": "x64",
        "executable": name,
        "executable_sha256": sha256(executable),
    }
    (directory / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "README.txt").write_text("Synthetic instructions", encoding="utf-8")
    (directory / "THIRD_PARTY_NOTICES.txt").write_text("Synthetic notices", encoding="utf-8")
    files = sorted(path for path in directory.iterdir() if path.name != "SHA256SUMS.txt")
    (directory / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )


def test_candidate_hashes_and_identity(tmp_path: Path) -> None:
    candidate(tmp_path)
    assert verify(tmp_path, "synthetic-commit", "v0.1.0")["version"] == "0.1.0"


def test_modified_executable_refused(tmp_path: Path) -> None:
    candidate(tmp_path)
    (tmp_path / "Forewit-v0.1.0-windows-x64.exe").write_bytes(b"Changed after verification")
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path, "synthetic-commit", "v0.1.0")


def test_other_commit_refused(tmp_path: Path) -> None:
    candidate(tmp_path)
    with pytest.raises(ValueError, match="identity"):
        verify(tmp_path, "another-commit", "v0.1.0")


def test_unexpected_asset_refused(tmp_path: Path) -> None:
    candidate(tmp_path)
    (tmp_path / "token.json").write_text("Synthetic secret", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        verify(tmp_path, "synthetic-commit", "v0.1.0")


def test_duplicate_checksum_refused(tmp_path: Path) -> None:
    candidate(tmp_path)
    path = tmp_path / "SHA256SUMS.txt"
    text = path.read_text("utf-8")
    path.write_text(text + text.splitlines()[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid"):
        verify(tmp_path, "synthetic-commit", "v0.1.0")


@pytest.mark.parametrize("tag", ["v9.9.9", "0.1.0", "v0.1.0-beta", "v0.1.0;echo bad"])
def test_invalid_tag_fails_before_git(tag: str) -> None:
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="Tag must match"):
        validate_tag(root, tag)
