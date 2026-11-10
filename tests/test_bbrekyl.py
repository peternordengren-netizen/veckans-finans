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
    """Nio dagar: två lugna, utbrott dag 2 (stängning 105 > övre 104, omsättning 2×), rekyl ned mot mittbandet."""
    return [
        day(100, 101, 99, 100), day(100, 101, 99, 100.5),
        day(101, 106, 100.8, 105, volume=2000),                       # utbrott (index 2, 6 dagar före dag i)
        day(105, 107, 103, 104, volume=900), day(104, 104.5, 102, 102.5, volume=800),
        day(102.5, 103, 101.5, 102, volume=700), day(102, 102.5, 101, 101.5, volume=700),
        day(101.5, 102, 100.8, 101.2, volume=600),
        day(101.2, 101.6, 100.4, 101.0, volume=600),                  # dag i: low 100,4 nära mittbandet 100
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


def test_villkor_som_inte_uppfylls():
    # Omsättningen under 1,5× snittet: inget utbrott.
    d = base_days()
    d[2] = {**d[2], "volume": 1400}
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
    m = {"signal": True, "lower_volume": True, "room_r": 2.0, "has_resistance": True, "upper_r": 1.0, "stop_atr": 1.5,
         "ma50_change_pct": 0.1}
    assert all(bbrekyl.criteria(m).values())
    none = bbrekyl.criteria({**m, "signal": False, "lower_volume": False, "room_r": 1.99, "upper_r": 0.99,
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
    tail = [(last + 4.5, last + 5.5, last + 0.5, 3000.0),   # utbrott
            (last + 4.0, last + 4.8, last + 3.0, 900.0), (last + 3.0, last + 3.6, last + 2.2, 800.0),
            (last + 2.0, last + 2.6, last + 1.6, 700.0), (last + 1.6, last + 2.0, last + 1.2, 700.0),
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

    check = bollinger.check(today, now, inst, fetch, data)
    rows = check["bbrekyl"]["rows"]
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
