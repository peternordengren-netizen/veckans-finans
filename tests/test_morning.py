"""Tester för scripts/morning.py och validate_morning (påhittade bolag och rådata)."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from morning import (  # noqa: E402
    build, insider_since, is_business_day, overnight_news, previous_business_day,
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
    assert "short" not in data  # blankning räknas inte sedan 2026-11-11
    assert validate_morning(data, INSTRUMENTS).errors == []
    # En äldre rapport med blankning validerar fortfarande, och en felräknad förändring fångas.
    old = {**data, "short": {"available": True, "error": False, "note": None, "from_as_of": "2026-10-15",
                             "to_as_of": "2026-10-16", "positions": [], "aggregate": [
                                 {"ticker": "BETA", "name": "Betabolaget", "start_pct": 0.5, "end_pct": 1.0, "change_pp": 0.5}]}}
    assert validate_morning(old, INSTRUMENTS).errors == []
    old["short"]["aggregate"][0]["change_pp"] = 0.4
    assert any("short.aggregate" in e for e in validate_morning(old, INSTRUMENTS).errors)

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


def test_morgonrapporten_har_ingen_blankning():
    """Blankning räknas inte sedan 2026-11-11 (FI tillåter inte automatisk hämtning)."""
    import morning

    assert not hasattr(morning, "short_since") and not hasattr(morning, "short_checked")


def test_ingen_handelsdag_ger_ingen_fil(tmp_path, monkeypatch, capsys):
    import morning

    monkeypatch.setattr(morning, "MORNING_DIR", tmp_path)
    assert morning.main(["--date", "2026-10-18", "--no-fetch"]) == 0
    assert "ingen handelsdag" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_ett_bolag_med_fel_stoppar_inte_morgonrapporten():
    """Ett bolag vars kurssignaler inte stämmer tas bort ur alla listor och sparas i "skipped" med orsaken."""
    import morning

    def fetch(_tickers, _start, _end):
        return {"^OMXSPI": _bars(1000.0, [100.0] * 29 + [101.0]), "ALFA.ST": _bars(3000.0, [100.0] * 29 + [110.0]),
                "BETA.ST": _bars(1000.0, [100.0] * 15 + [120.0] * 14 + [110.0])}

    data = build(MONDAY, NOW, INSTRUMENTS, [], {"events": []}, fetch)
    data["errors"] = []
    by = {c["ticker"]: c for c in data["price"]["companies"]}
    by["ALFA"]["signals"] = ["omsättning"]  # stämmer inte med värdena
    res = morning.validate_pruned(data, INSTRUMENTS)
    assert res.errors == []
    assert [s["ticker"] for s in data["skipped"]] == ["ALFA"] and "signalerna" in data["skipped"][0]["reason"]
    assert "ALFA" not in [c["ticker"] for c in data["price"]["companies"]]
    assert validate_morning(data, INSTRUMENTS).errors == []


def test_kvallens_bygge_for_nasta_handelsdag(tmp_path):
    """Fredag 18:00: kursdelen för måndagens morgonrapport och swinglistan byggs med fredagens stängning."""
    import morning

    friday = date(2026, 10, 16)

    def fetch(_tickers, _start, end):
        assert end >= friday  # fredagens stängning ingår
        return {"^OMXSPI": _bars(1000.0, [100.0] * 29 + [101.0]), "ALFA.ST": _bars(3000.0, [100.0] * 29 + [110.0]),
                "BETA.ST": _bars(1000.0, [100.0] * 15 + [120.0] * 14 + [110.0])}

    swung = []
    evening_dir = tmp_path / "kvall"
    code = morning.evening(friday, datetime(2026, 10, 16, 18, 0, tzinfo=TZ), fetch, evening_dir,
                           write_swing_fn=lambda d, *a: swung.append(d), instruments=INSTRUMENTS)
    assert code == 0 and swung == [MONDAY]  # swinglistan för måndagen
    saved = json.loads((evening_dir / "2026-10-19.json").read_text(encoding="utf-8"))
    assert saved["complete"] and saved["price"]["day"] == "2026-10-16"
    assert morning.load_evening(MONDAY, evening_dir)["day"] == "2026-10-16"  # morgonen tar den
    assert morning.load_evening(date(2026, 10, 20), evening_dir) is None
    # Före 17:45 eller på en helgdag: inget.
    assert morning.evening(friday, datetime(2026, 10, 16, 17, 0, tzinfo=TZ), fetch, evening_dir,
                           instruments=INSTRUMENTS) == 3
    assert morning.evening(date(2026, 10, 17), datetime(2026, 10, 17, 21, 0, tzinfo=TZ), fetch, evening_dir,
                           instruments=INSTRUMENTS) == 3


def test_kvallens_bygge_utan_dagens_kurser(tmp_path):
    """Saknas fredagens kurser (Yahoo har bara torsdagen) sparas kursdelen som ofullständig och Swing byggs inte."""
    import morning

    def fetch(_tickers, _start, _end):
        return {k: v[:-1] for k, v in {"^OMXSPI": _bars(1000.0, [100.0] * 30), "ALFA.ST": _bars(1000.0, [100.0] * 30),
                                       "BETA.ST": _bars(1000.0, [100.0] * 30)}.items()}

    swung = []
    code = morning.evening(date(2026, 10, 16), datetime(2026, 10, 16, 18, 0, tzinfo=TZ), fetch, tmp_path,
                           write_swing_fn=lambda d, *a: swung.append(d), instruments=INSTRUMENTS)
    assert code == 2 and swung == []
    assert morning.load_evening(MONDAY, tmp_path) is None  # morgonen hämtar själv


def test_morgonen_anvander_kvallens_kurser():
    data = build(MONDAY, NOW, INSTRUMENTS, [], {"events": []}, lambda *a: (_ for _ in ()).throw(AssertionError("hämtning")),
                 price={"day": "2026-10-16", "expected_day": "2026-10-16", "coverage": {"with_day": 2, "with_expected": 2,
                                                                                          "of": 2},
                        "index_change_pct": 1.0, "thresholds": {"volume_ratio": 2.0, "near_52w_pct": 2.0}, "companies": []})
    assert data["price"]["day"] == "2026-10-16"  # ingen kurshämtning på morgonen


def test_rotationer_bygger_och_validerar(tmp_path):
    """Vyn Rotationer med påhittade kurser: sektorernas relativa styrka, nivåserier och topplistan."""
    import rotation

    days = []
    d = date(2025, 10, 1)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    mk = lambda cs, v=1000.0: [Bar(dd, c, c, c, c, v, c) for dd, c in zip(days, cs)]  # noqa: E731
    flat = [100.0] * 300
    up = [100.0] * 280 + [100.0 + k for k in range(1, 21)]  # +20 % sista 20 dagarna
    bars = {"^OMXSPI": mk(flat), "ALFA.ST": mk(up), "BETA.ST": mk(flat), "SPY": mk(flat), "XLK": mk(up),
            "BZ=F": mk([80.0 + (k % 7) * 0.3 for k in range(299)] + [90.0]), "HG=F": mk([5.0] * 300)}
    insts = {"ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", sektor="industri"),
             "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", sektor="bank")}
    rb = {"SEGVB10YC": [(dd, 2.5 + (k % 5) * 0.01) for k, dd in enumerate(days)]}
    data = rotation.build(days[-1], NOW, bars, insts, {"industri": "Industri", "bank": "Banker"}, rb, MONDAY)
    by = {i["key"]: i for i in data["items"]}
    assert by["se:industri"]["rs20_pp"] > 0 and by["se:bank"]["rs20_pp"] == 0
    assert by["us:XLK"]["rs5_pp"] > 0 and by["BZ=F"]["chg5"] > 0 and by["SEGVB10YC"]["unit"] == "bp"
    assert "HG=F" in by and by["HG=F"]["z5"] is None  # oförändrad: ingen spridning att jämföra med
    assert rotation.validate(data) == [] and data["top"][0] == max(
        (i for i in data["items"] if i["z5"] is not None), key=lambda i: abs(i["z5"]))["key"]
    data["top"] = list(reversed(data["top"]))
    assert rotation.validate(data)
