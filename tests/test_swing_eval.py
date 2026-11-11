"""Tester för den dagliga Swing-utvärderingen (scripts/swing_eval.py), med påhittade bolag och kurser."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import swing_eval as se  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 11, 20, 8, 15, tzinfo=TZ)


def company(ticker, name, labels=(), sector="industri", **over):
    c = {"ticker": ticker, "name": name, "sector": sector, "labels": list(labels), "volume_ratio": 1.0, "rsi14": 50.0,
         "days_since_report": None, "report_published": None,
         "sr": {"support": {"dist_pct": 1.0}, "resistance": {"dist_pct": 6.0}}}
    c.update(over)
    return c


def pullback(ref=100.0, stop=98.0, criteria=None):
    return {"state": {"close": ref}, "levels": {"ref": ref, "stop": stop},
            "rank": {"criteria": criteria or {"stodzon": True, "vandsignal": False}}}


def swing_file(as_of="2026-11-10", date_="2026-11-11", companies=None):
    return {
        "date": date_, "as_of": as_of,
        "labels": {"rekyl-i-trend": "Rekyl i trend", "utbrott": "Utbrott"},
        "strategies": {"pullback": {"name": "Pullback i trend", "labels": ["rekyl-i-trend"]}},
        "ranking_criteria": {"stodzon": "Stöd nära", "vandsignal": "Vändsignal"},
        "companies": companies if companies is not None else [
            company("ALFA", "Alfaverken", ["rekyl-i-trend"], pullback=pullback(), momentum={"rsi14": 72.0, "stoch_k": 85.0, "stoch_d": 70.0}),
            company("BETA", "Betabolaget", ["utbrott"]),  # varken kandidat eller stark
            company("GAMMA", "Gammagruppen", [], sector="bank"),  # stark signal (insyn)
        ],
    }


SIGNALS = {"week": "2026-W45", "period": {"start": "2026-11-02", "end": "2026-11-08"},
           "companies": [{"ticker": "GAMMA", "signals": ["insyn"]}, {"ticker": "BETA", "signals": ["omsättning"]},
                         {"ticker": "DELTA", "signals": ["stor-rörelse", "blankning"]}]}
YAHOO = {"ALFA": "ALFA.ST", "BETA": "BETA.ST", "GAMMA": "GAMMA.ST"}


def test_starka_signaler_som_fliken_signaler():
    s = se.strong_signals(SIGNALS)
    assert set(s) == {"GAMMA"}  # en omsättningssignal räcker inte; blankningen räknas inte
    two = {"companies": [{"ticker": "EPS", "signals": ["stor-rörelse", "omsättning"]}]}
    assert set(se.strong_signals(two)) == {"EPS"}
    assert se.strong_signals(two, reported={"EPS"}) == {}  # rapporterat: stor rörelse räknas inte


def test_ogonblicksbild_sparar_alla_kandidater_och_starka():
    snap = se.snapshot(swing_file(), SIGNALS, YAHOO, {"industri": "Industri", "bank": "Banker"}, NOW)
    by = {e["ticker"]: e for e in snap["entries"]}
    assert set(by) == {"ALFA", "GAMMA"} and snap["day"] == "2026-11-10"
    a = by["ALFA"]
    assert a["strategies"] == ["pullback"] and a["r"] == 2.0 and a["criteria"] == {"pullback": ["stodzon"]}
    assert a["support_r"] == 0.5 and a["resistance_r"] == 3.0  # 1 % och 6 % av 100 i R (2)
    assert {"strategi:pullback", "etikett:rekyl-i-trend", "kriterium:pullback:stodzon", "rsi:hog", "stoch:hog",
            "stod:nara", "motstand:langt", "sektor:industri"} <= set(a["features"])
    g = by["GAMMA"]
    assert g["strong"] and g["r"] is None and g["signals"] == ["insyn"] and "signal:insyn" in g["features"]
    assert snap["feature_texts"]["kriterium:pullback:stodzon"] == "Kriterium (Pullback i trend): Stöd nära"
    for text in snap["feature_texts"].values():
        assert not any(w in text.lower() for w in ("köp", "sälj", "kursmål"))


def bars_for(start, closes, vols=None, highs=None):
    out, d, n = [], start, 0
    while n < len(closes):
        if d.weekday() < 5:
            c = closes[n]
            h = (highs or {}).get(n, c)
            out.append(Bar(d, c, c, h, c, (vols or {}).get(n, 1000.0), c))
            n += 1
        d += timedelta(days=1)
    return out


def test_uppfoljning_efter_1_3_5_dagar():
    snap = se.snapshot(swing_file(), SIGNALS, YAHOO, {}, NOW)
    start = date(2026, 10, 13)  # 20 handelsdagar före 2026-11-10
    n0 = 20
    alfa = [100.0] * (n0 + 1) + [101.0, 102.0, 103.0, 104.0, 105.0]
    idx = [1000.0] * (n0 + 1) + [1005.0, 1010.0, 1010.0, 1010.0, 1020.0]
    bars = {"ALFA.ST": bars_for(start, alfa, vols={n0 + 1: 2000.0}, highs={n0 + 2: 104.0}),
            "GAMMA.ST": bars_for(start, [50.0] * (n0 + 2)), INDEX_TICKER: bars_for(start, idx)}
    assert bars["ALFA.ST"][n0].day == date(2026, 11, 10)
    added = se.follow_up(snap, bars)
    a = next(e for e in snap["entries"] if e["ticker"] == "ALFA")
    assert a["follow"]["1"] == {"day": "2026-11-11", "move_pct": 1.0, "vs_index_pp": 0.5, "max_high_r": 0.5,
                                "volume_ratio": 2.0}
    assert a["follow"]["3"]["max_high_r"] == 2.0 and a["follow"]["3"]["vs_index_pp"] == 2.0
    assert a["follow"]["5"]["move_pct"] == 5.0 and a["follow"]["5"]["vs_index_pp"] == 3.0
    g = next(e for e in snap["entries"] if e["ticker"] == "GAMMA")
    assert set(g["follow"]) == {"1"} and g["follow"]["1"]["max_high_r"] is None  # utan R; bara en dag till
    assert added == 6 and set(a["follow"]) == {"1", "2", "3", "4", "5"}  # dag 1–5 för ALFA, dag 1 för GAMMA
    assert se.follow_up(snap, bars) == 0  # redan ifyllt ändras inte


def rows(n, day="2026-11-10"):
    """n fall där kännetecknet "x" finns hos de första 20 % (störst rörelse) och "y" hos alla."""
    out = []
    for k in range(n):
        f = ["y"] + (["x"] if k < n // 5 else [])
        out.append({"name": f"Bolag {k:03d}", "day": day, "features": f,
                    "follow": {"3": {"vs_index_pp": float(n - k)}, "5": {"vs_index_pp": float(n - k)}}})
    return out


def test_monster_andelar_intervall_och_veckor():
    p = se.patterns(rows(50), "3", {"x": "Kännetecken X", "y": "Kännetecken Y"})
    assert p["cases"] == 50 and p["top_n"] == 10
    x = next(f for f in p["features"] if f["feature"] == "x")
    assert x["top"]["k"] == 10 and x["top"]["share"] == 1.0 and x["other"]["share"] == 0.0 and x["diff_pp"] == 100.0
    assert x["top"]["ci"][0] > 0.6 and x["cases"] == 10
    y = next(f for f in p["features"] if f["feature"] == "y")
    assert y["diff_pp"] == 0.0 and y["weeks_held"] == 0
    # Fyra veckor i rad där x är vanligare bland de 20 %: markerat.
    four = [r for w, d in enumerate(["2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]) for r in rows(20, d)]
    x4 = next(f for f in se.patterns(four, "5", {})["features"] if f["feature"] == "x")
    assert x4["weeks_held"] == 4 and x4["held"]
    three = [r for d in ["2026-10-26", "2026-11-02", "2026-11-09"] for r in rows(20, d)]
    assert not next(f for f in se.patterns(three, "5", {})["features"] if f["feature"] == "x")["held"]
    assert se.wilson(0, 0) is None and se.wilson(5, 10)[0] < 0.5 < se.wilson(5, 10)[1]


def test_sammanstallning_topp_10_efter_1_dag(tmp_path):
    entries = [{"ticker": f"T{k}", "name": f"Bolag {k}", "sector": "industri", "strategies": [], "strong": True,
                "signals": ["insyn"], "labels": [], "criteria": {}, "volume_ratio": 1.0, "rsi14": 50.0, "stoch_k": None,
                "stoch_d": None, "r": None, "support_r": None, "resistance_r": None, "report_5d": False,
                "features": ["stark"], "follow": {"1": {"day": "2026-11-11", "move_pct": float(k), "vs_index_pp": float(k),
                                                         "max_high_r": None, "volume_ratio": 1.0}}} for k in range(15)]
    snaps = [{"day": "2026-11-10", "feature_texts": {"stark": "Starka signaler"}, "entries": entries},
             {"day": "2026-11-11", "feature_texts": {}, "entries": []}]
    s = se.summary(snaps, NOW)
    assert s["top"]["day"] == "2026-11-10" and s["top"]["followed_day"] == "2026-11-11" and s["top"]["cases"] == 15
    assert [r["ticker"] for r in s["top"]["rows"]] == [f"T{k}" for k in range(14, 4, -1)]
    # Sparas bara när något ändrats (inte bara tiden).
    assert len(se.save(snaps, s, tmp_path)) == 3
    assert se.save(snaps, {**s, "generated_at": "senare"}, tmp_path) == []


def test_update_skriver_inte_over_befintlig_ogonblicksbild(tmp_path):
    sig = tmp_path / "signals"
    sig.mkdir()
    (sig / "2026-W45.json").write_text(json.dumps(SIGNALS), encoding="utf-8")
    inst = {t: Instrument(n, (), t, f"{t}.ST", "Stockholm") for t, n in
            [("ALFA", "Alfaverken"), ("BETA", "Betabolaget"), ("GAMMA", "Gammagruppen")]}
    snaps, summ, changed = se.update([swing_file()], {}, inst, NOW, tmp_path / "eval", sig)
    assert len(snaps) == 1 and snaps[0]["signals_week"] == "2026-W45" and changed
    other = swing_file(companies=[])
    snaps2, _, _ = se.update([other], {}, inst, NOW, tmp_path / "eval", sig)
    assert len(snaps2[0]["entries"]) == 2  # den första bilden står kvar


def summ_for_note():
    return {"generated_at": "2026-11-16T08:15:00+01:00", "period": {"start": "2026-10-29", "end": "2026-11-13"},
            "snapshots": 12, "entries": 640, "feature_texts": {"rsi:hog": "RSI(14) minst 70"},
            "patterns": {"3": {"cases": 600, "features": [{"feature": "rsi:hog", "cases": 80, "diff_pp": 7.5,
                                                           "top": {"k": 24, "n": 120, "share": 0.2, "ci": [0.14, 0.28]},
                                                           "other": {"k": 56, "n": 480, "share": 0.1167, "ci": [0.09, 0.15]},
                                                           "weeks_held": 2, "held": False}]},
                         "5": {"cases": 0, "features": []}}}


def test_kontroll_av_claudes_text():
    text = ("Underlaget omfattar 640 fall från 12 morgnar. Efter 3 dagar hade 20 % av de 120 fallen bland de 20 % "
            "bästa RSI(14) minst 70, mot 11,7 % bland övriga. Intervallen är breda, 14–28 % mot 9–15 %. Mönstret har "
            "hållit 2 veckor i rad, inget har hållit 4 veckor än. Det är för tidigt att säga något säkert.")
    note = {"week": "2026-W47", "label": "Claudes tolkning", "summary_generated_at": "2026-11-16T08:15:00+01:00",
            "text": text}
    assert se.check_note(note, summ_for_note(), "2026-W47") == []
    bad = {**note, "text": text.replace("640 fall", "641 fall")}
    assert any("641" in e for e in se.check_note(bad, summ_for_note(), "2026-W47"))
    short = {**note, "text": "Underlaget omfattar 640 fall. Det är tidigt."}
    assert any("meningar" in e for e in se.check_note(short, summ_for_note(), "2026-W47"))
    buy = {**note, "text": text + " Det kan vara läge att köpa."}
    assert any("uppmaning" in e for e in se.check_note(buy, summ_for_note(), "2026-W47"))
    assert any("label" in e for e in se.check_note({**note, "label": "Fakta"}, summ_for_note(), "2026-W47"))


def test_behover_text(tmp_path):
    assert not se.needs_note(date(2026, 11, 16), tmp_path)  # inget underlag
    (tmp_path / "summary.json").write_text(json.dumps(summ_for_note()), encoding="utf-8")
    assert se.needs_note(date(2026, 11, 16), tmp_path)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "2026-W47.json").write_text("{}", encoding="utf-8")
    assert not se.needs_note(date(2026, 11, 16), tmp_path)
    assert se.week_of(date(2026, 11, 16)) == "2026-W47"
    (tmp_path / "notes" / "2026-W47.json").unlink()
    assert not se.needs_note(date(2026, 11, 18), tmp_path)  # onsdag: bara veckans första handelsdag
    assert se.first_business_day(date(2026, 4, 8)) == date(2026, 4, 7)  # annandag påsk 6 april


def market_bars(n=260, index_up=True, flat_last=False):
    """Index och två påhittade bolag i varsin sektor, n handelsdagar fram till 2026-11-10."""
    end = date(2026, 11, 10)
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    idx = [1000.0 + (j if index_up else -j) for j in range(n)]
    alfa = [100.0 + j * 0.5 for j in range(n)]  # stiger hela tiden: över 50 MA, nytt 52v-högsta
    beta = [200.0 - j * 0.3 for j in range(n)]  # faller: under 50 MA, nytt 52v-lägsta
    vb = [1000.0] * (n - 1) + [3000.0]  # tredubbel omsättning sista dagen
    mk = lambda cs, vs=None: [Bar(dd, c, c, c, c, (vs or [1000.0] * n)[k], c) for k, (dd, c) in enumerate(zip(days, cs))]  # noqa: E731
    return {INDEX_TICKER: mk(idx), "ALFA.ST": mk(alfa), "BETA.ST": mk(beta, vb)}, end


def test_marknadslaget():
    bars, end = market_bars()
    m = se.market_state(bars, {"ALFA.ST": "industri", "BETA.ST": "bank"}, end, {"industri": "Industri", "bank": "Banker"})
    assert m["trend"] == "upp" and m["regime"] == "upp-lugn" and m["regime_text"] == "Upptrend, lugna svängningar"
    assert m["index"]["vs_ma50_pct"] > 0 and m["index"]["vs_ma200_pct"] > 0
    assert m["breadth"]["above_ma50_pct"] == 50.0 and m["breadth"]["companies"] == 2
    assert m["new_highs"] == 1 and m["new_lows"] == 1
    assert [x["sector"] for x in m["sectors"]] == ["industri", "bank"]  # industri starkast mot index
    bank = m["sectors"][1]
    assert bank["volume_ratio"] == 3.0 and bank["rs20_pp"] < 0
    assert [x["kind"] for x in m["signs"]] == ["sektorvolym"] and "Banker" in m["signs"][0]["text"]
    down, _ = market_bars(index_up=False)
    assert se.market_state(down, {}, end, {})["trend"] == "ned"
    assert se.market_state(bars, {}, date(2026, 1, 5), {}) is None  # för kort historik


def test_lyckat_fall():
    f = lambda k, pp, r=None: {str(k): {"vs_index_pp": pp, "max_high_r": r}}  # noqa: E731
    assert se.success_status({"follow": {**f(1, 1.0, 2.1)}}) == "lyckad"  # 2R redan dag 1
    assert se.success_status({"follow": {**f(1, 1.0), **f(3, 5.2)}}) == "lyckad"  # 5 pe över index
    assert se.success_status({"follow": {**f(1, 1.0, 1.0), **f(5, 4.9, 1.9)}}) == "ej"
    assert se.success_status({"follow": {**f(1, 1.0, 1.0)}}) is None  # inte avgjort än
    assert se.success_reasons({"follow": {**f(2, 6.0, 2.5)}}) == ["2R", "5 pe"]


def case(name, day, feats, status_ok, strategies=("pullback",)):
    fol = {"5": {"vs_index_pp": 6.0 if status_ok else 0.0, "max_high_r": None, "move_pct": 0, "day": day,
                 "volume_ratio": 1.0}}
    return {"ticker": name[:4].upper(), "name": name, "sector": "industri", "strategies": list(strategies),
            "strong": False, "signals": [], "labels": [], "criteria": {}, "volume_ratio": 1.0, "rsi14": 50.0,
            "stoch_k": None, "stoch_d": None, "r": None, "support_r": None, "resistance_r": None, "report_5d": False,
            "features": feats, "follow": fol, "regime": "upp-lugn"}


def test_larm_liknar_lyckade_fall_och_notis():
    feats = ["strategi:pullback", "etikett:rekyl-i-trend", "stod:nara", "sektor:industri"]
    old = [{"day": "2026-11-02", "entries": [case("Alfaverken", "2026-11-09", feats, True),
                                             case("Betabolaget", "2026-11-09", feats, True),
                                             case("Gammagruppen", "2026-11-09", feats + ["rsi:hog"], True),
                                             case("Deltahem", "2026-11-09", feats, False)]},
           {"day": "2026-09-01", "entries": [case("Epsilonbolaget", "2026-09-08", feats, True)]}]  # för gammalt
    today = case("Zetaverken", "2026-11-10", feats, False)
    today["follow"] = {}
    snaps = old + [{"day": "2026-11-10", "swing_date": "2026-11-11", "entries": [today],
                    "market": {"day": "2026-11-10", "regime": "upp-lugn", "regime_text": "Upptrend, lugna svängningar",
                               "index": {"vs_ma50_pct": 1.0, "vol20": 12.0}, "new_highs": 3, "new_lows": 1,
                               "breadth": {"above_ma50_pct": 64.3, "week_ago_pct": 50.2, "change_pp": 14.1},
                               "signs": [{"kind": "bredd", "text": "Andelen bolag över 50 MA steg 14,1 procentenheter"}]}}]
    s = se.summary(snaps, NOW)
    sim = s["alerts"]["similar"]
    assert [x["name"] for x in sim] == ["Zetaverken"] and sim[0]["matches"] == 3
    assert sim[0]["similar_cases"] == 4 and sim[0]["similar_successes"] == 3  # Deltahem liknar men lyckades inte
    assert len(s["successes"]) == 4 and s["successes"][0]["reasons"] == ["5 pe"]
    assert s["success_recent"]["all"]["cases"] == 4  # september-fallet ligger utanför 28 dagar
    assert s["cases_index"][0][-1] in ("lyckad", "ej")
    n = se.alerts_notice(s, "2026-11-11")
    assert "Liknar nyligen lyckade fall: Zetaverken (3 av 4 liknande lyckades)." in n["notice"]
    assert "Marknadstecken (gårdagens stängning): Andelen bolag över 50 MA steg" in n["notice"]
    assert se.alerts_notice(s, "2026-11-12") is None  # gäller inte dagens swingfil
    assert set(s["patterns"]) == {"2v", "6v", "lage", "alla"} and s["patterns"]["lage"]["regime"] == "upp-lugn"
    # De lyckade fallen och kännetecknen räknas som tal i texten, men inte cases_index.
    assert "14,1" in se.accepted_numbers(s)


def test_fonster_for_monster():
    rows_ = [{"day": d, "regime": rg} for d, rg in [("2026-10-01", "ned-orolig"), ("2026-10-20", "upp-lugn"),
                                                     ("2026-11-09", "upp-lugn")]]
    assert [r["day"] for r in se._window_rows(rows_, "2v", 14, "2026-11-10", None)] == ["2026-11-09"]
    assert len(se._window_rows(rows_, "6v", 42, "2026-11-10", None)) == 3
    assert [r["day"] for r in se._window_rows(rows_, "lage", None, "2026-11-10", "ned-orolig")] == ["2026-10-01"]
    assert len(se._window_rows(rows_, "alla", None, "2026-11-10", None)) == 3
