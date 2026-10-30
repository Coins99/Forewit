"""What local AI setup downloads: one llama.cpp release and one model, each pinned by URL,
size and SHA-256, with its license and where it comes from (P0-B2 Part 2).

Changing either is an edit here plus a benchmark rerun (docs/local-ai-benchmark.md), never
something that happens on its own.
"""

import platform
import sys
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PinnedFile:
    """One download: where it is, and what it must be."""

    name: str  # Its file name on disk.
    url: str
    size: int
    sha256: str
    license: str
    source: str  # Who made it, for the owner and the ADR.


@dataclass(frozen=True, slots=True)
class Runtime:
    """llama.cpp's release archive for one platform."""

    archive: PinnedFile
    version: str
    executable: str  # llama-server's path inside the archive, with "/" separators.
    unpacked_size: int  # All its files once unpacked.


@dataclass(frozen=True, slots=True)
class Model:
    """The model file and the name analyses record for it."""

    file: PinnedFile
    name: str


@dataclass(frozen=True, slots=True)
class Manifest:
    """Everything setup downloads on one platform, and the only hosts it may reach."""

    runtime: Runtime
    model: Model
    hosts: frozenset[str]


_LLAMA_CPP: Final = "llama.cpp release b12015 by the ggml authors (commit a34de86)"
_RELEASES: Final = "https://github.com/ggml-org/llama.cpp/releases/download/b12015/"
# Each pinned URL redirects to a signed download address that expires, so only these first
# addresses are pinned, and the hosts the redirects reach are listed; ".hf.co" is any host in
# Hugging Face's CDN domain, which varies with where the request comes from.
HOSTS: Final = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "huggingface.co",
        ".hf.co",
    }
)

MODEL: Final = Model(
    file=PinnedFile(
        name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        url=(
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/"
            "a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        ),
        size=2_497_281_120,
        sha256="3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        license="Apache-2.0",
        source="Qwen3-4B-Instruct-2507 by Qwen, quantized to Q4_K_M by Unsloth",
    ),
    name="Qwen3-4B-Instruct-2507-Q4_K_M",
)

WINDOWS_X64: Final = Manifest(
    runtime=Runtime(
        archive=PinnedFile(
            name="llama-b12015-bin-win-vulkan-x64.zip",
            url=_RELEASES + "llama-b12015-bin-win-vulkan-x64.zip",
            size=37_515_234,
            sha256="20129400daa3d8216a1985abf942d993ba732419053540cca1ad6487bfd8ebe6",
            license="MIT",
            source=f"{_LLAMA_CPP}, Vulkan build for Windows x64",
        ),
        version="b12015",
        executable="llama-server.exe",
        unpacked_size=105_494_813,
    ),
    model=MODEL,
    hosts=HOSTS,
)

MACOS_ARM64: Final = Manifest(
    runtime=Runtime(
        archive=PinnedFile(
            name="llama-b12015-bin-macos-arm64.tar.gz",
            url=_RELEASES + "llama-b12015-bin-macos-arm64.tar.gz",
            size=16_509_291,
            sha256="f43344dcede6ec565a0956e25dee199f21566768947092ca468a5671d154620c",
            license="MIT",
            source=f"{_LLAMA_CPP}, Metal build for macOS on Apple silicon",
        ),
        version="b12015",
        executable="llama-b12015/llama-server",
        unpacked_size=41_796_096,
    ),
    model=MODEL,
    hosts=HOSTS,
)


def manifest_for(system: str = sys.platform, machine: str = platform.machine()) -> Manifest | None:
    """This computer's manifest; None where local analysis isn't supported: other systems,
    Windows on ARM and Intel Macs, which P0-B1 didn't measure."""
    if system == "win32" and machine.lower() in ("amd64", "x86_64"):
        return WINDOWS_X64
    if system == "darwin" and machine == "arm64":
        return MACOS_ARM64
    return None
