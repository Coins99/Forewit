"""The build bundles registration fields only; errors never echo credential input."""

import json
from pathlib import Path

import pytest

from mailbrief.providers.gmail.errors import GmailSetupError
from scripts.google_client import stage_google_client


def test_staging_strips_other_fields_and_explicit_source_wins(tmp_path: Path) -> None:
    payload = {
        "installed": {
            "client_id": "native.apps.googleusercontent.com",
            "client_secret": "synthetic",
            "refresh_token": "never-bundle",
            "token_uri": "https://untrusted.example",
        },
        "access_token": "never-bundle",
    }
    source = tmp_path / "download.json"
    source.write_text(json.dumps(payload))
    staged = stage_google_client(source, "invalid environment value", tmp_path / "resources")
    assert staged is not None
    assert json.loads(staged.read_text()) == {
        "installed": {
            "client_id": "native.apps.googleusercontent.com",
            "client_secret": "synthetic",
        }
    }
    assert stage_google_client(None, "", tmp_path / "resources") is None
    assert stage_google_client(None, json.dumps(payload), tmp_path / "resources") == staged


@pytest.mark.parametrize(
    "raw",
    ["bad-secret-input", '{"web": {}}', "x" * 64_001],
    ids=["invalid-json", "web-client", "oversized"],
)
def test_invalid_build_configuration_fails_without_echoing_input(tmp_path: Path, raw: str) -> None:
    with pytest.raises(GmailSetupError) as failure:
        stage_google_client(None, raw, tmp_path / "resources")
    assert raw not in str(failure.value)
    assert not (tmp_path / "resources" / "google-oauth.json").exists()
