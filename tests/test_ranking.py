"""Tester för rangordningen av Pullback-kandidaterna (scripts/ranking.py) och dess validering (påhittade värden)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ranking  # noqa: E402


def meas(**kw):
    base = {"signal": True, "room_r": 3.0, "has_resistance": True, "support_atr": 0.5, "upper_r": 1.5,
            "stop_atr": 1.2, "ma50_change_pct": 1.0}
    return {**base, **kw}


def test_alla_sex_kriterier_och_gransvarden():
    c = ranking.criteria(meas())
    assert c == dict.fromkeys(ranking.CRITERIA, True)
    # Gränserna räknas som uppfyllda: 2R, 1 ATR, 1R, 1,5 ATR.
    assert all(ranking.criteria(meas(room_r=2.0, support_atr=1.0, upper_r=1.0, stop_atr=1.5)).values())
    none = ranking.criteria(meas(signal=False, room_r=1.99, support_atr=1.01, upper_r=0.99, stop_atr=1.51,
                                 ma50_change_pct=0.0))
    assert not any(none.values())
    # Inget motstånd inom 120 dagar räknas som uppfyllt; ingen stödzon eller inget Bollingerläge som inte.
    assert ranking.criteria(meas(has_resistance=False, room_r=None))["motstand_2r"]
    assert not ranking.criteria(meas(support_atr=None))["stodzon"]
    assert not ranking.criteria(meas(upper_r=None))["bollinger_1r"]


def test_mätvärden_ur_kurserna():
    c = [100.0 + i * 0.1 for i in range(80)]
    m = ranking.measures(True, ref=108.0, stop=106.0, resistance_edge=114.0, support_dist_atr=0.3, upper_r=1.2,
                         risk_atr=1.1, c=c, i=79)
    assert m["room_r"] == 3.0 and m["has_resistance"] and m["ma50_change_pct"] > 0
    m2 = ranking.measures(False, 108.0, 106.0, None, None, None, None, c, 60)  # för kort för 50 MA 20 dagar bakåt
    assert m2["room_r"] is None and not m2["has_resistance"] and m2["ma50_change_pct"] is None


def test_rang_flest_kriterier_sedan_mest_utrymme_till_motstandet():
    entries = [
        {"name": "Alfaverken", "score": 4, "measures": meas(room_r=2.5)},
        {"name": "Betabolaget", "score": 5, "measures": meas(room_r=0.5)},
        {"name": "Gammagruppen", "score": 4, "measures": meas(room_r=6.0)},
        {"name": "Deltahem", "score": 4, "measures": meas(has_resistance=False, room_r=None)},  # inget motstånd
    ]
    ranking.rank(entries)
    assert [e["name"] for e in sorted(entries, key=lambda e: e["rank"])] == [
        "Betabolaget", "Deltahem", "Gammagruppen", "Alfaverken"]
