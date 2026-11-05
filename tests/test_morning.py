"""Tester för scripts/morning.py och validate_morning (påhittade bolag och rådata)."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from morning import (  # noqa: E402
    build, insider_since, is_business_day, overnight_news, previous_business_day, short_since,
)
from signals import Bar  # noqa: E402
from validate import Instrument, validate_morning  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
MONDAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 8, 10, tzinfo=TZ)

INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", mfn_slug="alfaverken", isin=("SE0000000011",)),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", isin=("SE0000000029",)),
}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def news_item(n, published, title, company="alfaverken", source="mfn", routine=False):
    return {"id": str(n), "source": source, "source_name": source.upper(), "url": f"https://mfn.example/{n}",
            "published": published, "title": title, "company": company, "routine": routine, "path": f"{source}/{n}.txt"}


def test_handelsdagar():
    assert previous_business_day(MONDAY) == date(2026, 10, 16)
    assert previous_business_day(date(2026, 12, 28)) == date(2026, 12, 23)  # jul
    assert not is_business_day(date(2026, 10, 18)) and not is_business_day(date(2026, 12, 24))


def test_nattens_nyheter_fonster_rutin_och_dubbletter(tmp_path):
    actions, local = tmp_path / "raw", tmp_path / "morning-raw"
    write_json(actions / "2026-W42" / "index.json", {"items": [
        news_item(1, "2026-10-16T17:00:00+02:00", "Före stängning"),  # före fönstret
        news_item(2, "2026-10-16T18:00:00+02:00", "Alfaverken vinner order"),
        news_item(3, "2026-10-17T09:00:00+02:00", "Kallelse till stämma", routine=True),
    ]})
    write_json(local / "2026-W43" / "index.json", {"items": [
        news_item(4, "2026-10-19T07:00:00+02:00", "Alfaverken delårsrapport"),
        news_item(2, "2026-10-16T18:00:00+02:00", "Alfaverken vinner order"),  # samma URL som i Actions-datan
        news_item(5, "2026-10-19T07:30:00+02:00", "Betabolaget förvärvar", company="betabolaget"),
    ]})
    since = datetime(2026, 10, 16, 17, 30, tzinfo=TZ)
    news = overnight_news([actions, local], INSTRUMENTS, since, NOW, reporting_today={"BETA"})
    assert [c["ticker"] for c in news] == ["BETA", "ALFA"]  # rapport i dag först
    assert news[0]["report_today"] is True
    assert [i["title"] for i in news[1]["items"]] == ["Alfaverken vinner order", "Alfaverken delårsrapport"]


def _insyn_row(isin, nature, volume, price, published):
    return {"nature": nature, "instrument_type": "Aktie", "share_program": False, "status": "Aktuell",
            "currency": "SEK", "isin": isin, "volume": volume, "price": price, "person": "P", "position": "VD",
            "closely_associated": "Nej", "transaction_date": published, "published_date": published,
            "venue": "NASDAQ STOCKHOLM AB"}


def test_insyn_sedan_forra_handelsdagen(tmp_path):
    raw = tmp_path / "raw"
    write_json(raw / "2026-W42" / "fi" / "insyn-2026-10-16.json",
               {"rows": [_insyn_row("SE0000000011", "Förvärv", 10_000, 50.0, "2026-10-16")]})
    write_json(raw / "2026-W42" / "fi" / "insyn-2026-10-15.json",  # före fönstret
               {"rows": [_insyn_row("SE0000000029", "Förvärv", 10_000, 50.0, "2026-10-15")]})
    days = ["2026-10-16", "2026-10-17", "2026-10-18"]
    ins = insider_since([raw], days, INSTRUMENTS)
    assert [c["ticker"] for c in ins["companies"]] == ["ALFA"]
    assert ins["companies"][0]["net_value_sek"] == 500_000
    assert ins["days_missing"] == ["2026-10-17", "2026-10-18"]


def _snap(as_of, current, aggregate):
    return {"as_of": as_of, "current": current, "aggregate": aggregate}


def test_blankning_mellan_ogonblicksbilder(tmp_path):
    raw = tmp_path / "raw"
    pos = lambda holder, isin, pct: {"holder": holder, "issuer": "x", "isin": isin, "pct": pct, "date": "2026-10-15"}  # noqa: E731
    write_json(raw / "2026-W42" / "fi" / "blankning-2026-10-15.json", _snap(
        "2026-10-15", [pos("Fond A", "SE0000000011", 0.6)],
        [{"issuer": "Alfaverken AB", "pct": 1.0}, {"issuer": "Betabolaget AB", "pct": 0.5}]))
    write_json(raw / "2026-W42" / "fi" / "blankning-2026-10-18.json", _snap(
        "2026-10-18", [pos("Fond B", "SE0000000029", 0.7)],
        [{"issuer": "Alfaverken AB", "pct": 1.1}, {"issuer": "Betabolaget AB", "pct": 1.0}]))
    s = short_since([raw], date(2026, 10, 15), date(2026, 10, 18), INSTRUMENTS)
    assert s["available"] and (s["from_as_of"], s["to_as_of"]) == ("2026-10-15", "2026-10-18")
    assert {(p["ticker"], p["holder"], p["change_pp"]) for p in s["positions"]} == {
        ("ALFA", "Fond A", -0.6), ("BETA", "Fond B", 0.7)}
    # Alfaverken +0,1 under tröskeln 0,2; Betabolaget +0,5.
    assert [(a["ticker"], a["change_pp"]) for a in s["aggregate"]] == [("BETA", 0.5)]
    same = short_since([raw], date(2026, 10, 18), date(2026, 10, 18), INSTRUMENTS)
    assert same["available"] is False


def _bars(last_volume, closes):
    start = date(2026, 9, 1)
    days = [start + timedelta(days=i) for i in range(60) if (start + timedelta(days=i)).weekday() < 5]
    days = [d for d in days if d <= date(2026, 10, 16)][-len(closes):]
    return [Bar(d, c, c, c, c, last_volume if i == len(days) - 1 else 1000.0) for i, (d, c) in enumerate(zip(days, closes))]


def test_hela_rapporten_validerar(tmp_path):
    raw = tmp_path / "raw"
    write_json(raw / "2026-W43" / "index.json", {"items": [news_item(1, "2026-10-19T07:00:00+02:00", "Alfaverken Q3")]})

    def fetch(_tickers, _start, _end):
        return {
            "^OMXSPI": _bars(1000.0, [100.0] * 29 + [101.0]),
            "ALFA.ST": _bars(3000.0, [100.0] * 29 + [110.0]),  # +10 %, 3× omsättning, 52v-högsta
            "BETA.ST": _bars(1000.0, [100.0] * 15 + [120.0] * 14 + [110.0]),  # mitt i intervallet
        }

    calendar = {"events": [{"date": "2026-10-19", "kind": "rapport", "ticker": "ALFA", "name": "Alfaverken",
                            "status": "yahoo", "yahoo_date": "2026-10-19", "mfn_date": None, "mfn_url": None}]}
    data = build(MONDAY, NOW, INSTRUMENTS, [raw], calendar, fetch)
    data["errors"] = []
    assert data["news_since"] == "2026-10-16T17:30:00+02:00"
    assert data["insider"]["days"] == ["2026-10-16", "2026-10-17", "2026-10-18"]
    assert data["price"]["day"] == "2026-10-16"
    [alfa] = data["price"]["companies"]
    assert (alfa["ticker"], alfa["signals"], alfa["relative_pp"]) == ("ALFA", ["omsättning", "52v-högsta"], 9.0)
    assert data["news"][0]["report_today"] is True
    assert validate_morning(data, INSTRUMENTS).errors == []

    data["price"]["companies"][0]["signals"] = ["omsättning"]
    data["news"][0]["items"][0]["published"] = "2026-10-15T09:00:00+02:00"
    errors = validate_morning(data, INSTRUMENTS).errors
    assert any("signalerna" in e for e in errors) and any("utanför" in e for e in errors)


def _bars_until(last_day, closes):
    """Vardagar fram till och med last_day."""
    days, d = [], last_day
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return [Bar(day, c, c, c, c, 1000.0) for day, c in zip(sorted(days), closes)]


def test_dagsdatans_datum_ar_det_de_flesta_bolagen_har():
    from signals import data_day

    fri, thu = date(2026, 10, 16), date(2026, 10, 15)
    bars = {f"B{i}": _bars_until(thu, [100.0] * 30) for i in range(10)}
    bars["B0"] = _bars_until(fri, [100.0] * 30)  # ett bolag har fredagen, nio har torsdagen som senaste
    assert data_day(bars, list(bars), MONDAY) == (thu, 10, 10)
    for i in range(1, 9):
        bars[f"B{i}"] = _bars_until(fri, [100.0] * 30)  # nio av tio har fredagen: 90 % räcker
    assert data_day(bars, list(bars), MONDAY) == (fri, 9, 10)
    assert data_day({}, ["X"], MONDAY) == (None, 0, 0)


def test_gardagens_kurser_saknas_hos_de_flesta_bolagen(tmp_path):
    """Yahoo 08:10: bara Alfaverken (och index) har fredagen. Kurserna gäller då torsdagen för alla bolag,
    med förra handelsdagen och täckningen i filen, och valideringen kräver retry "kurser"."""
    fri, thu = date(2026, 10, 16), date(2026, 10, 15)

    def fetch(_tickers, _start, _end):
        return {"^OMXSPI": _bars_until(fri, [100.0] * 30), "ALFA.ST": _bars_until(fri, [100.0] * 29 + [110.0]),
                "BETA.ST": _bars_until(thu, [100.0] * 30)}

    data = build(MONDAY, NOW, INSTRUMENTS, [tmp_path], None, fetch)
    p = data["price"]
    assert (p["day"], p["expected_day"]) == ("2026-10-15", "2026-10-16")
    assert p["coverage"] == {"with_day": 2, "with_expected": 1, "of": 2}
    data["errors"], data["retry"] = [], []
    assert any("retry saknar" in e for e in validate_morning(data, INSTRUMENTS).errors)
    data["retry"] = ["kurser"]
    assert validate_morning(data, INSTRUMENTS).errors == []


def test_bara_kurser_skriver_om_kursdelen(tmp_path, monkeypatch):
    """--bara-kurser (omförsöken till 09:10): kursdelen och felet skrivs om, övriga delar lämnas."""
    import morning

    fri, thu = date(2026, 10, 16), date(2026, 10, 15)
    stale = {"^OMXSPI": _bars_until(fri, [100.0] * 30), "ALFA.ST": _bars_until(fri, [100.0] * 30),
             "BETA.ST": _bars_until(thu, [100.0] * 30)}
    fresh = {**stale, "BETA.ST": _bars_until(fri, [100.0] * 30)}
    monkeypatch.setattr(morning, "MORNING_DIR", tmp_path)
    monkeypatch.setattr(morning, "load_instruments", lambda: INSTRUMENTS)
    swings = []
    monkeypatch.setattr(morning, "write_swing", lambda *a: swings.append(a[0]))
    data = build(MONDAY, NOW, INSTRUMENTS, [tmp_path], None, lambda *a: stale)
    data["errors"] = ["FI blankning: något annat"]
    data["retry"] = morning.retry_reasons(data, [], MONDAY)
    morning.add_price_error(data, MONDAY)
    assert data["retry"] == ["kurser"] and any(e.startswith("kurser saknas för 2026-10-16") for e in data["errors"])
    write_json(tmp_path / "2026-10-19.json", data)

    assert morning.refresh_prices(MONDAY, NOW, lambda *a: stale) == 2  # fortfarande torsdagen
    assert morning.refresh_prices(MONDAY, NOW, lambda *a: fresh) == 0
    out = json.loads((tmp_path / "2026-10-19.json").read_text(encoding="utf-8"))
    assert out["price"]["day"] == "2026-10-16" and out["retry"] == []
    assert out["errors"] == ["FI blankning: något annat"]  # bara kursfelet togs bort
    assert out["news_since"] == data["news_since"] and swings == [MONDAY, MONDAY]


def test_saknade_kurser_eller_natverk_gors_om():
    """Morgonrapporten är ofullständig (görs om vid nästa inloggning/upplåsning) om förra handelsdagens
    kurser saknas eller ingen källa gick att hämta."""
    from morning import FETCH_FAILED, retry_reasons

    monday = date(2026, 10, 26)  # förra handelsdagen fredag 23 okt
    data = lambda day: {"price": {"day": day}}  # noqa: E731
    assert retry_reasons(data("2026-10-23"), [], monday) == []
    assert retry_reasons(data("2026-10-22"), [], monday) == ["kurser"]  # Yahoo har inte fredagen än
    assert retry_reasons(data(None), [FETCH_FAILED], monday) == ["nätverk", "kurser"]
    assert retry_reasons(data("2026-10-23"), ["fi_fetch.py: fel vid hämtning från FI"], monday) == []


def test_saknad_blankningsbild_ar_fel_inte_noll_positioner():
    """Saknas registrets ögonblicksbild för förra handelsdagen (FI gav 404 från 29/10 2026) är det ett
    fel som visas som varning, inte "inga ändringar"."""
    from morning import short_checked

    base = {"available": True, "note": None, "from_as_of": "2026-10-29", "to_as_of": "2026-11-01",
            "positions": [{"x": 1}], "aggregate": []}
    friday = date(2026, 10, 30)
    assert short_checked(base, friday) == {**base, "error": False}  # söndagens bild räcker för fredagen
    stale = short_checked({**base, "available": False, "from_as_of": "2026-10-27", "to_as_of": "2026-10-27",
                           "positions": []}, friday)
    assert stale["error"] and not stale["available"] and stale["positions"] == []
    assert "Blankningsregistret saknas" in stale["note"] and "2026-10-27" in stale["note"] and "2026-10-30" in stale["note"]
    # Instruktionen: var filerna laddas ner och var de ska sparas.
    assert "data/fi_manual/" in stale["note"] and "Aktuella positioner" in stale["note"] and "--las-in" in stale["note"]
    none = short_checked({**base, "to_as_of": None, "from_as_of": None}, friday)
    assert none["error"] and "ingen ögonblicksbild" in none["note"]


def test_ingen_handelsdag_ger_ingen_fil(tmp_path, monkeypatch, capsys):
    import morning

    monkeypatch.setattr(morning, "MORNING_DIR", tmp_path)
    assert morning.main(["--date", "2026-10-18", "--no-fetch"]) == 0
    assert "ingen handelsdag" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
