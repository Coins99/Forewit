"""Stage only a native app registration for packaging, never user credentials."""

import json
from pathlib import Path

from mailbrief.providers.gmail.oauth import DesktopClient


def stage_google_client(source: Path | None, contents: str, directory: Path) -> Path | None:
    if source is None and not contents:
        return None
    client = DesktopClient.load(source) if source is not None else DesktopClient.from_json(contents)
    staged = directory / "google-oauth.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": client.client_id,
                    "client_secret": client.client_secret,
                }
            }
        ),
        encoding="utf-8",
    )
    return staged
