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
    assert len(peers) == events.PEER_N and next(iter(peers)) == "BETA"  # högst korrelation först
    assert peers["BETA"]["followed"] and not peers["BETA"]["own_news"]
    # Gammagruppen rörde sig åt samma håll men har ett eget pressmeddelande: följde inte med.
    assert peers["GAMMA"]["own_news"] and not peers["GAMMA"]["followed"]
    assert not peers["DELTA"]["followed"]  # åt andra hållet
    assert events.validate({"events": evs}) == []


def test_no_event_without_news_or_report():
    bars = make_bars(last=EVENT_DAY_MOVES)
    assert detect(bars, news={}) == []
    evs = detect(bars, news={}, reporting={"ALFA"})
    assert [e["ticker"] for e in evs] == ["ALFA"] and evs[0]["report"]


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
    assert out["by_class"]["ej bedömd"]["all"]["1"]["n"] == events.PEER_N - 1
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
