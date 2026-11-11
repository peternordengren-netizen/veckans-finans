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


def make_bars(closes, volumes=None, start=date(2026, 6, 1), end=None):
    """Vardagar från start, eller (end) bakåt så att serien slutar på end."""
    days, d = [], end or start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=-1 if end else 1)
    days.sort()
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
    bars = {"ALFA.ST": make_bars(closes)}
    bars["BETA.ST"] = make_bars([100.0] * 20, end=bars["ALFA.ST"][-1].day)  # BETA: för kort historik
    last = bars["ALFA.ST"][-1].day
    today = last + timedelta(days=1)
    # Rapport på fredagskvällen efter stängning -> reaktion nästa handelsdag; här på sista dagen.
    reports = {"ALFA": {"published": datetime.combine(last, datetime.min.time(), TZ).replace(hour=7).isoformat(),
                        "verdict": "över"}}
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, bars, reports)
    assert [c["ticker"] for c in data["companies"]] == ["ALFA"]
    a = data["companies"][0]
    assert a["days_since_report"] == 0 and "short_pct" not in a  # blankning räknas inte sedan 2026-11-11
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
    bars = {"ALFA.ST": make_ohlc_trend_with_pullback()}
    bars["BETA.ST"] = make_bars([100.0] * 60, end=bars["ALFA.ST"][-1].day)
    today = bars["ALFA.ST"][-1].day + timedelta(days=1)
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, bars, {})
    a = next(c for c in data["companies"] if c["ticker"] == "ALFA")
    assert "rekyl-vandsignal" in a["labels"], a["pullback"]
    lv = a["pullback"]["levels"]
    assert lv["signal"] == "hammer" and lv["trigger"] == a["pullback"]["state"]["high"]
    assert lv["stop"] < lv["ref"] < lv["r2"] < lv["r3"]
    b = next(c for c in data["companies"] if c["ticker"] == "BETA")
    assert b["pullback"] == {"state": None, "levels": None}  # under 200 dagar
    assert data["strategies"]["pullback"]["labels"] == ["rekyl-i-trend", "rekyl-vandsignal"]
    assert validate_swing(data, INSTRUMENTS).errors == []
    # Bollingerläget för kandidaten: gårdagens stängning mot banden, avståndet till övre bandet i R.
    bb = a["pullback"]["bollinger"]
    assert bb["price"] == round(a["pullback"]["state"]["close"], 4) and bb["lower"] < bb["upper"]
    assert bb["at_upper"] == (bb["price"] >= bb["upper"])
    assert bb["upper_r"] == round((bb["upper"] - bb["price"]) / (lv["ref"] - lv["stop"]), 2)
    assert "bollinger" not in b["pullback"]  # inga nivåer, inget läge
    # Rangordningen: poäng = uppfyllda kriterier, rang 1 (enda kandidaten), mätvärden som nivåerna.
    rk = a["pullback"]["rank"]
    assert rk["rank"] == 1 and rk["score"] == sum(rk["criteria"].values()) and rk["criteria"]["vandsignal"]
    assert rk["measures"]["stop_atr"] == lv["risk_atr"] and rk["measures"]["upper_r"] == bb["upper_r"]
    assert data["ranking_criteria"]["vandsignal"] == "Vändsignal" and "rank" not in b["pullback"]
    rk["score"] += 1
    assert any("poängen" in e for e in validate_swing(data, INSTRUMENTS).errors)
    rk["score"] -= 1
    rk["rank"] = 2
    assert any("rangordningen" in e for e in validate_swing(data, INSTRUMENTS).errors)
    rk["rank"] = 1
    data["ranking_criteria"]["vandsignal"] = "Bästa läget"
    assert any("värdering" in e for e in validate_swing(data, INSTRUMENTS).errors)
    data["ranking_criteria"]["vandsignal"] = "Vändsignal"
    bb["at_upper"] = not bb["at_upper"]
    assert any("vid eller över" in e for e in validate_swing(data, INSTRUMENTS).errors)
    bb["at_upper"] = not bb["at_upper"]
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
    sr = pb["sr_filter"]  # stöd nära och motstånd minst 2R bort, mot övriga fall
    assert sr["with"]["cases"] + sr["without"]["cases"] == pb["all"]["cases"]
    rk = pb["ranking"]  # topp 5 per dag mot övriga, samma fall
    assert rk["top_n"] == 5 and rk["top"]["cases"] + rk["others"]["cases"] <= pb["all"]["cases"]
    assert sum(s["cases"] for s in rk["by_score"].values()) == rk["top"]["cases"] + rk["others"]["cases"]


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


