"""Tester för strategin Momentum-utbrott (VCP) (scripts/vcp.py), med påhittade kurser och bolag."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bollinger  # noqa: E402
import swing  # noqa: E402
import vcp  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument, validate_swing  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")


def state(**over):
    """Mått som uppfyller alla krav (Nära pivot): pivot 112, rekyler 10,7 % → 5,4 % → 2,2 %."""
    s = {"close": 109.0, "high": 109.5, "low": 108.0, "rs_pct": 85, "ma50": 106.0, "ma150": 95.0, "ma200": 88.0,
         "ma200_prev": 85.0, "low52": 60.0, "high52": 112.0, "base_days": 40, "pivot": 112.0,
         "contractions": [{"peak": 112.0, "trough": 100.0, "depth_pct": 10.71, "peak_ago": 40, "trough_ago": 30},
                          {"peak": 110.0, "trough": 104.0, "depth_pct": 5.45, "peak_ago": 20, "trough_ago": 14},
                          {"peak": 109.0, "trough": 106.6, "depth_pct": 2.2, "peak_ago": 8, "trough_ago": 3}],
         "volume": 1000.0, "volume_avg50": 1000.0, "vol_dry": 800.0, "vol_avg": 1000.0, "atr_dry": 1.5, "atr_avg": 2.0}
    s.update(over)
    return s


def test_rs_och_percentil_mot_listan():
    c = [100.0] * 253
    c[-1] = 130.0  # +30 % på alla horisonter (samma startvärde)
    assert round(vcp.rs_score(c, 252), 6) == 0.3
    assert vcp.rs_score(c, 251) is None  # 12 månader saknas
    scores = sorted([0.1, 0.2, 0.3, 0.4])
    assert vcp.percentile(0.4, scores) == 75 and vcp.percentile(0.1, scores) == 0
    assert vcp.percentile(0.5, scores) == 99  # högst 99


def test_krav_och_etiketter():
    assert vcp.labels(state()) == ["vcp-nara-pivot"]
    assert vcp.labels(state(rs_pct=69)) == []  # RS under 70
    assert vcp.labels(state(ma150=107.0)) == []  # MA-ordningen bruten
    assert vcp.labels(state(ma200_prev=89.0)) == []  # 200 MA inte stigande
    assert vcp.labels(state(low52=90.0)) == []  # under 30 % över 52-veckorslägsta
    assert vcp.labels(state(high52=150.0)) == []  # mer än 25 % under 52-veckorshögsta
    assert vcp.labels(state(base_days=10)) == [] and vcp.labels(state(base_days=61)) == []  # 3–12 veckor
    grows = state()["contractions"][:2][::-1]
    assert vcp.labels(state(contractions=grows)) == []  # rekylerna växer
    assert vcp.labels(state(contractions=state()["contractions"][:1])) == []  # bara en rekyl
    deep = [dict(x) for x in state()["contractions"]]
    deep[-1]["trough"] = 102.0  # stoppet 8,9 % under pivoten
    assert vcp.labels(state(contractions=deep)) == []
    assert vcp.labels(state(close=106.0)) == []  # mer än 5 % under pivoten
    # Utbrott: över pivoten med minst 1,5× volym; utan volym ingen etikett.
    assert vcp.labels(state(close=113.0, volume=1500.0)) == ["vcp-utbrott"]
    assert vcp.labels(state(close=113.0, volume=1400.0)) == []


def test_nivaer_och_kriterier():
    lv = vcp.levels(state(), "vcp-nara-pivot", atr=2.0)
    assert lv["ref"] == 112.0 and lv["trigger"] == 112.0 and lv["stop"] == 106.6
    assert lv["stop_under_pivot_pct"] == round(5.4 / 112 * 100, 2) and lv["target"] == round(112 + 3 * 5.4, 4)
    assert lv["depths"] == [10.71, 5.45, 2.2] and lv["base_weeks"] == 8.0
    ub = vcp.levels(state(close=113.0, volume=1500.0), "vcp-utbrott", atr=2.0)
    assert ub["ref"] == 113.0 and ub["trigger"] is None and ub["volume_ratio"] == 1.5
    crit = vcp.criteria(state())
    assert crit == {"volym_avtar": True, "atr_avtar": True, "tre_rekyler": True, "sista_rekyl": True, "rs_80": True,
                    "vid_pivot": False}  # 2,7 % under pivoten
    assert vcp.criteria(state(close=110.0))["vid_pivot"] is True
    for text in vcp.CRITERIA.values():
        assert not any(w in text.lower() for w in ("köp", "sälj", "kursmål", "bäst"))
    entries = [{"name": "Alfa", "score": 4, "rs_pct": 80}, {"name": "Beta", "score": 4, "rs_pct": 90},
               {"name": "Gamma", "score": 5, "rs_pct": 70}]
    vcp.rank(entries)
    assert [e["name"] for e in sorted(entries, key=lambda e: e["rank"])] == ["Gamma", "Beta", "Alfa"]


def test_rekylerna_i_basen():
    # Topp dag 0 (112), botten 100, topp 110, botten 104, topp 109, sedan ned till 106.
    path = [112, 108, 104, 100, 103, 107, 110, 108, 106, 104, 106, 108, 109, 108, 107, 106, 106.5]
    h = [x + 0.5 for x in path]
    lo = [x - 0.5 for x in path]
    out = vcp.contractions(h, lo, 0, len(path) - 1, swing=2)
    assert [x["depth_pct"] for x in out] == [round((112.5 - 99.5) / 112.5 * 100, 2),
                                            round((110.5 - 103.5) / 110.5 * 100, 2),
                                            round((109.5 - 105.5) / 109.5 * 100, 2)]
    assert [x["peak_ago"] for x in out] == [16, 10, 4]


def test_simulering_3r():
    lv = {"pivot": 100.0, "stop": 98.0}
    o = [99, 99, 101, 104, 108, 114, 116]
    h = [99.5, 99.8, 102, 106, 110, 116, 117]
    lo = [98, 98, 100.5, 103, 107, 112, 115]
    case = vcp.simulate(o, h, lo, 0, "vcp-nara-pivot", lv)
    # Pivoten nås dag 2, start vid öppningen 101 (över pivoten), risk 3, målet 110 nås dag 4.
    assert case["outcome"] == "3R" and case["end"] == 4
    lo_stop = [98, 98, 100.5, 97, 107, 112, 115]
    assert vcp.simulate(o, h, lo_stop, 0, "vcp-nara-pivot", lv)["outcome"] == "stopp"
    never = [98, 98, 98, 98, 98, 98, 98]
    assert vcp.simulate(never, never, never, 0, "vcp-nara-pivot", lv)["outcome"] == "ej utlöst"
    s = vcp.summarize([case, {"outcome": "stopp", "net_pct": -5.0, "risk_pct": 5.0}])
    assert s["r3_first"] == 1 and s["stop_first"] == 1 and s["r3_share"] == 0.5


def series(end, closes, vols=None):
    days, d = [], end
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    vols = vols or [1000.0] * len(closes)
    return [Bar(dd, c, c, c * 1.004, c * 0.996, v, c) for dd, c, v in zip(days, closes, vols)]


def vcp_closes():
    """260 dagar uppgång från 60 till 112, sedan en bas med krympande rekyler (påhittat)."""
    up = [60 + 52 * j / 219 for j in range(220)]
    base = [112, 110, 106, 103, 100.5, 102, 105, 107.5, 110, 109, 107, 105.5, 104, 105, 106.5, 108, 109.2, 108.5,
            107.5, 106.8, 107.2, 107.8, 108.4, 108.8, 108.9, 108.6, 108.2, 108.0, 108.3, 108.6, 108.8, 108.9, 109.0,
            108.9, 108.8, 108.9, 109.0, 109.1, 109.2, 109.3]
    return up + base


def test_swing_bygger_och_validerar_strategin():
    end = date(2026, 11, 6)
    alfa = vcp_closes()
    n = len(alfa)
    others = {t: [100 + k * 0.01 * j for j in range(n)] for k, t in enumerate(["BETA", "GAMMA", "DELTA"])}
    inst = {t: Instrument(name, (), t, f"{t}.ST", "Stockholm") for t, name in
            [("ALFA", "Alfaverken"), ("BETA", "Betabolaget"), ("GAMMA", "Gammagruppen"), ("DELTA", "Deltahem")]}
    vol = [1000.0] * (n - 10) + [700.0] * 10
    bars = {"ALFA.ST": series(end, alfa, vol), **{f"{t}.ST": series(end, c) for t, c in others.items()}}
    today = end + timedelta(days=3)
    data = swing.build(today, datetime.now(TZ), inst, bars, {})
    a = next(c for c in data["companies"] if c["ticker"] == "ALFA")
    st = a["vcp"]["state"]
    assert st["rs_pct"] == 75  # bäst av fyra i listan
    assert "vcp-nara-pivot" in a["labels"], (st["base_days"], [x["depth_pct"] for x in st["contractions"]], vcp.trend(st))
    lv = a["vcp"]["levels"]
    assert lv["depths"] == sorted(lv["depths"], reverse=True) and len(lv["depths"]) >= 2
    assert a["vcp"]["rank"]["rank"] == 1 and data["strategies"]["vcp"]["name"] == "Momentum-utbrott (VCP)"
    assert validate_swing(data, inst).errors == []
    lv["stop"] = lv["stop"] - 1
    assert any("stop" in e for e in validate_swing(data, inst).errors)
    lv["stop"] = lv["stop"] + 1
    # Status i dag: triggernivån är pivoten.
    now = datetime.combine(today, datetime.min.time(), TZ).replace(hour=9, minute=20)
    tb = Bar(today, lv["pivot"] + 0.5, lv["pivot"] + 0.5, lv["pivot"] + 1, lv["stop"] + 1, 500.0, lv["pivot"])

    def fetch(tickers, start, end_):
        return {**bars, "ALFA.ST": bars["ALFA.ST"] + [tb]}

    check = bollinger.check(today, now, inst, fetch, data, intraday=lambda t, d: None)
    rows = check["vcp"]["rows"]
    assert [r["ticker"] for r in rows] == ["ALFA"] and rows[0]["today"]["trigger"] == round(lv["pivot"], 4)
    assert rows[0]["today"]["status"] == "triggered" and bollinger.validate(check) == []


def test_historiken_har_strategin():
    import swing_history
    from prices import INDEX_TICKER

    end = date(2026, 11, 6)
    alfa = vcp_closes()
    n = len(alfa)
    bars = {"ALFA.ST": series(end, alfa), "BETA.ST": series(end, [100.0] * n), "GAMMA.ST": series(end, [90.0] * n),
            "DELTA.ST": series(end, [80.0] * n), INDEX_TICKER: series(end, [1000.0 + j for j in range(n)])}
    inst = {t: Instrument(name, (), t, f"{t}.ST", "Stockholm") for t, name in
            [("ALFA", "Alfaverken"), ("BETA", "Betabolaget"), ("GAMMA", "Gammagruppen"), ("DELTA", "Deltahem")]}
    h = swing_history.vcp_history(bars, inst)
    assert set(h["labels"]) == set(vcp.LABELS) and h["all"]["cases"] >= 1
    assert set(h["forward"]["all"]) == {"5", "20", "60"} and "överlevnadsbias" in h["note"]
