"""Tester för stöd och motstånd (scripts/levels.py) och testet i swinghistoriken (påhittade kurser)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import levels  # noqa: E402
from validate import Result, _check_sr  # noqa: E402


def test_vandpunkter():
    #     dag:  0    1    2    3    4    5    6    7    8    9   10
    lo = [10, 9, 8, 7, 8, 9, 10, 9, 8, 9, 10.0]
    h = [x + 2 for x in lo]
    h[5] = 20.0  # en topp dag 5
    pts = levels.pivots(h, lo, i=10, lookback=120, k=3)
    assert (7, "botten") in pts and (20.0, "topp") in pts
    # Dag 8 (low 8) har bara två dagar efter sig: ingen vändpunkt än.
    assert all(p != (8, "botten") for p in pts)


def test_zoner_hogst_en_atr_breda():
    pts = [(100.0, "botten"), (100.8, "topp"), (101.5, "botten"), (110.0, "topp")]
    z = levels.zones(pts, atr=1.0)
    assert [(x["low"], x["high"], x["touches"]) for x in z] == [(100.0, 100.8, 2), (101.5, 101.5, 1), (110.0, 110.0, 1)]
    assert (z[0]["bottoms"], z[0]["tops"]) == (1, 1) and z[0]["mid"] == 100.4


def test_stod_och_motstand_klassas_mot_stangningen():
    z = [{"kind": "zon", "low": 95, "high": 96, "mid": 95.5},  # under: stöd
         {"kind": "zon", "low": 99, "high": 101, "mid": 99.8},  # stängningen i zonen, mitten under: stöd
         {"kind": "zon", "low": 99.5, "high": 102, "mid": 101},  # stängningen i zonen, mitten över: motstånd
         {"kind": "zon", "low": 104, "high": 105, "mid": 104.5}]  # över: motstånd
    sup, res = levels.classify(z, close=100.0)
    assert [x["low"] for x in sup] == [95, 99] and [x["low"] for x in res] == [99.5, 104]


def test_bruten_topp_blir_stod_och_sammanfattning():
    # Uppgång till en topp (dag 10, high 110), rekyl, ny uppgång över toppen: toppen ligger nu under kursen.
    closes = [100 + i for i in range(11)] + [109 - i for i in range(5)] + [105 + 2 * i for i in range(8)]
    h = [x + 0.5 for x in closes]
    lo = [x - 0.5 for x in closes]
    i = len(closes) - 1
    s = levels.summary(h, lo, closes[i], i, atr=1.0, ema20=None, ma50=None, ma200=None)
    sup = s["support"]
    assert sup["kind"] == "zon" and sup["high"] == 110.5 and sup["tops"] == 1  # bruten topp = stöd
    assert sup["dist_pct"] == round((119 - 110.5) / 119 * 100, 2) and sup["dist_atr"] == 8.5
    assert s["resistance"] is None and s["resistance_r"] is None  # inget motstånd inom 120 dagar
    assert s["trigger"] == 119.5 and s["stop"] == round(sup["low"] - 0.1, 4)
    # Med 20 EMA över kursen blir den motstånd; motståndet i R räknas från triggernivån.
    s2 = levels.summary(h, lo, closes[i], i, atr=1.0, ema20=125.0, ma50=112.0, ma200=None)
    assert s2["resistance"]["kind"] == "20 EMA" and s2["support"]["kind"] == "50 MA"
    assert s2["resistance_r"] == round((125.0 - 119.5) / (119.5 - (112.0 - 0.1)), 2)
    r = Result()
    _check_sr(s2, "t", r)
    assert r.errors == []
    s2["resistance_r"] = 9.9
    _check_sr(s2, "t", r)
    assert any("motståndet i R" in e for e in r.errors)
    assert levels.summary(h, lo, closes[i], i, atr=None, ema20=None, ma50=None, ma200=None) is None


def test_medelvarde_inne_i_en_zon_ar_ingen_egen_niva():
    """Stängningen 305 ligger i en zon vars mitt är över kursen (motstånd). 20 EMA 303,21 under kursen men inne i
    zonen är ingen egen stödnivå, annars hamnar stödet över motståndets nedre kant (Beijer Alma 2026-11-05)."""
    zone = {"kind": "zon", "low": 302.49, "high": 307.95, "mid": 305.35, "touches": 4, "bottoms": 0, "tops": 4}
    below = {"kind": "zon", "low": 290.0, "high": 292.0, "mid": 291.0, "touches": 2, "bottoms": 2, "tops": 0}
    mas = levels.ma_levels(303.21, 301.42, 280.0)
    sup, res = levels.nearest([below, zone], mas, close=305.0)
    assert res["kind"] == "zon" and res["low"] == 302.49
    assert sup["kind"] == "50 MA" and sup["high"] == 301.42  # 20 EMA inne i zonen räknas inte
    assert sup["high"] <= res["low"]
    # Samma sak omvänt: stängningen i en stödzon, 20 EMA över kursen men inne i zonen är inget motstånd.
    sup2, res2 = levels.nearest([{**zone, "mid": 304.0}], levels.ma_levels(306.0, None, 320.0), close=305.0)
    assert sup2["kind"] == "zon" and res2["kind"] == "200 MA" and sup2["high"] <= res2["low"]
    # Valideringen fångar ett stöd över motståndets nedre kant.
    bad = {"support": {"kind": "20 EMA", "low": 303.21, "high": 303.21, "mid": 303.21}, "resistance": {**zone},
           "trigger": 306.5, "stop": 302.0, "resistance_r": round((302.49 - 306.5) / (306.5 - 302.0), 2)}
    r = Result()
    _check_sr(bad, "t", r)
    assert any("ligger över motståndet" in e for e in r.errors)

def test_kursen_inne_i_zonen_och_motstand_i_r_till_ovre_kanten():
    """Stängningen inne i motståndszonen: markeras "inside" (avstånd 0), och motståndet i R räknas till zonens
    övre kant. Utanför zonen som förut, till den nedre."""
    zone = {"kind": "zon", "low": 302.49, "high": 307.95, "mid": 305.35, "touches": 4, "bottoms": 0, "tops": 4}
    d = levels._dist(zone, 305.0, 2.0, "resistance")
    assert d["inside"] is True and d["dist_pct"] == 0.0
    assert levels.resistance_edge(d) == 307.95
    outside = levels._dist({**zone, "low": 306.0, "mid": 307.0}, 305.0, 2.0, "resistance")
    assert outside["inside"] is False and levels.resistance_edge(outside) == 306.0
    ma = levels._dist(levels.ma_levels(305.0, None, None)[0], 305.0, 2.0, "resistance")
    assert ma["inside"] is False  # ett medelvärde är en enda nivå
    sr = {"support": None, "resistance": d, "trigger": 306.5, "stop": 300.5,
          "resistance_r": round((307.95 - 306.5) / 6.0, 2)}
    r = Result()
    _check_sr(sr, "t", r)
    assert r.errors == []
    _check_sr({**sr, "resistance_r": round((302.49 - 306.5) / 6.0, 2)}, "t", r)  # till nedre kanten: fel
    assert any("motståndet i R" in e for e in r.errors)