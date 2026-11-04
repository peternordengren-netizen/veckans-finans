"""Tester för fliken Swing: scripts/swing.py, scripts/swing_history.py och valideringen (påhittade kurser)."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import swing  # noqa: E402
import swing_history  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument, validate_swing  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", sektor="industri"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", sektor="fastigheter"),
}


def make_bars(closes, volumes=None, start=date(2026, 6, 1)):
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    vols = volumes or [1000.0] * len(closes)
    return [Bar(day, c, c, c * 1.01, c * 0.99, v) for day, c, v in zip(days, closes, vols)]


def test_rsi_och_glidande_medel():
    up = [float(100 + i) for i in range(60)]
    m = swing.series(make_bars(up))[-1]
    assert m["rsi2"] == 100.0 and m["rsi14"] == 100.0  # bara uppgångar
    assert m["ma20_pct"] > 0 and m["ma50_pct"] > 0 and m["ma20_over_ma50"]
    assert swing.series(make_bars(up))[49] is None  # för kort historik
    down = [float(200 - i) for i in range(60)]
    m = swing.series(make_bars(down))[-1]
    assert m["rsi2"] == 0.0 and m["ma20_pct"] < 0 and not m["ma20_over_ma50"]
    assert swing.technical_labels(m) == ["rsi2-under-10", "trend-ned"]


def test_utbrott_med_och_utan_omsattning():
    closes = [100.0] * 59 + [110.0]
    vols = [1000.0] * 59 + [3000.0]
    m = swing.series(make_bars(closes, vols))[-1]
    assert m["above_high20"] and m["volume_ratio"] == 3.0
    assert "utbrott-omsattning" in swing.technical_labels(m)
    m = swing.series(make_bars(closes))[-1]
    assert "utbrott" in swing.technical_labels(m) and "utbrott-omsattning" not in swing.technical_labels(m)


def test_rapportetiketter():
    assert swing.report_label(0, "över") == "efter-rapport-over"
    assert swing.report_label(4, "under") == "efter-rapport-under"
    assert swing.report_label(2, None) == "efter-rapport"
    assert swing.report_label(5, "över") is None  # utanför fem handelsdagar
    assert swing.report_label(None, None) is None
    # Före 17:30 reagerar samma dag, efter 17:30 nästa dag.
    assert swing.reaction_day(datetime(2026, 10, 23, 7, 30, tzinfo=TZ)) == date(2026, 10, 23)
    assert swing.reaction_day(datetime(2026, 10, 23, 17, 40, tzinfo=TZ)) == date(2026, 10, 24)


def test_build_och_validering():
    closes = [100.0 + i * 0.5 for i in range(70)]
    bars = {"ALFA.ST": make_bars(closes), "BETA.ST": make_bars([100.0] * 20)}  # BETA: för kort historik
    last = bars["ALFA.ST"][-1].day
    today = last + timedelta(days=1)
    # Rapport på fredagskvällen efter stängning -> reaktion nästa handelsdag; här på sista dagen.
    reports = {"ALFA": {"published": datetime.combine(last, datetime.min.time(), TZ).replace(hour=7).isoformat(),
                        "verdict": "över"}}
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, bars, reports, ("2026-10-27", {"ALFA": 1.25}))
    assert [c["ticker"] for c in data["companies"]] == ["ALFA"]
    a = data["companies"][0]
    assert a["days_since_report"] == 0 and a["short_pct"] == 1.25
    assert "efter-rapport-over" in a["labels"] and "trend-upp" in a["labels"]
    assert validate_swing(data, INSTRUMENTS).errors == []
    a["labels"] = ["utbrott"]
    assert any("stämmer inte" in e for e in validate_swing(data, INSTRUMENTS).errors)
    a["labels"] = swing.technical_labels(a) + ["efter-rapport-over"]
    data["labels"]["utbrott"] = "Köpläge"
    assert any("uppmaning" in e for e in validate_swing(data, INSTRUMENTS).errors)


def test_historik_per_etikett(tmp_path):
    # Index platt; ALFA stiger jämnt (trend upp, bättre än index), BETA faller (trend ned, sämre).
    n = 120
    bars = {
        "^OMXSPI": make_bars([1000.0] * n),
        "ALFA.ST": make_bars([100.0 + i for i in range(n)]),
        "BETA.ST": make_bars([300.0 - i for i in range(n)]),
    }
    hist = swing_history.build(bars, INSTRUMENTS, date(2026, 12, 31), results_dir=tmp_path)
    assert hist["base"]["5"]["n"] > 0 and abs(hist["base"]["5"]["better"] - 0.5) < 0.01  # hälften upp, hälften ned
    assert hist["labels"]["trend-upp"]["5"]["better"] == 1.0
    assert hist["labels"]["trend-ned"]["5"]["better"] == 0.0
    assert hist["labels"]["efter-rapport-over"]["5"]["n"] == 0


# --- Pullback i trend -----------------------------------------------------------------------


import pullback  # noqa: E402


def candle(o, h, lo, c, prev_o=None, prev_c=None):
    return {"open": o, "high": h, "low": lo, "close": c, "prev_open": prev_o if prev_o is not None else o,
            "prev_close": prev_c if prev_c is not None else o, "ema20": 0.0, "ma50": 0.0, "ma200": 0.0}


def test_hammer_och_bullish_engulfing():
    # Hammer: lång nedre skugga (minst 2 × kroppen), liten övre skugga (högst 25 % av spannet).
    assert pullback.is_hammer(candle(100, 101, 94, 100.8))
    assert not pullback.is_hammer(candle(100, 104, 94, 100.8))  # övre skugga för lång
    assert not pullback.is_hammer(candle(100, 101, 99, 100.8))  # nedre skugga för kort
    # Engulfing: röd dag följd av grön dag vars kropp omsluter den röda.
    assert pullback.is_engulfing(candle(98, 103, 97.5, 102.5, prev_o=101, prev_c=98.5))
    assert not pullback.is_engulfing(candle(99, 103, 98.5, 100.5, prev_o=101, prev_c=98.5))  # omsluter inte
    assert not pullback.is_engulfing(candle(98, 103, 97.5, 102.5, prev_o=98, prev_c=99))  # förra dagen grön


def test_trend_rekyl_och_etiketter():
    base = {"open": 101.0, "high": 105.2, "low": 100.5, "close": 105.0, "prev_open": 104.0, "prev_close": 104.2,
            "ema20": 100.0, "ma50": 95.0, "ma200": 90.0}
    assert pullback.touched(base) == ("ema20", 100.0)  # low 0,5 % från 20 EMA, stängning över
    assert pullback.labels(base) == ["rekyl-i-trend"]
    assert pullback.labels({**base, "ma50": 89.0}) == []  # 50 MA under 200 MA: ingen trend
    assert pullback.labels({**base, "low": 102.0}) == []  # low 2 % från nivån: ingen rekyl
    assert pullback.labels({**base, "close": 99.5, "low": 99.4, "open": 99.6, "high": 100.2}) == []  # stängning under
    hammer = {**base, "open": 103.9, "high": 104.3, "low": 100.2, "close": 104.2}
    assert pullback.signal(hammer) == "hammer" and pullback.labels(hammer) == ["rekyl-vandsignal"]
    # Båda nivåerna nådda: den lägre (50 MA) gäller.
    assert pullback.touched({**base, "ema20": 100.6, "ma50": 100.0, "low": 100.3, "close": 101.0})[0] == "ma50"
    assert pullback.labels(None) == []


def test_ema():
    e = pullback.ema([1.0, 2.0, 3.0, 4.0], 2)
    assert e[0] is None and e[1] == 1.5 and abs(e[2] - (3 * 2 / 3 + 1.5 / 3)) < 1e-12


def test_stopp_niva_och_r():
    #        dagar:  0    1    2    3    4    5    6    7    8    9
    h = [100, 104, 108, 110, 107, 105, 104, 103, 102, 103.0]
    lo = [98, 101, 105, 106, 103, 101, 100.5, 99.8, 99.6, 100.2]
    # Toppen (högsta high de 10 dagarna) dag 3; lägsta low därefter 99,6.
    assert pullback.stop_level(h, lo, 9, ma_level=100.0) == 99.6
    assert pullback.stop_level(h, lo, 9, ma_level=99.0) == 99.0  # MA-nivån lägre
    st = {"open": 100.4, "high": 103.0, "low": 100.2, "close": 102.5, "prev_open": 101, "prev_close": 102,
          "ema20": 100.0, "ma50": 95.0, "ma200": 90.0}
    lv = pullback.levels(h, lo, 2.0, 9, st)
    assert lv["signal"] is None and lv["ref"] == 102.5 and lv["stop"] == 99.6
    assert lv["risk_pct"] == round(2.9 / 102.5 * 100, 2) and lv["risk_atr"] == 1.45
    assert lv["r2"] == round(102.5 + 5.8, 4) and lv["r3"] == round(102.5 + 8.7, 4) and lv["top"] == 110
    hammer = {**st, "open": 102.6, "high": 103.0, "low": 100.2, "close": 102.9}
    lv = pullback.levels(h, lo, 2.0, 9, hammer)
    assert lv["signal"] == "hammer" and lv["trigger"] == 103.0 and lv["ref"] == 103.0


def test_simulering_2r_stopp_och_ej_utlost():
    st = {"open": 100, "high": 101, "low": 99.5, "close": 100.5, "prev_open": 100, "prev_close": 100,
          "ema20": 99.6, "ma50": 95.0, "ma200": 90.0}
    # Rekyl i trend: start på nästa dags öppning 100, stopp 98 -> R = 2, 2R = 104.
    o = [100, 100, 101, 103]
    h = [101, 101.5, 102, 104.5]
    lo = [99.5, 99.5, 100.5, 102.5]
    case = pullback.simulate(o, h, lo, 0, st, stop=98.0)
    assert case["outcome"] == "2R" and case["end"] == 3
    assert case["net_pct"] == round((104 / 100 - 1) * 100, 3) and case["risk_pct"] == 2.0
    # Stoppet först; nås båda samma dag räknas stoppet.
    case = pullback.simulate([100, 100, 99], [101, 101, 104.5], [99.5, 99.5, 97.0], 0, st, stop=98.0)
    assert case["outcome"] == "stopp" and case["net_pct"] == round((98 / 100 - 1) * 100, 3)
    # Öppnar under stoppet efter start: öppningen gäller.
    case = pullback.simulate([100, 100, 96], [101, 101, 97], [99.5, 99.5, 95.5], 0, st, stop=98.0)
    assert case["net_pct"] == round((96 / 100 - 1) * 100, 3)
    # Vändsignal: triggernivån (gårdagens high 101) nås inte inom 3 dagar -> ej utlöst.
    hammer = {**st, "open": 100.6, "high": 101.0, "low": 99.6, "close": 100.9}
    case = pullback.simulate([100, 100.5, 100.4, 100.3, 100.2], [101, 100.9, 100.8, 100.7, 102],
                             [99.6, 100, 100, 100, 100], 0, hammer, stop=99.0)
    assert case["outcome"] == "ej utlöst"
    s = pullback.summarize([{"outcome": "2R", "net_pct": 3.6, "risk_pct": 2.0},
                            {"outcome": "stopp", "net_pct": -2.4, "risk_pct": 2.0},
                            {"outcome": "öppet", "net_pct": None, "risk_pct": None}])
    assert (s["cases"], s["decided"], s["r2_share"], s["open"]) == (3, 2, 0.5, 1)
    assert s["mean_net_pct"] == 0.6 and s["mean_net_r"] == 0.3


def make_ohlc_trend_with_pullback():
    """245 dagar jämn uppgång, sedan en rekyl ned mot 20 EMA och en hammer sista dagen."""
    closes = [100.0 + i * 0.4 for i in range(245)]
    bars = [Bar(b.day, b.close, b.adj_close, b.close + 0.3, b.close - 0.3, 1000.0, b.close - 0.1)
            for b in make_bars(closes)]
    d = bars[-1].day
    for x in (198.0, 196.5, 195.5, 195.0):
        d += timedelta(days=1 if d.weekday() < 4 else 3)
        bars.append(Bar(d, x, x, x + 0.4, x - 0.4, 1000.0, x + 0.2))
    o, h, lo, c = pullback.adjusted(bars)
    e20 = pullback.ema(c + [0.0], 20)[-2]  # ungefärlig nivå inför sista dagen
    d += timedelta(days=1 if d.weekday() < 4 else 3)
    # Hammer vars low når nära 20 EMA och som stänger över den.
    bars.append(Bar(d, e20 + 1.5, e20 + 1.5, e20 + 1.7, e20 - 0.3, 1000.0, e20 + 1.2))
    return bars


def test_swing_bygger_och_validerar_pullback():
    bars = {"ALFA.ST": make_ohlc_trend_with_pullback(), "BETA.ST": make_bars([100.0] * 60)}
    today = bars["ALFA.ST"][-1].day + timedelta(days=1)
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, bars, {}, (None, {}))
    a = next(c for c in data["companies"] if c["ticker"] == "ALFA")
    assert "rekyl-vandsignal" in a["labels"], a["pullback"]
    lv = a["pullback"]["levels"]
    assert lv["signal"] == "hammer" and lv["trigger"] == a["pullback"]["state"]["high"]
    assert lv["stop"] < lv["ref"] < lv["r2"] < lv["r3"]
    b = next(c for c in data["companies"] if c["ticker"] == "BETA")
    assert b["pullback"] == {"state": None, "levels": None}  # under 200 dagar
    assert data["strategies"]["pullback"]["labels"] == ["rekyl-i-trend", "rekyl-vandsignal"]
    assert validate_swing(data, INSTRUMENTS).errors == []
    lv["r2"] += 1
    assert any("r2" in e for e in validate_swing(data, INSTRUMENTS).errors)
    lv["r2"] -= 1
    a["labels"].remove("rekyl-vandsignal")
    assert any("stämmer inte" in e for e in validate_swing(data, INSTRUMENTS).errors)
    data["strategies"]["pullback"]["name"] = "Köpläge"
    assert any("uppmaning" in e for e in validate_swing(data, INSTRUMENTS).errors)


def test_historiken_har_pullback():
    n = 500
    bars = {"^OMXSPI": make_bars([1000.0] * n), "ALFA.ST": make_bars([100.0 + i for i in range(n)]),
            "BETA.ST": make_bars([600.0 - i for i in range(n)])}
    hist = swing_history.build(bars, INSTRUMENTS, date(2028, 1, 1))
    pb = hist["pullback"]
    assert set(pb["labels"]) == {"rekyl-i-trend", "rekyl-vandsignal"}
    assert "fee_pct" not in pb["thresholds"] and pb["all"]["cases"] >= 0
    assert set(hist["labels"]) >= {"rekyl-i-trend", "rekyl-vandsignal"}


def test_marknadsfilter():
    """Indexet över sitt 50- och 200-dagars MA: stigande index uppfyller filtret, fallande inte; dagar utan 200
    dagars historik saknas."""
    up = swing_history.market_filter(make_bars([1000.0 + i for i in range(260)]))
    down = swing_history.market_filter(make_bars([2000.0 - i for i in range(260)]))
    assert len(up) == 61 and all(up.values())
    assert not any(down.values())
    # Med fallande index blir inga fall kvar med filtret; utan filter finns de som förut.
    n = 500
    bars = {"^OMXSPI": make_bars([3000.0 - i for i in range(n)]), "ALFA.ST": make_ohlc_trend_with_pullback(),
            "BETA.ST": make_bars([600.0 - i for i in range(n)])}
    hist = swing_history.build(bars, INSTRUMENTS, date(2028, 1, 1))
    mf = hist["pullback"]["market_filter"]
    assert mf["all"]["cases"] == 0 and mf["days_up"] == 0
    assert hist["pullback"]["all"]["cases"] >= mf["all"]["cases"]
