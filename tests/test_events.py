"""Tester för Händelser i sektorn (scripts/events.py) och kollegornas uppföljning (scripts/swing_eval.py).
Fiktiva bolag och syntetiska kurser."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import events  # noqa: E402
import swing_eval  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument  # noqa: E402

NAMES = {"ALFA": "Alfaverken", "BETA": "Betabolaget", "GAMMA": "Gammagruppen", "DELTA": "Deltaindustri",
         "EPS": "Epsilonfabriken", "ZETA": "Zetakraft", "ETA": "Etahandel"}
NOW = datetime(2026, 1, 20, 18, 30, tzinfo=events.TZ)


def inst() -> dict:
    return {t: Instrument(namn=n, alias=(), ticker=t, yahoo_ticker=f"{t}.ST", bors="Large Cap", sektor="industri")
            for t, n in NAMES.items()}


def business_days(n: int, start: date = date(2025, 1, 2)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_bars(n: int = 300, event_at: int | None = None, last: dict[str, float] | None = None) -> dict:
    """Kurser där Alfaverken, Betabolaget, Gammagruppen och Deltaindustri följer en gemensam faktor (Alfaverken och
    Betabolaget mest). På dag event_at (standard: sista dagen) ersätts dagsrörelserna med `last` (procent)."""
    rng = np.random.default_rng(7)
    days = business_days(n)
    event_at = n - 1 if event_at is None else event_at
    last = last or {}
    f = rng.normal(0, 0.012, n)
    rets = {
        INDEX_TICKER: rng.normal(0, 0.004, n),
        "ALFA": f + rng.normal(0, 0.003, n), "BETA": f + rng.normal(0, 0.004, n),
        "GAMMA": f + rng.normal(0, 0.008, n), "DELTA": f + rng.normal(0, 0.010, n),
        "EPS": rng.normal(0, 0.012, n), "ZETA": rng.normal(0, 0.012, n), "ETA": rng.normal(0, 0.012, n),
    }
    rets[INDEX_TICKER][event_at] = 0.0
    for t, pct in last.items():
        rets[t][event_at] = pct / 100
    out = {}
    for t, r in rets.items():
        c, bars = 100.0, []
        for i, d in enumerate(days):
            c = c * (1 + r[i]) if i else c
            bars.append(Bar(day=d, close=c, adj_close=c, high=c * 1.004, low=c * 0.996, volume=10000.0))
        out[t if t == INDEX_TICKER else f"{t}.ST"] = bars
    return out


EVENT_DAY_MOVES = {"ALFA": 15.0, "BETA": 4.0, "GAMMA": 3.0, "DELTA": -3.0, "EPS": 0.5, "ZETA": 0.0, "ETA": -0.5}
NEWS = {"ALFA": [{"published": "2026-01-20T08:00:00+01:00", "title": "Alfaverken får en stor order",
                  "url": "https://example.com/alfa", "source": "MFN", "raw_file": None}],
        "GAMMA": [{"published": "2026-01-20T09:00:00+01:00", "title": "Gammagruppen byter vd",
                   "url": "https://example.com/gamma", "source": "MFN", "raw_file": None}]}


def detect(bars, news=NEWS, reporting=frozenset()):
    days = [b.day for b in bars[INDEX_TICKER]]
    return events.detect(days[-1], days[-2], bars, inst(), news, set(reporting))


def test_event_with_news_and_peers():
    bars = make_bars(last=EVENT_DAY_MOVES)
    evs = detect(bars)
    assert [e["ticker"] for e in evs] == ["ALFA"]
    e = evs[0]
    assert e["direction"] == "upp" and e["atr_mult"] >= events.ATR_MULT and not e["report"]
    peers = {p["ticker"]: p for p in e["peers"]}
    # Bara de tre som följer den gemensamma faktorn är kollegor; övriga korrelerar inte (under 0,4).
    assert list(peers) == ["BETA", "GAMMA", "DELTA"]  # högst korrelation först
    assert all(p["same_sector"] and p["corr"] >= events.PEER_SECTOR_CORR for p in peers.values())
    assert peers["BETA"]["followed"] and not peers["BETA"]["own_news"]
    # Gammagruppen rörde sig åt samma håll men har ett eget pressmeddelande: följde inte med.
    assert peers["GAMMA"]["own_news"] and not peers["GAMMA"]["followed"]
    assert not peers["DELTA"]["followed"]  # åt andra hållet
    # Toppflaggorna (scripts/peaks.py) finns vid händelsebolaget och kollegorna; utan rekordnivåer bara 52v-flaggor.
    assert isinstance(e["peaks"], list) and all(isinstance(p["peaks"], list) for p in peers.values())
    assert all(f["kind"] == "52v" for f in e["peaks"] + [f for p in peers.values() for f in p["peaks"]])
    assert events.validate({"events": evs}) == []


def test_no_event_without_news_or_report():
    bars = make_bars(last=EVENT_DAY_MOVES)
    assert detect(bars, news={}) == []
    evs = detect(bars, news={}, reporting={"ALFA"})
    assert [e["ticker"] for e in evs] == ["ALFA"] and evs[0]["report"]


def test_peer_rule_sector_and_correlation():
    # Korrelation 0,4–0,55 räcker bara i samma sektor; 0,55 räcker oavsett sektor; högst PEER_N.
    rng = np.random.default_rng(1)
    f = rng.normal(0, 1, 260)

    def mix(c):  # serie med korrelation ungefär c mot f
        return c * f + np.sqrt(1 - c * c) * rng.normal(0, 1, 260)

    tickers = ["A", "SAMMA45", "ANNAN45", "ANNAN70", "SAMMA20", *[f"H{i}" for i in range(6)]]
    m = np.vstack([f, mix(0.45), mix(0.45), mix(0.7), mix(0.2), *[mix(0.9) for _ in range(6)]])
    sectors = {"A": "bank", "SAMMA45": "bank", "SAMMA20": "bank"}
    got = dict(events.top_peers("A", tickers[:5], m[:5], sectors, min_overlap=200))
    assert set(got) == {"SAMMA45", "ANNAN70"}
    assert events.top_peers("A", ["A", "ANNAN45"], m[[0, 2]], sectors, min_overlap=200) == []
    many = events.top_peers("A", tickers, m, sectors, min_overlap=200)
    assert len(many) == events.PEER_N and all(t.startswith("H") for t, _ in many)
    assert [c for _, c in many] == sorted((c for _, c in many), reverse=True)


def test_no_clear_peers_text():
    data = {"events": detect(make_bars(last=EVENT_DAY_MOVES))}
    data["events"][0]["peers"] = []
    assert events.validate(data) == []
    assert events.NO_PEERS_TEXT in events.notice_text(data)


def test_validate_catches_weak_peer():
    evs = detect(make_bars(last=EVENT_DAY_MOVES))
    evs[0]["peers"][0].update(corr=0.45, same_sector=False)
    assert any("kollegevillkoret" in x for x in events.validate({"events": evs}))


def test_small_move_is_no_event():
    bars = make_bars(last={"ALFA": 1.0})
    assert detect(bars) == []


def test_validate_catches_wrong_followed():
    evs = detect(make_bars(last=EVENT_DAY_MOVES))
    evs[0]["peers"][0]["followed"] = not evs[0]["peers"][0]["followed"]
    assert any("följde med" in x for x in events.validate({"events": evs}))


def judgement(cls="bolagsspecifik", reason="Meddelandet handlar om en order som bara gäller bolaget."):
    return {"ticker": "ALFA", "class": cls, "label": events.JUDGEMENT_LABEL, "reason": reason}


def test_check_judgements():
    data = {"date": "2026-01-21", "events": detect(make_bars(last=EVENT_DAY_MOVES))}
    assert events.check_judgements(data, {"date": "2026-01-21", "judgements": [judgement()]}) == []
    assert events.check_judgements(data, {"date": "2026-01-21", "judgements": []})  # saknas
    bad = [judgement(cls="marknad"), judgement(reason="Två meningar. Här är den andra."),
           judgement(reason="Läget talar för att köpa aktien."), {**judgement(), "label": "tolkning"}]
    for j in bad:
        assert events.check_judgements(data, {"date": "2026-01-21", "judgements": [j]}), j
    events.merge_judgements(data, {"judgements": [judgement()]})
    assert data["events"][0]["judgement"]["class"] == "bolagsspecifik" and not events.needs_judgement(data)


def test_write_events_keeps_judgement(tmp_path, monkeypatch):
    bars = make_bars(last=EVENT_DAY_MOVES)
    days = [b.day for b in bars[INDEX_TICKER]]
    monkeypatch.setattr(events, "company_news", lambda *a, **k: NEWS)
    target = days[-1] + timedelta(days=1)
    out = events.write_events(target, days[-1], days[-2], NOW, bars, inst(), [tmp_path], tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert events.needs_judgement(data)
    events.merge_judgements(data, {"judgements": [judgement()]})
    events.write(data, tmp_path)
    events.write_events(target, days[-1], days[-2], NOW, bars, inst(), [tmp_path], tmp_path)
    again = events.load(target, tmp_path)
    assert again["events"][0]["judgement"]["class"] == "bolagsspecifik"


def test_notice_text():
    data = {"events": detect(make_bars(last=EVENT_DAY_MOVES))}
    text = events.notice_text(data)
    assert text.startswith("Händelser i sektorn") and "Alfaverken +" in text and "följde med: Betabolaget" in text
    assert "ATR" in text and "enligt Claude" not in text
    events.merge_judgements(data, {"judgements": [judgement()]})
    assert "bolagsspecifik enligt Claude" in events.notice_text(data)
    assert events.notice_text({"events": []}) == ""
    assert not events.FORBIDDEN.search(text)


def test_followup_by_class(tmp_path):
    n, at = 300, 290
    bars = make_bars(n=n, event_at=at, last=EVENT_DAY_MOVES)
    days = [b.day for b in bars[INDEX_TICKER]]
    evs = events.detect(days[at], days[at - 1], bars, inst(), NEWS, set())
    data = {"schema_version": 1, "date": (days[at] + timedelta(days=1)).isoformat(), "as_of": days[at].isoformat(),
            "events": evs}
    ev_dir = tmp_path / "events"
    events.write(data, ev_dir)
    yahoo = {t: i.yahoo_ticker for t, i in inst().items()}
    snaps, changed = swing_eval.update_events(bars, yahoo, tmp_path / "eval", ev_dir, keep_open=None)
    assert len(snaps) == 1 and changed and changed[0][1] == f"swing_eval/events/{days[at]}.json"
    beta = next(p for p in snaps[0]["events"][0]["peers"] if p["ticker"] == "BETA")
    assert set(beta["follow"]) == {"1", "3", "5"}
    rel = (bars["BETA.ST"][at + 3].adj_close / bars["BETA.ST"][at].adj_close
           - bars[INDEX_TICKER][at + 3].adj_close / bars[INDEX_TICKER][at].adj_close) * 100
    assert beta["follow"]["3"]["same_dir_pp"] == pytest.approx(rel, abs=0.01)  # händelsen uppåt: samma tecken
    out = swing_eval.events_outcomes(snaps)
    assert out["events"]["ej bedömd"] == 1 and out["by_class"]["ej bedömd"]["followed"]["5"]["n"] >= 1
    # Gammagruppen (eget pressmeddelande) räknas inte bland kollegorna.
    assert out["by_class"]["ej bedömd"]["all"]["1"]["n"] == 2  # Betabolaget och Deltaindustri
    # Claudes klass förs in när den kommer; ingen ny uppföljning ändrar redan ifyllda värden.
    events.merge_judgements(data, {"judgements": [judgement(cls="branschgemensam",
                                                           reason="Meddelandet beskriver efterfrågan i hela branschen.")]})
    events.write(data, ev_dir)
    snaps2, _ = swing_eval.update_events(bars, yahoo, tmp_path / "eval", ev_dir, keep_open=None)
    assert snaps2[0]["events"][0]["class"] == "branschgemensam"
    assert swing_eval.events_outcomes(snaps2)["events"]["branschgemensam"] == 1


def test_update_ignores_real_events_in_tests(tmp_path):
    # update() med en annan eval_dir läser inte data/events.
    _, summ, _ = swing_eval.update([], {}, inst(), NOW, tmp_path / "eval", tmp_path / "sig")
    assert summ["events_outcomes"]["events"] == {c: 0 for c in swing_eval.EVENT_CLASSES}


def test_history_rough_comparison():
    n = 300
    bars = make_bars(n=n, event_at=280, last=EVENT_DAY_MOVES)
    h = events.history(bars, inst(), years=1)
    assert h["rough"] and h["events"] >= 1 and h["peers"] >= 1
    assert set(h["groups"]) == {"all", "followed", "not_followed"}
    assert h["groups"]["all"]["5"]["n"] >= 1 and "nyhetsdata" in h["note"]
    json.dumps(h)  # inga numpy-typer i filen


def test_rounding_matches_page():
    # Samma avrundning som sidan (Intl.NumberFormat, signDisplay "exceptZero"): exakta halvor bort från noll.
    assert events.fmt1(12.25) == "+12,3" and events.fmt1(-12.25) == "−12,3"
    assert events.fmt1(2.15) == "+2,2"  # kortaste decimalformen, som Intl (inte det binära 2,1499…)
    assert events.fmt1(-0.04) == "0,0" and events.fmt1(0.0) == "0,0"
    assert events.fmt1(3.85, sign=False) == "3,9" and events.fmt1(4.44, sign=False) == "4,4"


# --- Negativa händelser ---------------------------------------------------------------------------------

DOWN_MOVES = {"ALFA": -15.0, "BETA": -4.0, "GAMMA": -3.0, "DELTA": 3.0, "EPS": 0.5, "ZETA": 0.0, "ETA": -0.5}


def test_negative_events_first():
    # Alfaverken föll, Zetakraft steg kraftigt (båda med pressmeddelande): den negativa händelsen står först.
    bars = make_bars(last={**DOWN_MOVES, "ZETA": 20.0})
    news = {**NEWS, "ZETA": [{**NEWS["ALFA"][0], "title": "Zetakraft får en stor order", "url": "https://example.com/z"}]}
    evs = detect(bars, news=news)
    assert [e["direction"] for e in evs] == ["ned", "upp"] and evs[0]["ticker"] == "ALFA"
    assert abs(evs[1]["vs_index_pp"]) > abs(evs[0]["vs_index_pp"])  # trots större rörelse uppåt
    text = events.notice_text({"events": evs})
    assert text.index("Alfaverken") < text.index("Zetakraft") and "föll med: Betabolaget" in text
    # Kollegor men ingen som föll med: sägs uttryckligen för negativa händelser.
    for p in evs[0]["peers"]:
        p["followed"] = False
    assert "Alfaverken" in events.notice_text({"events": evs}) and "ingen kollega föll med" in events.notice_text({"events": evs})


def test_news_type_for_negative_events():
    data = {"date": "2026-01-21", "events": detect(make_bars(last=DOWN_MOVES))}
    j = judgement(reason="Bolaget sänker sin prognos för året.")
    assert any("news_type" in x for x in events.check_judgements(data, {"date": "2026-01-21", "judgements": [j]}))
    for bad in ("prognos", None):
        assert events.check_judgements(data, {"date": "2026-01-21", "judgements": [{**j, "news_type": bad}]})
    ok = {**j, "news_type": "sänkt prognos"}
    assert events.check_judgements(data, {"date": "2026-01-21", "judgements": [ok]}) == []
    events.merge_judgements(data, {"judgements": [ok]})
    assert data["events"][0]["judgement"]["news_type"] == "sänkt prognos" and events.validate(data) == []
    assert "sänkt prognos, bolagsspecifik enligt Claude" in events.notice_text(data)
    # Positiva händelser har ingen typ av nyhet.
    up = {"date": "2026-01-21", "events": detect(make_bars(last=EVENT_DAY_MOVES))}
    assert any("bara för negativa" in x for x in
               events.check_judgements(up, {"date": "2026-01-21", "judgements": [{**judgement(), "news_type": "annat"}]}))


def test_intraday_negative_only_with_news_until(tmp_path, monkeypatch):
    bars = make_bars(last=DOWN_MOVES)
    today = bars[INDEX_TICKER][-1].day
    now = datetime.combine(today, datetime.min.time().replace(hour=13, minute=20), tzinfo=events.TZ)
    seen = {}

    def fake_news(raw_dirs, instruments, since, until):
        seen["window"] = (since, until)
        return NEWS

    monkeypatch.setattr(events, "company_news", fake_news)
    wk = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
    (tmp_path / wk).mkdir()
    runs = [{"time": f"{today}T09:17:00+01:00", "sources": {"mfn": {"status": "ok"}, "cision": {"status": "ok"}}},
            {"time": f"{today}T11:17:00+01:00", "sources": {"mfn": {"status": "error"}, "cision": {"status": "ok"}}}]
    (tmp_path / wk / "index.json").write_text(json.dumps({"items": [], "runs": runs}), encoding="utf-8")
    out = events.intraday(today, now, bars, inst(), raw_dirs=[tmp_path], calendar_dir=tmp_path)
    assert seen["window"][1] == now and seen["window"][0].hour == 17
    assert [e["ticker"] for e in out["events"]] == ["ALFA"] and out["events"][0]["direction"] == "ned"
    assert out["news_until"] == {"mfn": f"{today}T09:17+01:00", "cision": f"{today}T11:17+01:00"}
    # En hämtning efter kontrollen räknas inte (omkörning i efterhand).
    early = datetime.combine(today, datetime.min.time().replace(hour=10), tzinfo=events.TZ)
    assert events.news_fetched_until([tmp_path], today, early) == {"mfn": f"{today}T09:17+01:00",
                                                                     "cision": f"{today}T09:17+01:00"}
    assert out["notice"].startswith("LARM") and "föll med: Betabolaget" in out["notice"]
    assert "pressmeddelande 08:00" in out["notice"] and "MFN 09:17, Cision 11:17" in out["notice"]
    # En händelse uppåt larmar inte under dagen.
    up = events.intraday(today, now, make_bars(last=EVENT_DAY_MOVES), inst(), raw_dirs=[tmp_path], calendar_dir=tmp_path)
    assert up["events"] == [] and up["notice"] == ""
    # Utan dagens indexkurs: inga händelser.
    old = {k: v[:-1] for k, v in bars.items()}
    assert events.intraday(today, now, old, inst(), raw_dirs=[tmp_path], calendar_dir=tmp_path)["error"]


def test_history_negative_only():
    h = events.history(make_bars(n=300, event_at=280, last=DOWN_MOVES), inst(), years=1)
    neg = h["negative"]
    assert 1 <= neg["events"] <= h["events"] and neg["followed"] <= neg["peers"] <= h["peers"]
    assert set(neg["groups"]) == {"followed", "not_followed"}
    # Utan teckenvändning: samma tal som by_direction med omvänt tecken.
    f5, d5 = neg["groups"]["followed"]["5"], h["by_direction"]["ned"]["followed"]["5"]
    assert f5["n"] == d5["n"] and (f5["mean_pp"] is None or f5["mean_pp"] == pytest.approx(-d5["mean_pp"], abs=0.011))
    json.dumps(h)
