"""Tester för strategin Rekyl efter Bollingerutbrott (scripts/bbrekyl.py), med påhittade kurser."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bbrekyl  # noqa: E402
import bollinger  # noqa: E402
import pullback  # noqa: E402
import swing  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument, validate_swing  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")


def day(o, h, lo, c, volume=1000.0, volume_avg=1000.0, mid=100.0, upper=104.0, ma50=95.0):
    return {"open": o, "high": h, "low": lo, "close": c, "volume": volume, "volume_avg": volume_avg, "mid": mid,
            "upper": upper, "ma50": ma50}


def state(days, ema20=101.0):
    return {"days": days, "ema20": ema20}


def base_days():
    """Nio dagar: två lugna, utbrott dag 2 (stängning 105 > övre 104, omsättning 2×), rekyl ned mot mittbandet med
    uppdagar (2 000) lika stor omsättning som neddagar (700 + 500 + 400 + 400), snitt 0,67× och inga tunga röda dagar."""
    return [
        day(100, 101, 99, 100), day(100, 101, 99, 100.5),
        day(101, 106, 100.8, 105, volume=2000),                       # utbrott (index 2, 6 dagar före dag i)
        day(105, 107, 103, 104, volume=700),                          # ned, röd
        day(104, 105, 103, 104.5, volume=1000),                       # upp
        day(104.5, 104.8, 102, 102.5, volume=500),                    # ned, röd
        day(102.5, 103.5, 102, 103, volume=1000),                     # upp
        day(103, 103, 101, 101.5, volume=400),                        # ned, röd
        day(101.5, 101.6, 100.4, 101.0, volume=400),                  # dag i: low 100,4 nära mittbandet 100
    ]


def test_utbrott_rekyl_och_etikett():
    st = state(base_days())
    assert bbrekyl.breakout(st) == 2
    assert bbrekyl.touched(st) == "mid"  # 0,4 % över mittbandet
    assert bbrekyl.held(st, 2)
    assert bbrekyl.labels(st) == ["bbrekyl"]
    lv = bbrekyl.levels(st, atr=2.0)
    assert lv["signal"] is None and lv["ref"] == 101.0 and lv["stop"] == 100.4  # rekylens lägsta low
    assert lv["target"] == 107.0 and lv["target_r"] == round((107 - 101) / 0.6, 2)  # utbrottets högsta high
    assert lv["r2"] == round(101 + 1.2, 4) and lv["r3"] == round(101 + 1.8, 4)
    assert lv["breakout"]["days_ago"] == 6 and lv["breakout"]["volume_ratio"] == 2.0
    assert lv["lower_volume"] is True
    assert lv["risk_atr"] == 0.3


def test_volymvillkoren_med_siffror():
    st = state(base_days())
    vc = bbrekyl.volume_checks(st, 2)
    assert vc == {"breakout_ratio": 2.0, "r1": True, "red_days": 4, "red_max_ratio": 0.7, "r2": True,
                  "rekyl_avg_ratio": round(4000 / 6 / 1000, 2), "r3": True, "up_volume": 2000, "down_volume": 2000,
                  "r4": True}
    assert bbrekyl.levels(st, atr=2.0)["volume"] == vc
    # Kraven: utbrott minst 1,5× och r2; r1, r3 och r4 är rangkriterier.
    assert bbrekyl.REQUIRED_RULES == ("r2",) and bbrekyl.THRESHOLDS["required_rules"] == ["r2"]
    # r1: utbrottet med 1,8× räknas (krav 1,5×), men r1 (2×) är inte uppfyllt.
    d = base_days()
    d[2] = {**d[2], "volume": 1800}
    assert bbrekyl.labels(state(d)) == ["bbrekyl"] and bbrekyl.labels(state(d), rules=bbrekyl.ALL_RULES) == []
    lv = bbrekyl.levels(state(d), atr=2.0)
    assert lv["volume"]["r1"] is False and lv["volume"]["breakout_ratio"] == 1.8
    assert bbrekyl.labels(state(d), rules=("r2", "r3", "r4")) == ["bbrekyl"]
    # r2: en röd dag med 1,6× omsättning.
    d = base_days()
    d[5] = {**d[5], "volume": 1600}
    vc = bbrekyl.volume_checks(state(d), 2)
    assert not vc["r2"] and vc["red_max_ratio"] == 1.6
    assert bbrekyl.labels(state(d)) == [] and bbrekyl.labels(state(d), rules=("r1",)) == ["bbrekyl"]
    # Exakt 1,5× är inte "över" 1,5×.
    d[5] = {**d[5], "volume": 1500}
    assert bbrekyl.volume_checks(state(d), 2)["r2"]
    # r3: snittet i rekylen på eller över 20-dagarssnittet.
    d = base_days()
    for j in (4, 6):
        d[j] = {**d[j], "volume": 2600, "upper": 106}  # övre bandet över stängningen: ingen ny utbrottsdag
    vc = bbrekyl.volume_checks(state(d), 2)
    assert not vc["r3"] and vc["r4"] and bbrekyl.labels(state(d)) == ["bbrekyl"]  # rangkriterium, inget krav
    assert bbrekyl.labels(state(d), rules=("r3",)) == []
    # r4: mer omsättning på neddagar än uppdagar.
    d = base_days()
    d[6] = {**d[6], "volume": 900}
    vc = bbrekyl.volume_checks(state(d), 2)
    assert (vc["up_volume"], vc["down_volume"], vc["r4"]) == (1900, 2000, False)
    assert bbrekyl.labels(state(d)) == ["bbrekyl"] and bbrekyl.labels(state(d), rules=bbrekyl.ALL_RULES) == []


def test_villkor_som_inte_uppfylls():
    # Omsättningen under 1,5× snittet: inget utbrott, inte ens med grundregeln.
    d = base_days()
    d[2] = {**d[2], "volume": 1400}
    assert bbrekyl.breakout(state(d), rules=()) is None
    assert bbrekyl.breakout(state(d)) is None and bbrekyl.labels(state(d)) == []
    # Stängningen under 50 MA på utbrottsdagen.
    d = base_days()
    d[2] = {**d[2], "ma50": 106}
    assert bbrekyl.breakout(state(d)) is None
    # Utbrottet för länge sedan (dag i−8 ligger utanför de 7 dagarna).
    d = base_days()
    d[0], d[2] = {**d[2]}, {**d[1]}
    assert bbrekyl.breakout(state(d)) is None
    # En stängning under mittbandet sedan utbrottet.
    d = base_days()
    d[5] = {**d[5], "close": 99.5}
    assert bbrekyl.labels(state(d)) == []
    # En stängning under utbrottsdagens low (100,8) men över mittbandet (mittbandet 99 den dagen).
    d = base_days()
    d[6] = {**d[6], "close": 100.6, "mid": 99.0}
    assert not bbrekyl.held(state(d), 2)
    # Gårdagens low för långt från mittbandet och 20 EMA (och inte mellan dem).
    d = base_days()
    d[-1] = {**d[-1], "low": 101.5, "close": 101.6}
    assert bbrekyl.touched(state(d, ema20=100.2)) is None
    # Mellan mittbandet och 20 EMA räknas.
    assert bbrekyl.touched(state(d, ema20=103.0)) == "mellan"


def test_vandsignal_ger_trigger_och_annan_etikett():
    d = base_days()
    # Hammer dag i: lång nedre skugga, liten kropp, high nära stängningen.
    d[-1] = {**d[-1], "open": 101.6, "high": 101.75, "low": 100.3, "close": 101.7}
    st = state(d)
    assert pullback.signal(bbrekyl.candle(st)) == "hammer"
    assert bbrekyl.labels(st) == ["bbrekyl-vandsignal"]
    lv = bbrekyl.levels(st, atr=2.0)
    assert lv["trigger"] == 101.75 and lv["ref"] == 101.75


def test_rangkriterier():
    m = {"signal": True, "r1": True, "r3": True, "r4": True, "room_r": 2.0, "has_resistance": True, "upper_r": 1.0,
         "stop_atr": 1.5, "ma50_change_pct": 0.1}
    assert all(bbrekyl.criteria(m).values())
    assert len(bbrekyl.CRITERIA) == 8 and "lagre_omsattning" not in bbrekyl.CRITERIA
    none = bbrekyl.criteria({**m, "signal": False, "r1": False, "r3": False, "r4": False, "room_r": 1.99, "upper_r": 0.99,
                             "stop_atr": 1.51, "ma50_change_pct": 0.0})
    assert not any(none.values())
    assert set(bbrekyl.CRITERIA) == set(bbrekyl.criteria(m))
    for text in bbrekyl.CRITERIA.values():
        assert not any(w in text.lower() for w in ("köp", "sälj", "kursmål", "bäst"))
    entries = [{"name": "Alfaverken", "score": 4, "measures": {**m, "room_r": 3.0}},
               {"name": "Betabolaget", "score": 4, "measures": {**m, "room_r": 5.0}},
               {"name": "Gammagruppen", "score": 5, "measures": {**m, "room_r": 1.0}}]
    bbrekyl.rank(entries)
    assert [e["name"] for e in sorted(entries, key=lambda e: e["rank"])] == ["Gammagruppen", "Betabolaget", "Alfaverken"]


def make_breakout_bars(end=date(2026, 11, 6)):
    """90 dagar svag uppgång med lugn omsättning, ett utbrott med hög omsättning 5 dagar före sista dagen och en
    rekyl tillbaka mot mittbandet. Påhittade kurser."""
    closes = [100 + 0.05 * i + (0.3 if i % 2 else -0.3) for i in range(84)]
    days, d = [], end
    while len(days) < 90:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    bars = [Bar(dd, c, c, c + 0.4, c - 0.4, 1000.0, c) for dd, c in zip(days, closes)]
    last = closes[-1]
    # Rekylen: upp- och neddagar med lika stor omsättning (1 500), snitt under 20-dagarssnittet.
    tail = [(last + 4.5, last + 5.5, last + 0.5, 3000.0),   # utbrott
            (last + 4.0, last + 4.8, last + 3.0, 600.0), (last + 4.4, last + 4.9, last + 3.6, 900.0),
            (last + 2.6, last + 3.2, last + 2.2, 500.0), (last + 1.6, last + 2.0, last + 1.2, 400.0),
            (last + 1.7, last + 2.0, last + 1.0, 600.0)]  # dag i
    for (c, h, lo, v), dd in zip(tail, days[84:]):
        bars.append(Bar(dd, c, c, h, lo, v, c + 0.1))
    return bars


def test_swing_bygger_och_validerar_strategin():
    bars = make_breakout_bars()
    inst = {"ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm")}
    last = bars[-1].day
    today = last + timedelta(days=3 if last.weekday() == 4 else 1)
    # Rapport kvällen före utbrottsdagen: utbrottsdagen är reaktionsdag.
    bo_day = bars[-6].day
    prev_day = bars[-7].day
    rep = {"ALFA": {"published": datetime.combine(prev_day, datetime.min.time(), TZ).replace(hour=18).isoformat(),
                    "verdict": "över"}}
    data = swing.build(today, datetime.now(TZ), inst, {"ALFA.ST": bars}, rep, (None, {}))
    a = data["companies"][0]
    bb = a["bbrekyl"]
    assert bb["levels"] is not None, bb["state"]
    assert set(a["labels"]) & set(bbrekyl.LABELS)
    lv = bb["levels"]
    assert lv["breakout"]["day"] == bo_day.isoformat() and lv["breakout"]["days_ago"] == 5
    assert lv["breakout"]["report"] == {"published": rep["ALFA"]["published"], "verdict": "över"}
    assert bb["rank"]["rank"] == 1 and bb["bollinger"]["price"] == round(bars[-1].close, 4)
    # RSI(14) och Stoch RSI för kandidaten (scripts/momentum.py), samma RSI som swingfilens rsi14.
    import momentum
    assert a["momentum"] == momentum.snapshot([x.adj_close for x in bars])
    assert a["momentum"]["rsi14"] == a["rsi14"]
    assert data["strategies"]["bbrekyl"]["name"] == "Rekyl efter Bollingerutbrott"
    assert validate_swing(data, inst).errors == []
    # Valideringen räknar om nivåerna ur fönstret.
    lv["stop"] = lv["stop"] - 1
    assert any("stop" in e for e in validate_swing(data, inst).errors)
    lv["stop"] = lv["stop"] + 1
    bb["rank"]["criteria"]["vandsignal"] = not bb["rank"]["criteria"]["vandsignal"]
    assert any("kriterierna" in e for e in validate_swing(data, inst).errors)
    bb["rank"]["criteria"]["vandsignal"] = not bb["rank"]["criteria"]["vandsignal"]
    # Status i dag vid Bollingerkontrollen: samma avsnitt som Pullback, under "bbrekyl".
    now = datetime.combine(today, datetime.min.time(), TZ).replace(hour=9, minute=20)
    tb = Bar(today, lv["ref"] + 0.5, lv["ref"] + 0.5, lv["ref"] + 0.6, lv["stop"] + 0.1, 500.0, lv["ref"])

    def fetch(tickers, start, end):
        return {"ALFA.ST": bars + [tb]}

    calls = []

    def intraday(tickers, d):
        calls.append(tickers)
        return None  # timdatan gick inte att hämta: ingen jämförelse, statusen påverkas inte

    check = bollinger.check(today, now, inst, fetch, data, intraday=intraday)
    assert calls == [["ALFA.ST"]]  # ett anrop, bara strategins kandidater
    rows = check["bbrekyl"]["rows"]
    assert rows[0]["today"]["volume"] is None
    # Vid kontrollen: RSI och Stoch RSI med dagens kurs som sista värde.
    assert rows[0]["momentum"] == momentum.snapshot([x.adj_close for x in bars] + [tb.close])
    assert [r["ticker"] for r in rows] == ["ALFA"]
    assert rows[0]["today"]["trigger"] == bb["state"]["days"][-1]["high"]
    assert rows[0]["today"]["status"] in bollinger.TODAY_STATUS
    assert bollinger.validate(check) == []


def test_historiken_har_strategin():
    import swing_history

    bars = make_breakout_bars()
    from prices import INDEX_TICKER
    idx = [Bar(b.day, 1000.0 + i, 1000.0 + i, 1001.0 + i, 999.0 + i, 0.0, 1000.0 + i) for i, b in enumerate(bars)]
    inst = {"ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm")}
    data = swing_history.build({"ALFA.ST": bars, INDEX_TICKER: idx}, inst, bars[-1].day + timedelta(days=1),
                               results_dir=ROOT / "data" / "mock" / "results")
    hb = data["bbrekyl"]
    assert set(hb["labels"]) == set(bbrekyl.LABELS) and "all" in hb
    assert hb["all"]["cases"] >= 0


def test_omsattning_hittills_mot_normalt():
    """Dagens 15-minutersstaplar mot snittet för tidigare dagar fram till samma klockslag."""
    import pandas as pd

    stamps, vols = [], []
    for d in pd.bdate_range("2026-10-26", "2026-11-09"):  # 11 handelsdagar före i dag
        for hhmm, v in (("09:00", 100.0), ("09:15", 50.0), ("09:30", 50.0), ("17:15", 300.0)):
            stamps.append(pd.Timestamp(f"{d.date()} {hhmm}", tz="Europe/Stockholm"))
            vols.append(v)
    for hhmm, v in (("09:00", 200.0), ("09:15", 100.0)):  # i dag t.o.m. 09:15-stapeln
        stamps.append(pd.Timestamp(f"2026-11-10 {hhmm}", tz="Europe/Stockholm"))
        vols.append(v)
    cols = pd.MultiIndex.from_tuples([("ALFA.ST", "Volume")])
    df = pd.DataFrame([[v] for v in vols], index=pd.DatetimeIndex(stamps), columns=cols)
    vol = bollinger.volume_vs_normal(df, "ALFA.ST", date(2026, 11, 10))
    assert vol == {"so_far": 300, "normal": 150, "ratio": 2.0, "until": "09:15", "days": 11}
    assert bollinger.volume_vs_normal(df, "BETA.ST", date(2026, 11, 10)) is None
    assert bollinger.volume_vs_normal(df, "ALFA.ST", date(2026, 11, 11)) is None  # inga staplar den dagen
    short = df[df.index >= pd.Timestamp("2026-11-06", tz="Europe/Stockholm")]
    assert bollinger.volume_vs_normal(short, "ALFA.ST", date(2026, 11, 10)) is None  # för få tidigare dagar