def _cross_day(closes, up):
    """Direkt uträkning: sista dagen j där 50 MA gick över (up) eller under 200 MA."""
    ma = lambda n, j: sum(closes[j - n + 1:j + 1]) / n  # noqa: E731
    days = [j for j in range(200, len(closes))
            if (ma(50, j) > ma(200, j)) == up and (ma(50, j - 1) > ma(200, j - 1)) != up]
    return days[-1] if days else None


def test_golden_och_death_cross():
    # Lång nedgång, sedan uppgång: 50 MA korsar upp över 200 MA (golden cross).
    up = [300.0 - i for i in range(220)] + [80.0 + 3 * i for i in range(80)]
    j = _cross_day(up, True)
    assert j is not None
    crosses = swing.ma_crosses(up)
    assert crosses[j] == ("golden", 0) and crosses[j + 9] == ("golden", 9) and crosses[j + 10] is None
    assert crosses[j - 1] is None or crosses[j - 1][0] != "golden"
    m = swing.series(make_bars(up[:j + 4]))[-1]
    assert (m["ma_cross"], m["ma_cross_days"]) == ("golden", 3) and "golden-cross" in swing.technical_labels(m)
    # Omvänt: lång uppgång, sedan nedgång (death cross).
    down = [100.0 + i for i in range(220)] + [320.0 - 3 * i for i in range(80)]
    k = _cross_day(down, False)
    m = swing.series(make_bars(down[:k + 1]))[-1]
    assert (m["ma_cross"], m["ma_cross_days"]) == ("death", 0)
    assert swing.technical_labels(m)[-1] == "death-cross" and "golden-cross" not in swing.technical_labels(m)
    # Under 201 dagar: ingen korsning kan avgöras.
    assert all(x is None for x in swing.ma_crosses(up[:200]))
    old = {k2: v for k2, v in m.items() if k2 not in ("ma_cross", "ma_cross_days")}  # äldre filer
    assert "death-cross" not in swing.technical_labels(old)


def test_korsning_valideras_och_finns_i_historiken():
    closes = [300.0 - i for i in range(220)] + [80.0 + 3 * i for i in range(80)]
    j = _cross_day(closes, True)
    bars = {"ALFA.ST": make_bars(closes[:j + 2])}
    bars["BETA.ST"] = make_bars([100.0] * 60, end=bars["ALFA.ST"][-1].day)
    today = bars["ALFA.ST"][-1].day + timedelta(days=1)
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, bars, {})
    a = next(c for c in data["companies"] if c["ticker"] == "ALFA")
    assert "golden-cross" in a["labels"] and a["ma_cross_days"] == 1
    assert data["labels"]["golden-cross"] == "Golden cross" and data["labels"]["death-cross"] == "Death cross"
    assert validate_swing(data, INSTRUMENTS).errors == []
    a["ma_cross"] = "death"
    assert any("stämmer inte" in e for e in validate_swing(data, INSTRUMENTS).errors)
    n = 500
    hist_bars = {"^OMXSPI": make_bars([1000.0] * n), "ALFA.ST": make_bars(closes + [closes[-1]] * (n - len(closes))),
                 "BETA.ST": make_bars([600.0 - i for i in range(n)])}
    hist = swing_history.build(hist_bars, INSTRUMENTS, date(2028, 1, 1))
    assert {"golden-cross", "death-cross"} <= set(hist["labels"])


