"""Tester för Bollingerkontrollen (scripts/bollinger.py) och etiketten i Swing (påhittade kurser)."""

import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bollinger  # noqa: E402
import swing  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
TODAY = date(2026, 11, 4)  # onsdag
NOW = datetime(2026, 11, 4, 9, 20, tzinfo=TZ)
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm"),
    "GAMMA": Instrument("Gammagruppen", (), "GAMMA", "GAMMA.ST", "Stockholm"),
    "DELTA": Instrument("Deltahem", (), "DELTA", "DELTA.ST", "Stockholm"),
}


def days_until(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def series(closes, end=TODAY):
    return [Bar(d, c, c, c, c, 1000.0) for d, c in zip(days_until(end, len(closes)), closes)]


BASE = [100.0, 102.0] * 10  # 20 dagar som pendlar: snitt 101, standardavvikelse 1


def test_ovre_bandet():
    assert bollinger.upper_band([1.0] * 19) is None
    vals = [100.0 + (i % 3) for i in range(25)]
    last = vals[-20:]
    assert bollinger.upper_band(vals) == statistics.fmean(last) + 2 * statistics.pstdev(last)
    assert bollinger.status(105.0, 104.0) == "over" and bollinger.status(104.0, 104.0) == "over"
    assert bollinger.status(103.0, 104.0) == "near"  # 0,96 % under
    assert bollinger.status(102.9, 104.0) is None  # 1,06 % under


def test_kontrollen_delar_upp_bolagen():
    def fetch(tickers, start, end):
        assert end == TODAY and len(tickers) == 4  # ett batchanrop för alla
        return {
            "ALFA.ST": series(BASE[1:] + [106.0]),  # långt över bandet
            "BETA.ST": series(BASE[1:] + [103.2]),  # strax under bandet
            "GAMMA.ST": series(BASE[1:] + [101.0]),  # mitt i bandet
            "DELTA.ST": series(BASE, end=TODAY - timedelta(days=1)),  # ingen kurs i dag
        }

    data = bollinger.check(TODAY, NOW, INSTRUMENTS, fetch)
    assert [r["ticker"] for r in data["over"]] == ["ALFA"]
    assert [r["ticker"] for r in data["near"]] == ["BETA"]
    assert (data["checked"], data["missing"]) == (3, 1)
    assert "fördröjda ca 15 minuter" in data["delay_note"]
    assert bollinger.validate(data) == []
    data["near"].append(data["over"][0])
    assert bollinger.validate(data)


def test_ingen_handelsdag(capsys):
    saturday = datetime(2026, 11, 7, 9, 20, tzinfo=TZ)
    assert bollinger.main(["--handelsdag"], now=saturday) == 3
    assert bollinger.main(["--handelsdag"], now=NOW) == 0
    assert bollinger.main([], fetch=lambda *a: {}, now=saturday) == 3
    assert "ingen handelsdag" in capsys.readouterr().out


def test_etiketten_i_swing():
    closes = [100.0 + (i % 2) * 2 for i in range(59)] + [110.0]  # sista dagen långt över bandet
    m = swing.series([Bar(d, c, c, c * 1.01, c * 0.99, 1000.0) for d, c in zip(days_until(TODAY, 60), closes)])[-1]
    assert m["bb_upper_pct"] > 0 and "bollinger-ovre" in swing.technical_labels(m)
    m2 = {**m, "bb_upper_pct": -0.5}
    assert "bollinger-ovre" not in swing.technical_labels(m2)
    old = {k: v for k, v in m.items() if k != "bb_upper_pct"}  # äldre filer saknar måttet
    assert "bollinger-ovre" not in swing.technical_labels(old)
