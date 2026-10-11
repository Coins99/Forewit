"""Notice export must never replace an owner file or open an error dialog."""

from pathlib import Path

import pytest

from mailbrief import app


def bundled_notices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "THIRD_PARTY_NOTICES.txt").write_text("Synthetic notices", encoding="utf-8")
    monkeypatch.setattr(app, "__file__", str(tmp_path / "app.py"))
    return tmp_path / "export.txt"


def test_export_bundled_notices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = bundled_notices(tmp_path, monkeypatch)
    assert app.main(["--export-notices", str(destination)]) == 0
    assert destination.read_text("utf-8") == "Synthetic notices"


@pytest.mark.parametrize("existing", [False, True])
def test_notice_export_failure_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    destination = bundled_notices(tmp_path, monkeypatch)
    if existing:
        destination.write_text("Owner writing", encoding="utf-8")
    else:
        destination = tmp_path / "missing-folder" / "export.txt"
    with pytest.raises(SystemExit) as failure:
        app.main(["--export-notices", str(destination)])
    assert failure.value.code == 2
    if existing:
        assert destination.read_text("utf-8") == "Owner writing"
    else:
        assert not destination.exists()
