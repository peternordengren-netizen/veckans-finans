"""Tester för scripts/signals.py och validate_signals (påhittade bolag och kurser)."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import signals  # noqa: E402
from signals import THRESHOLDS, Bar, build, tone_of  # noqa: E402
from validate import Instrument, validate_signals  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=TZ)
WEEK = "2026-W42"  # 12–18 okt
START = date(2026, 10, 12)

INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", sektor="industri"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm"),
    "GAMA": Instrument("Gammagruppen", (), "GAMA", "GAMA.ST", "Stockholm"),
    "DKKX": Instrument("Danskbolaget", (), "DKKX", "DKKX.CO", "Köpenhamn"),
    "SAKN": Instrument("Saknasbolaget", (), "SAKN", "SAKN.ST", "Stockholm"),
}


def series(closes_before: list[float], closes_week: list[float], vol_before=1000.0, vol_week=1000.0,
           high=None, low=None) -> list[Bar]:
    """Handelsdagar före veckan (bakåt från fredag 9 okt) följt av veckans dagar (måndag 12 okt framåt)."""
    days_before, d = [], START - timedelta(days=1)
    while len(days_before) < len(closes_before):
        if d.weekday() < 5:
            days_before.append(d)
        d -= timedelta(days=1)
    days_before.reverse()
    bars = [Bar(day, c, c, high or c, low or c, vol_before) for day, c in zip(days_before, closes_before)]
    bars += [Bar(START + timedelta(days=i), c, c, high or c, low or c, vol_week) for i, c in enumerate(closes_week)]
    return bars


def fake_fetch(_tickers, _start, _end):
    flat = [100.0] * 30
    return {
        "^OMXSPI": series(flat, [101.0] * 5),  # index +1 %
        # Alfaverken: +10 %, tredubblad omsättning, stänger på 52-veckorshögsta.
        "ALFA.ST": series(flat, [110.0] * 5, vol_week=3000.0),
        # Betabolaget: −5 % och nära 52-veckorslägsta (lägsta 94).
        "BETA.ST": series(flat, [95.0] * 5, low=None) + [],
        # Gammagruppen: oförändrad.
        "GAMA.ST": series(flat, [100.0] * 5, high=120.0, low=80.0),
        # Danskbolaget: +3 %, ingen jämförelse mot index (inte SEK).
        "DKKX.CO": series(flat, [103.0] * 5, high=150.0, low=50.0),
    }


WEEK_DATA = {
    "sectors": [{"id": "industri", "items": [
        {"sentiment": {"label": "negativ"}, "companies": [
            {"match": "matchad", "ticker": "ALFA", "impact": "direkt"},
            {"match": "matchad", "ticker": "GAMA", "impact": "indirekt"},
        ]},
    ]}],
}
ANALYSIS = {
    "short_positions": {"available": False, "changes": []},
    "short_aggregate": {"available": True, "changes": [
        {"ticker": "GAMA", "change_pp": 0.6}, {"ticker": "BETA", "change_pp": 0.2},
    ]},
    "insider": {"companies": [
        {"ticker": "BETA", "net_value_sek": 500000, "buyers": ["A", "B"], "sellers": []},
    ]},
}


def by_ticker(data):
    return {c["ticker"]: c for c in data["companies"]}


def test_varden_och_signaler():
    data = build(WEEK, INSTRUMENTS, WEEK_DATA, ANALYSIS, fake_fetch, date(2026, 10, 19), NOW)
    c = by_ticker(data)
    assert data["index"]["change_pct"] == 1.0
    alfa = c["ALFA"]
    assert (alfa["change_pct"], alfa["relative_pp"], alfa["volume_ratio"]) == (10.0, 9.0, 3.0)
    assert alfa["from_high_pct"] == 0.0 and alfa["tone"] == "negativ" and alfa["news"] == 1
    # Negativ ton men bättre än index -> avviker; +9 mot index -> stor rörelse; omsättning; 52v-högsta.
    assert alfa["signals"] == ["avviker", "stor-rörelse", "omsättning", "52v-högsta"]
    beta = c["BETA"]
    # −6 mot index: stor rörelse även nedåt.
    assert beta["relative_pp"] == -6.0 and "stor-rörelse" in beta["signals"]
    assert "insyn" in beta["signals"] and "52v-lägsta" in beta["signals"]
    gama = c["GAMA"]
    # Blankningen i analysfilen (äldre form) läses inte: ingen signal, inga blankningsfält (sedan 2026-11-11).
    assert gama["signals"] == [] and gama["tone"] is None  # indirekt berörd ger ingen ton
    assert "short_aggregate_pp" not in gama and "short_as_of" not in data
    assert "short_aggregate_pp" not in data["thresholds"]
    dkkx = c["DKKX"]
    assert dkkx["change_pct"] == 3.0 and dkkx["relative_pp"] is None and dkkx["signals"] == []
    assert c["SAKN"]["change_pct"] is None and data["errors"] == ["SAKN.ST: ingen kursdata för veckan"]
    assert validate_signals(data, WEEK, INSTRUMENTS).errors == []


def test_utan_vecko_och_analysfil():
    data = build(WEEK, INSTRUMENTS, None, None, fake_fetch, date(2026, 10, 19), NOW)
    c = by_ticker(data)
    assert c["ALFA"]["tone"] is None and c["ALFA"]["signals"] == ["stor-rörelse", "omsättning", "52v-högsta"]
    assert validate_signals(data, WEEK, INSTRUMENTS).errors == []


def test_stor_rorelse_galler_bara_over_gransen():
    from signals import signals_for

    base = {"insider_net_sek": None, "short_positions_pp": None, "short_aggregate_pp": None, "tone": None,
            "volume_ratio": None, "from_high_pct": None, "from_low_pct": None}
    assert signals_for({**base, "relative_pp": 5.0}, THRESHOLDS) == []  # exakt 5 räknas inte ("mer än")
    assert signals_for({**base, "relative_pp": 5.01}, THRESHOLDS) == ["stor-rörelse"]
    assert signals_for({**base, "relative_pp": -5.2}, THRESHOLDS) == ["stor-rörelse"]
    assert signals_for({**base, "relative_pp": None}, THRESHOLDS) == []


def test_sammanvagd_ton():
    assert tone_of([]) is None
    assert tone_of(["neutral", "positiv"]) == "positiv"
    assert tone_of(["positiv", "negativ"]) == "blandad"
    assert tone_of(["neutral"]) == "neutral"


def test_validering_raknar_om_signalerna():
    data = build(WEEK, INSTRUMENTS, WEEK_DATA, ANALYSIS, fake_fetch, date(2026, 10, 19), NOW)
    by_ticker(data)["GAMA"]["signals"] = ["insyn", "omsättning"]
    errors = validate_signals(data, WEEK, INSTRUMENTS).errors
    assert any("Gammagruppen" in e and "stämmer inte med värdena" in e for e in errors)


def test_validering_underkanner_okant_bolag_och_fel_index():
    data = build(WEEK, INSTRUMENTS, WEEK_DATA, ANALYSIS, fake_fetch, date(2026, 10, 19), NOW)
    alfa = by_ticker(data)["ALFA"]
    alfa["relative_pp"] = 5.0
    data["companies"].append({**by_ticker(data)["GAMA"], "ticker": "OKAND"})
    errors = validate_signals(data, WEEK, INSTRUMENTS).errors
    assert any("relative_pp" in e for e in errors)
    assert any("instruments.csv" in e for e in errors)


def test_trosklarna_sparas_i_filen():
    data = build(WEEK, INSTRUMENTS, None, None, fake_fetch, date(2026, 10, 19), NOW)
    assert data["thresholds"] == THRESHOLDS


def test_pe_upp_fran_kurs_mot_vinst():
    from signals import signals_for

    base = {"insider_net_sek": None, "short_positions_pp": None, "short_aggregate_pp": None, "tone": None,
            "volume_ratio": None, "from_high_pct": None, "from_low_pct": None, "relative_pp": None}
    assert signals_for({**base, "pe_change_pct": 30.0}, THRESHOLDS) == ["pe-upp"]
    assert signals_for({**base, "pe_change_pct": 29.9}, THRESHOLDS) == []
    assert signals_for({**base, "pe_change_pct": None}, THRESHOLDS) == []
    valuation = {"companies": [{"ticker": "GAMA", "pe_change_pct": 45.0}, {"ticker": "ALFA", "pe_change_pct": -5.0}]}
    data = build(WEEK, INSTRUMENTS, None, None, fake_fetch, date(2026, 10, 19), NOW, valuation)
    c = by_ticker(data)
    assert "pe-upp" in c["GAMA"]["signals"] and "pe-upp" not in c["ALFA"]["signals"]
    assert c["BETA"]["pe_change_pct"] is None
    assert validate_signals(data, WEEK, INSTRUMENTS).errors == []


def test_blankning_bara_i_aldre_filer():
    """Nya filer har ingen blankning (sedan 2026-11-11). Äldre filer räknas som förut med sina egna trösklar:
    sammanlagd blankning ändrad minst 0,5 procentenheter, och utan short_only_aggregate också en ändrad position."""
    from signals import signals_for

    base = {"insider_net_sek": None, "short_positions_pp": None, "short_aggregate_pp": None, "tone": None,
            "volume_ratio": None, "from_high_pct": None, "from_low_pct": None, "relative_pp": None}
    assert "short_aggregate_pp" not in THRESHOLDS and "short_only_aggregate" not in THRESHOLDS
    assert signals_for({**base, "short_aggregate_pp": -0.6}, THRESHOLDS) == []  # nya filer: aldrig
    newer = {**THRESHOLDS, "short_aggregate_pp": 0.5, "short_only_aggregate": True}  # filer 2026-10-29 – 11-10
    assert signals_for({**base, "short_positions_pp": 0.3, "short_aggregate_pp": 0.2}, newer) == []
    assert signals_for({**base, "short_positions_pp": 0.3, "short_aggregate_pp": 0.5}, newer) == ["blankning"]
    assert signals_for({**base, "short_aggregate_pp": -0.6}, newer) == ["blankning"]
    old = {k: v for k, v in newer.items() if k != "short_only_aggregate"}
    assert signals_for({**base, "short_positions_pp": 0.3, "short_aggregate_pp": 0.2}, old) == ["blankning"]


def test_saknad_stangning_hamtas_ur_timdatan(monkeypatch):
    """Yahoo har en dagsrad med high och low men utan stängning (2026-11-10): stängningen hämtas ur timdatan (sista
    timstapeln samma dag), med ett extra batchanrop bara för de bolagen. Utan high/low hoppas raden över som förut."""
    import types

    import pandas as pd

    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    nan = float("nan")

    def frame(index, rows):
        cols = pd.MultiIndex.from_tuples([(t, f) for t in rows for f in fields])
        data = [[v for t in rows for v in rows[t][i]] for i in range(len(index))]
        return pd.DataFrame(data, index=index, columns=cols)

    days = pd.DatetimeIndex(["2026-11-06", "2026-11-09"])
    daily = frame(days, {
        "ALFA.ST": [[97.6, 98.1, 96.7, 97.75, 97.75, 1000], [97.55, 99.4, 97.2, nan, nan, 900]],
        "BETA.ST": [[50.0, 51.0, 49.0, 50.5, 50.5, 100], [50.5, 52.0, 50.0, 51.0, 51.0, 120]],
        "GAMMA.ST": [[10.0, 11.0, 9.0, 10.5, 10.5, 10], [nan, nan, nan, nan, nan, nan]],
    })
    hours = pd.DatetimeIndex(["2026-11-09 15:00", "2026-11-09 16:00", "2026-11-09 17:00"]).tz_localize("Europe/Stockholm")
    hourly = frame(hours, {"ALFA.ST": [[98.75, 98.8, 97.9, 98.05, 98.05, 1], [98.05, 98.15, 97.75, 98.05, 98.05, 1],
                                       [98.0, 98.2, 97.8, 98.2, 98.2, 1]]})
    calls = []

    def download(tickers, **kw):
        calls.append((list(tickers), kw["interval"]))
        return hourly if kw["interval"] == "60m" else daily

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))
    bars = signals._download_bars(["ALFA.ST", "BETA.ST", "GAMMA.ST"], date(2026, 11, 2), date(2026, 11, 9))
    assert calls == [(["ALFA.ST", "BETA.ST", "GAMMA.ST"], "1d"), (["ALFA.ST"], "60m")]
    a = bars["ALFA.ST"][-1]
    assert a.day == date(2026, 11, 9) and a.close == 98.2 and a.adj_close == 98.2 and a.high == 99.4 and a.volume == 900
    assert bars["BETA.ST"][-1].close == 51.0
    assert [b.day for b in bars["GAMMA.ST"]] == [date(2026, 11, 6)]  # tom rad: hoppas över
