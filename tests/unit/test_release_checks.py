"""Release gates fail on missing service coverage and accidentally bundled private files."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.check_coverage import SERVICES, check
from scripts.check_package import audit_distribution, stop_process


def test_service_coverage_counts_branches_and_requires_every_service(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    classes = [
        f'<class filename="services/{name}.py"><lines>'
        '<line hits="1" condition-coverage="100% (2/2)"/>'
        "</lines></class>"
        for name in SERVICES
    ]
    report.write_text("<coverage>" + "".join(classes) + "</coverage>", encoding="utf-8")
    assert check(report) == []
    report.write_text(
        "<coverage>" + "".join(classes).replace("100% (2/2)", "50% (1/2)", 1) + "</coverage>",
        encoding="utf-8",
    )
    # The first service in the list is the one made to fail.
    assert check(report) == [f"services/{SERVICES[0]}.py: 66.67% below 90%"]
    report.write_text("<coverage/>", encoding="utf-8")
    assert len(check(report)) == len(SERVICES)


def test_every_service_module_is_gated() -> None:
    services = Path(__file__).resolve().parents[2] / "src" / "mailbrief" / "services"
    modules = {path.stem for path in services.glob("*.py")} - {"__init__"}
    assert set(SERVICES) == modules
    assert len(SERVICES) == len(modules)
    assert {"analysis", "brief", "actions", "application"} <= set(SERVICES)


@pytest.mark.parametrize(
    "name",
    [
        "client_secret_example.json",
        "owner.sqlite3",
        "desktop.log",
        "token.json",
        ".env",
    ],
)
def test_distribution_audit_refuses_private_files(tmp_path: Path, name: str) -> None:
    (tmp_path / "Forewit.exe").write_bytes(b"synthetic binary")
    audit_distribution(tmp_path)
    (tmp_path / name).write_bytes(b"synthetic private marker")
    with pytest.raises(ValueError, match="private or development"):
        audit_distribution(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows bootloader cleanup")
@pytest.mark.parametrize(
    "failure", [OSError("Unavailable"), subprocess.TimeoutExpired("taskkill", 15)]
)
def test_failed_tree_cleanup_still_stops_owned_parent(tmp_path: Path, failure: Exception) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], text=True)
    try:
        with patch("scripts.check_package.subprocess.run", side_effect=failure):
            stop_process(process, tmp_path)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