def test_swing_galler_dagsdatans_datum_inte_enstaka_bolags():
    """Har bara ett av två bolag gårdagens rad (Yahoo kl. 08:10) gäller måtten dagen före för båda,
    med förra handelsdagen och täckningen i filen."""
    alfa = make_bars([100.0 + i * 0.5 for i in range(70)])
    last = alfa[-1].day
    beta = make_bars([100.0 + i * 0.2 for i in range(69)], end=alfa[-2].day)  # saknar sista dagen
    today = last + timedelta(days=1 if last.weekday() < 4 else 3)
    data = swing.build(today, datetime.now(TZ), INSTRUMENTS, {"ALFA.ST": alfa, "BETA.ST": beta}, {})
    assert data["as_of"] == alfa[-2].day.isoformat() and data["expected_day"] == last.isoformat()
    assert data["coverage"] == {"with_day": 2, "with_expected": 1, "of": 2}
    assert {c["day"] for c in data["companies"]} == {alfa[-2].day.isoformat()}  # Alfaverken utan sista dagen
    assert validate_swing(data, INSTRUMENTS).errors == []

def test_bolag_utan_kurser_och_avnoterade_visas_i_filen():
    """Ett noterat bolag utan kurser står i "missing" (Yahoo saknar kursdata), ett med för kort historik med antalet
    dagar, och ett avnoterat (instruments.csv) i "delisted" med sista handelsdag och källa; det hämtas inte och
    räknas inte."""
    alfa = make_bars([100.0 + i * 0.5 for i in range(70)])
    last = alfa[-1].day
    today = last + timedelta(days=1 if last.weekday() < 4 else 3)
    src = "https://exempel.test/gammabolaget-avnoteras"
    instruments = {
        **INSTRUMENTS,
        "GAMMA": Instrument("Gammabolaget", (), "GAMMA", "GAMMA.ST", "Stockholm", avnoterad=last - timedelta(days=10),
                            avnoterad_kalla=src),
        "DELTA": Instrument("Deltaverken", (), "DELTA", "DELTA.ST", "Stockholm"),
    }
    assert set(swing.priced(instruments, today)) == {"ALFA", "BETA", "DELTA"}
    bars = {"ALFA.ST": alfa, "BETA.ST": make_bars([100.0] * 20, end=last),
            "GAMMA.ST": make_bars([100.0] * 70, end=last - timedelta(days=10))}
    data = swing.build(today, datetime.now(TZ), instruments, bars, {})
    assert [c["ticker"] for c in data["companies"]] == ["ALFA"]
    assert data["missing"] == [{"ticker": "BETA", "name": "Betabolaget", "reason": "short_history", "bars": 20},
                               {"ticker": "DELTA", "name": "Deltaverken", "reason": "no_data", "bars": 0}]
    assert data["delisted"] == [{"ticker": "GAMMA", "name": "Gammabolaget",
                                 "last_day": (last - timedelta(days=10)).isoformat(), "source": src}]
    assert data["coverage"]["of"] == 2  # bolag med kurser; det avnoterade hämtas inte
    assert validate_swing(data, instruments).errors == []
    # Valideringen: ett bolag både i tabellen och bland de saknade, och fel sista handelsdag.
    bad = {**data, "missing": [*data["missing"], {"ticker": "ALFA", "name": "Alfaverken", "reason": "no_data", "bars": 0}]}
    assert any("både i tabellen" in e for e in validate_swing(bad, instruments).errors)
    bad = {**data, "delisted": [{**data["delisted"][0], "last_day": last.isoformat()}]}
    assert any("stämmer inte med instruments.csv" in e for e in validate_swing(bad, instruments).errors)


def test_ett_enda_r_motstandet_i_strategins_r():
    """Swingfilen har inget eget R för stöd och motstånd (sr): motståndet i R finns bara som rangordningens room_r,
    räknat på strategins R (referens minus strategins stopp)."""
    bars = make_ohlc_trend_with_pullback()
    last = bars[-1].day
    data = swing.build(last + timedelta(days=1), datetime.now(TZ), INSTRUMENTS, {"ALFA.ST": bars}, {})
    a = data["companies"][0]
    assert "resistance_r" not in a["sr"]
    lv, m = a["pullback"]["levels"], a["pullback"]["rank"]["measures"]
    rst = a["sr"]["resistance"]
    if rst:
        import levels
        assert m["room_r"] == round((levels.resistance_edge(rst) - lv["ref"]) / (lv["ref"] - lv["stop"]), 2)
    else:
        assert m["room_r"] is None and not m["has_resistance"]
