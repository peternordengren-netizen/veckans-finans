"""Tester för kursens läge mot toppnivåer (scripts/peaks.py) och uppföljningen i utvärderingen. Fiktiva bolag och
syntetiska kurser."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import peaks  # noqa: E402
import swing_eval  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = peaks.TZ


def bars(closes: list[float], start: date = date(2025, 1, 2)) -> list[Bar]:
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(day=d, close=c, adj_close=c, high=c, low=c, volume=1000.0))
        d += timedelta(days=1)
    return out


def flat_then(last: float, top: float = 100.0, n: int = 300) -> list[Bar]:
    """n dagar: toppen `top` dag 50, annars 90, sista dagen `last`."""
    c = [90.0] * n
    c[50] = top
    c[-1] = last
    return bars(c)


def kinds(fl):
    return {(f["kind"], f["status"]) for f in fl}


def test_52w_near_new_and_none():
    b = flat_then(98.0)
    f = peaks.flags(b, len(b) - 1)
    assert kinds(f) == {("52v", "nära")} and f[0]["dist_pct"] == -2.0 and f[0]["level"] == 100.0
    assert f[0]["level_day"] == b[50].day.isoformat()
    b = flat_then(101.0)
    f = peaks.flags(b, len(b) - 1)
    assert kinds(f) == {("52v", "ny")} and f[0]["dist_pct"] == 1.0
    assert peaks.flags(flat_then(96.9), 299) == []  # mer än 3 % under
    assert kinds(peaks.flags(flat_then(97.0), 299)) == {("52v", "nära")}  # exakt 3 % under räknas
    # Lika med toppen är inte en ny topp, men nära (0 %).
    assert kinds(peaks.flags(flat_then(100.0), 299)) == {("52v", "nära")}


def test_52w_window_and_short_history():
    # Toppen ligger mer än 52 veckor före dagen: räknas inte.
    c = [90.0] * 300
    c[5] = 100.0
    c[-1] = 98.0
    b = bars(c)
    assert (b[-1].day - b[5].day).days > peaks.WEEKS52_DAYS
    f = peaks.flags(b, len(b) - 1)
    assert kinds(f) == {("52v", "ny")} and f[0]["level"] == 90.0  # 52v-högsta = 90, inte den äldre toppen 100
    # För kort historik: ingen 52v-flagga.
    assert peaks.flags(flat_then(101.0, n=120), 119) == []


def rec_for(b: list[Bar], upto: int) -> dict:
    return peaks.record_of(b[:upto + 1])


def test_record_from_weekly_fetch_and_daily_bars():
    c = [90.0] * 300
    c[10] = 150.0  # rekordet, utanför de 52 veckorna
    c[-1] = 147.0
    b = bars(c)
    rec = {"record": 150.0, "record_day": b[10].day.isoformat(), "prev_record": 120.0, "prev_record_day": "2020-01-02",
           "first_day": "2000-01-03", "last_day": b[-6].day.isoformat(), "last_close": 90.0}
    f = peaks.flags(b, len(b) - 1, rec)
    assert kinds(f) == {("52v", "ny"), ("rekord", "nära")}
    r = next(x for x in f if x["kind"] == "rekord")
    assert r["dist_pct"] == -2.0 and r["level"] == 150.0
    # En högre stängning i dagsdatan efter hämtningen höjer rekordnivån (och 52v-högsta): ingen flagga vid 147.
    c2 = list(c)
    c2[-3] = 160.0
    b2 = bars(c2)
    assert peaks.prior_record(b2, len(b2) - 1, rec) == (160.0, b2[-3].day)
    assert peaks.flags(b2, len(b2) - 1, rec) == []


def test_record_split_rescaled():
    # Split 1:2 efter hämtningen: Yahoo halverar historiken; den sparade nivån skalas om.
    c = [45.0] * 300
    c[-1] = 74.0
    b = bars(c)
    rec = {"record": 150.0, "record_day": "2020-01-02", "prev_record": 140.0, "prev_record_day": "2019-01-02",
           "first_day": "2000-01-03", "last_day": b[-6].day.isoformat(), "last_close": 90.0}
    r = next(x for x in peaks.flags(b, len(b) - 1, rec) if x["kind"] == "rekord")
    assert r["level"] == 75.0 and r["status"] == "nära"


def test_record_fetched_after_day():
    b = flat_then(101.0)
    day = b[-1].day
    # Hämtningen gjordes efter dagen och dagen själv blev rekordet: rekordet före dagen (prev_record) gäller.
    rec = {"record": 101.0, "record_day": day.isoformat(), "prev_record": 100.0,
           "prev_record_day": b[50].day.isoformat(), "first_day": b[0].day.isoformat(),
           "last_day": (day + timedelta(days=3)).isoformat(), "last_close": 99.0}
    r = next(x for x in peaks.flags(b, len(b) - 1, rec) if x["kind"] == "rekord")
    assert r["status"] == "ny" and r["dist_pct"] == 1.0
    # Rekorddagen efter dagen: rekordet före dagen är okänt, ingen rekordflagga.
    rec2 = {**rec, "record": 120.0, "record_day": (day + timedelta(days=1)).isoformat()}
    assert all(x["kind"] != "rekord" for x in peaks.flags(b, len(b) - 1, rec2))


def test_record_of_and_build_records():
    b = bars([10.0, 30.0, 20.0, 40.0, 35.0])
    r = peaks.record_of(b)
    assert r["record"] == 40.0 and r["record_day"] == b[3].day.isoformat() and r["prev_record"] == 30.0
    assert r["last_close"] == 35.0 and r["first_day"] == b[0].day.isoformat()
    inst = {"ALFA": Instrument(namn="Alfaverken", alias=(), ticker="ALFA", yahoo_ticker="ALFA.ST", bors="Large Cap",
                               sektor="industri"),
            "BETA": Instrument(namn="Betabolaget", alias=(), ticker="BETA", yahoo_ticker="BETA.ST", bors="Large Cap",
                               sektor="industri")}
    data = peaks.build_records({"ALFA.ST": b}, inst, datetime(2026, 1, 3, 9, tzinfo=TZ), {"BETA": r})
    assert set(data["records"]) == {"ALFA", "BETA"} and data["kept_from_before"] == ["BETA"]


def peer(ticker, followed, fl, same_dir):
    return {"ticker": ticker, "name": ticker, "own_news": False, "followed": followed, "peaks": fl,
            "follow": {"5": {"same_dir_pp": same_dir}}}


def test_followed_by_top_in_evaluation():
    near = [{"kind": "52v", "status": "nära", "dist_pct": -1.0, "level": 10.0, "level_day": "2026-01-02"}]
    snap = {"day": "2026-01-20", "events": [{"ticker": "ALFA", "class": None, "peers": [
        peer("BETA", True, near, -2.0), peer("GAMMA", True, [], 1.0), peer("DELTA", False, near, -1.0),
        {**peer("EPS", True, near, 3.0), "own_news": True}, {k: v for k, v in peer("ZETA", True, [], 4.0).items()
                                                           if k != "peaks"}]}]}
    out = swing_eval.events_outcomes([snap])["followed_by_top"]
    assert out["near_top"]["5"]["n"] == 1 and out["near_top"]["5"]["same_dir"] == 0
    assert out["other"]["5"]["n"] == 1 and out["other"]["5"]["same_dir"] == 1  # ZETA saknar flaggor: räknas inte
    assert swing_eval.near_top({"peaks": []}) is False and swing_eval.near_top({}) is None
