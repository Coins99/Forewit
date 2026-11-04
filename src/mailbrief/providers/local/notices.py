"""Bundled upstream notices, available offline before a local AI download."""

from pathlib import Path


def local_notices() -> str:
    folder = Path(__file__).with_name("licenses")
    parts = [
        "llama.cpp b12015 — MIT; Copyright (c) 2023-2026 The ggml authors.\n"
        "https://github.com/ggml-org/llama.cpp/blob/b12015/LICENSE\n\n"
        "Qwen3-4B-Instruct-2507 — Apache-2.0; Copyright 2024 Alibaba Cloud.\n"
        "Q4_K_M quantization by Unsloth; original model by Qwen.\n"
        "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/main/LICENSE\n"
        "Runtime and model are downloaded separately on request, unchanged by Forewit."
    ]
    for name in ("llama-cpp-MIT.txt", "Qwen-Apache-2.0.txt"):
        parts.extend((name, (folder / name).read_text(encoding="utf-8")))
    return "\n\n".join(parts)
