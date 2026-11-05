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


def test_lage_mellan_banden_och_avstand_i_r():
    lower, upper = bollinger.bands(BASE)  # snitt 101, standardavvikelse 1
    assert (round(lower, 6), round(upper, 6)) == (99.0, 103.0)
    p = bollinger.position(102.0, lower, upper, ref=101.0, stop=100.0)  # R = 1
    assert p["pct_b"] == 0.75 and p["upper_r"] == 1.0 and p["at_upper"] is False
    p = bollinger.position(104.0, lower, upper, ref=101.0, stop=100.0)
    assert p["pct_b"] == 1.25 and p["upper_r"] == -1.0 and p["at_upper"] is True
    assert bollinger.position_errors(p, "x") == []
    assert bollinger.position_errors({**p, "at_upper": False}, "x")
    assert bollinger.position_errors({**p, "pct_b": 0.5}, "x")


def pb_company(ticker, name, labels, ref, stop):
    return {"ticker": ticker, "name": name, "labels": labels,
            "pullback": {"state": {}, "levels": {"ref": ref, "stop": stop} if labels else None}}


def test_kontrollen_raknar_pullback_kandidaterna():
    swing_file = {
        "date": TODAY.isoformat(),
        "strategies": {"pullback": {"name": "Pullback i trend", "labels": ["rekyl-i-trend", "rekyl-vandsignal"]}},
        "companies": [
            pb_company("ALFA", "Alfaverken", ["rekyl-vandsignal"], 101.0, 100.0),
            pb_company("BETA", "Betabolaget", ["rekyl-i-trend"], 101.0, 99.0),
            pb_company("GAMMA", "Gammagruppen", [], 0, 0),  # ingen kandidat
            pb_company("DELTA", "Deltahem", ["rekyl-i-trend"], 101.0, 100.0),  # ingen kurs i dag
        ],
    }

    def fetch(tickers, start, end):
        return {"ALFA.ST": series(BASE[1:] + [106.0]), "BETA.ST": series(BASE[1:] + [101.0]),
                "GAMMA.ST": series(BASE[1:] + [101.0]), "DELTA.ST": series(BASE, end=TODAY - timedelta(days=1))}

    data = bollinger.check(TODAY, NOW, INSTRUMENTS, fetch, swing_file)
    pb = data["pullback"]
    assert pb["swing_date"] == TODAY.isoformat() and pb["candidates"] == 3
    assert [(r["ticker"], r["at_upper"]) for r in pb["rows"]] == [("ALFA", True), ("BETA", False)]
    assert pb["rows"][0]["upper_r"] < 0 < pb["rows"][1]["upper_r"]
    assert bollinger.validate(data) == []
    pb["rows"][1]["at_upper"] = True
    assert bollinger.validate(data)
    # Utan swingfil: inget pullback-avsnitt.
    assert bollinger.check(TODAY, NOW, INSTRUMENTS, fetch)["pullback"] is None


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
