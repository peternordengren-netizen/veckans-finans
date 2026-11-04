"""Tester för spärren i scripts/run_morning.ps1: före 08:10 körs inget morgonflöde.

Inloggnings- och upplåsningsutlösarna kör samma åtgärd som utlösaren 08:10, så spärren
testas via -Now. Skriptet kopieras till en tom katalog, så att inget i projektet berörs.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell-skript för Windows")


def run_morning(tmp_path: Path, now: str) -> tuple[int, str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts" / "run_morning.ps1", scripts)
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(scripts / "run_morning.ps1"), "-Now", now],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return p.returncode, p.stdout


@pytest.mark.parametrize("now", ["2026-10-21 06:57", "2026-10-21 07:59", "2026-10-21 08:08:59"])
def test_fore_0810_gors_ingenting(tmp_path, now):
    code, out = run_morning(tmp_path, now)
    assert code == 0
    assert "före 08:10" in out
    assert not (tmp_path / "logs").exists()  # inget steg har startat, ingen logg skapad


@pytest.mark.parametrize("now", ["2026-10-22 07:00", "2026-10-22 17:43"])
def test_schemalagd_eftermiddag_fore_1745_gor_ingenting(tmp_path, now):
    """run_update.ps1 -Scheduled: en körning som tas igen nästa morgon (eller för tidigt) gör ingenting."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts" / "run_update.ps1", scripts)
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(scripts / "run_update.ps1"), "-Scheduled", "-Now", now],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert p.returncode == 0
    assert "före 17:45" in p.stdout
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("now", ["2026-10-21 08:10", "2026-10-21 13:20"])
def test_fran_0810_startar_flodet(tmp_path, now):
    code, out = run_morning(tmp_path, now)
    assert code == 0
    assert "före 08:10" not in out
    log = next((tmp_path / "logs" / "morning").glob("*.log")).read_text(encoding="utf-8-sig")
    assert "Startar morning.py" in log  # i den tomma katalogen finns ingen morning.py, så den fallerar


@pytest.mark.parametrize("now", ["2026-11-04 08:30", "2026-11-04 18:00"])
def test_bollinger_utanfor_handelstid_gor_ingenting(tmp_path, now):
    """run_bollinger.ps1 -Scheduled: en körning som tas igen utanför 09:00-17:30 gör ingenting."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts" / "run_bollinger.ps1", scripts)
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(scripts / "run_bollinger.ps1"), "-Scheduled", "-Now", now],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert p.returncode == 0
    assert "utanför handelstid" in p.stdout
    assert not (tmp_path / "logs").exists()
