"""What setup downloads: the manifest for each supported computer, and what every pin must
be (HTTPS to a listed host, a full SHA-256, a size, a safe executable path)."""

import re
from pathlib import PurePosixPath

import httpx
import pytest

from mailbrief.providers.local.manifest import (
    MACOS_ARM64,
    MODEL,
    WINDOWS_X64,
    Manifest,
    PinnedFile,
    manifest_for,
)
from mailbrief.providers.local.transport import host_allowed

MANIFESTS = pytest.mark.parametrize(
    "manifest", [WINDOWS_X64, MACOS_ARM64], ids=["windows-x64", "macos-arm64"]
)


def pins(manifest: Manifest) -> tuple[PinnedFile, PinnedFile]:
    return manifest.runtime.archive, manifest.model.file


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("win32", "AMD64", WINDOWS_X64),
        ("win32", "x86_64", WINDOWS_X64),
        ("darwin", "arm64", MACOS_ARM64),
        ("win32", "ARM64", None),
        ("darwin", "x86_64", None),
        ("linux", "x86_64", None),
    ],
)
def test_manifest_for_each_computer(system: str, machine: str, expected: Manifest | None) -> None:
    assert manifest_for(system, machine) is expected


@MANIFESTS
def test_every_pinned_url_is_https_to_a_listed_host(manifest: Manifest) -> None:
    for pinned in pins(manifest):
        url = httpx.URL(pinned.url)
        assert url.scheme == "https"
        assert url.port is None and not url.userinfo
        assert host_allowed(url.host, manifest.hosts)


@MANIFESTS
def test_listed_hosts_are_lower_case_names(manifest: Manifest) -> None:
    # host_allowed lowers the host it is given, not the entries.
    for host in manifest.hosts:
        assert host == host.lower() and host.strip(".") and " " not in host


@MANIFESTS
def test_every_pin_has_a_sha256_a_size_and_a_plain_file_name(manifest: Manifest) -> None:
    for pinned in pins(manifest):
        assert re.fullmatch(r"[0-9a-f]{64}", pinned.sha256)
        assert pinned.size > 0
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", pinned.name)
        assert httpx.URL(pinned.url).path.endswith(f"/{pinned.name}")
        assert pinned.license and pinned.source
    assert manifest.runtime.unpacked_size > 0


@pytest.mark.parametrize(
    ("manifest", "suffix", "server"),
    [(WINDOWS_X64, ".zip", "llama-server.exe"), (MACOS_ARM64, ".tar.gz", "llama-server")],
    ids=["windows-x64", "macos-arm64"],
)
def test_the_runtime_executable_is_a_relative_path_inside_the_archive(
    manifest: Manifest, suffix: str, server: str
) -> None:
    runtime = manifest.runtime
    assert runtime.archive.name.endswith(suffix)  # How setup unpacks it.
    executable = runtime.executable
    path = PurePosixPath(executable)
    assert "\\" not in executable and ":" not in executable
    assert not path.is_absolute()
    assert all(part not in ("", ".", "..") for part in path.parts)
    assert path.name == server
    assert runtime.version and runtime.version in runtime.archive.url


def test_both_manifests_share_the_model() -> None:
    assert WINDOWS_X64.model is MODEL and MACOS_ARM64.model is MODEL
    assert WINDOWS_X64.hosts == MACOS_ARM64.hosts
