"""Tester för eftermiddagsuppdateringens kursreaktion (scripts/afternoon.py) och dess validering
(påhittade bolag och kurser)."""

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import afternoon  # noqa: E402
from signals import Bar  # noqa: E402
from validate import Instrument, validate_afternoon  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
TODAY = date(2026, 10, 22)  # torsdag; förra handelsdagen 21 okt
NOW = datetime(2026, 10, 22, 17, 46, tzinfo=TZ)
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm"),
    "GAMMA": Instrument("Gammagruppen", (), "GAMMA", "GAMMA.OL", "Oslo"),
    "DELTA": Instrument("Deltahem", (), "DELTA", "DELTA.ST", "Stockholm"),
}
UPDATE = "2026-10-22T1745"


def row(ticker, published):
    return {"ticker": ticker, "name": INSTRUMENTS[ticker].namn, "published": published,
            "title": f"{INSTRUMENTS[ticker].namn} delårsrapport", "url": f"https://mfn.example/{ticker.lower()}"}


LISTS = {
    "2026-10-22": [row("DELTA", "2026-10-21T12:00:00+02:00"),  # före gårdagens stängning: reaktionen var i går
                   row("ALFA", "2026-10-22T07:00:00+02:00")],
    UPDATE: [row("GAMMA", "2026-10-22T12:00:00+02:00"),
             row("BETA", "2026-10-22T17:40:00+02:00")],  # efter stängning
}


def list_fn(scope, instruments, sector_kpis, results_dir=None):
    return LISTS[scope]


def bars(*closes):
    days = [date(2026, 10, 21), date(2026, 10, 22)][: len(closes)]
    return [Bar(d, c, c, c, c, 1000) for d, c in zip(days, closes)]


def fetch_today(tickers, start, end):
    return {"^OMXSPI": bars(100, 99), "ALFA.ST": bars(50, 53), "GAMMA.OL": bars(10, 9), "BETA.ST": bars(20, 21),
            "DELTA.ST": bars(30, 30)}


def results_dir(tmp_path):
    d = tmp_path / "results"
    (d / "morning" / "2026-10-22").mkdir(parents=True)
    (d / "updates" / UPDATE).mkdir(parents=True)
    return d


def test_kursreaktion_for_dagens_rapporterande_bolag(tmp_path):
    data = afternoon.build(TODAY, NOW, INSTRUMENTS, fetch_today, results_dir(tmp_path), list_fn)
    assert data["scopes"] == ["2026-10-22", UPDATE]
    assert data["price_day"] == "2026-10-22" and data["index"]["change_pct"] == -1.0
    by = {c["ticker"]: c for c in data["companies"]}
    assert "DELTA" not in by  # rapporten kom före förra handelsdagens stängning
    assert (by["ALFA"]["change_pct"], by["ALFA"]["relative_pp"], by["ALFA"]["close"]) == (6.0, 7.0, 53)
    assert (by["GAMMA"]["change_pct"], by["GAMMA"]["relative_pp"]) == (-10.0, None)  # inte i kronor: inget mot index
    assert by["BETA"]["after_close"] and by["BETA"]["change_pct"] is None and by["BETA"]["close"] is None
    assert [c["ticker"] for c in data["companies"]] == ["ALFA", "GAMMA", "BETA"]  # efter stängning sist
    assert validate_afternoon(data, "2026-10-22", INSTRUMENTS).errors == []


def test_validering_av_kursreaktionen(tmp_path):
    data = afternoon.build(TODAY, NOW, INSTRUMENTS, fetch_today, results_dir(tmp_path), list_fn)
    by = {c["ticker"]: c for c in data["companies"]}
    by["ALFA"]["relative_pp"] = 5.0
    by["BETA"]["change_pct"], by["BETA"]["close"] = 5.0, 21
    errors = validate_afternoon(data, "2026-10-22", INSTRUMENTS).errors
    assert any("mot index" in e for e in errors)
    assert any("efter stängning" in e for e in errors)
    assert validate_afternoon(data, "2026-10-23", INSTRUMENTS).errors  # fel datum mot filnamnet


def test_inga_kurser_i_dag_ger_ingen_reaktion(tmp_path):
    no_today = lambda t, s, e: {k: v[:1] for k, v in fetch_today(t, s, e).items()}  # noqa: E731
    data = afternoon.build(TODAY, NOW, INSTRUMENTS, no_today, results_dir(tmp_path), list_fn)
    assert data["price_day"] is None and data["index"]["change_pct"] is None
    assert all(c["change_pct"] is None and c["relative_pp"] is None for c in data["companies"])
    assert validate_afternoon(data, "2026-10-22", INSTRUMENTS).errors == []


def test_kursfel_stoppar_inte_uppdateringen(tmp_path):
    def broken(t, s, e):
        raise RuntimeError("Yahoo svarar inte")

    data = afternoon.build(TODAY, NOW, INSTRUMENTS, broken, results_dir(tmp_path), list_fn)
    assert data["errors"][0] == "kursdata: Yahoo svarar inte"
    assert data["retry"] == ["kurser", "nätverk"]
    assert validate_afternoon(data, "2026-10-22", INSTRUMENTS).errors == []


def test_analytikerlankar_ur_instruments(tmp_path):
    """Kolumnen "Analytiker": länkarna kommer ur instruments.csv (kontrollerade adresser), inget hämtas."""
    insts = {**INSTRUMENTS, "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm",
                                               placera_tagg="alfaverken", afv_aktie="alfaverken-ab")}
    data = afternoon.build(TODAY, NOW, insts, fetch_today, results_dir(tmp_path), list_fn)
    by = {c["ticker"]: c for c in data["companies"]}
    assert by["ALFA"]["links"] == {"placera": "https://www.placera.se/taggar/alfaverken",
                                   "affarsvarlden": "https://www.affarsvarlden.se/aktie/alfaverken-ab"}
    assert by["GAMMA"]["links"] == {"placera": None, "affarsvarlden": None}
    assert validate_afternoon(data, "2026-10-22", insts).errors == []
    by["ALFA"]["links"]["placera"] = "https://www.placera.se/taggar/annat"
    assert any("analytikerlänkarna" in e for e in validate_afternoon(data, "2026-10-22", insts).errors)


def test_saknade_kurser_ar_fel_och_gors_om(tmp_path):
    """Saknas dagens kurser (2026-10-30: körningen gick utan nätverk) är det ett fel och filen är
    ofullständig, så att nästa inloggning eller upplåsning försöker igen."""
    no_today = lambda t, s, e: {k: v[:1] for k, v in fetch_today(t, s, e).items()}  # noqa: E731
    data = afternoon.build(TODAY, NOW, INSTRUMENTS, no_today, results_dir(tmp_path), list_fn)
    assert data["retry"] == ["kurser"]
    assert data["errors"] == ["kurser saknas för 2026-10-22: Alfaverken, Gammagruppen, OMX Stockholm PI"]
    # Bara ett bolag utan kurs räcker; ett bolag som rapporterat efter stängning behöver ingen kurs.
    one = lambda t, s, e: {k: v for k, v in fetch_today(t, s, e).items() if k != "GAMMA.OL"}  # noqa: E731
    rd = tmp_path / "results"
    data = afternoon.build(TODAY, NOW, INSTRUMENTS, one, rd, list_fn)
    assert data["retry"] == ["kurser"] and "Gammagruppen" in data["errors"][0] and "Betabolaget" not in data["errors"][0]
    assert afternoon.build(TODAY, NOW, INSTRUMENTS, fetch_today, rd, list_fn)["retry"] == []


def test_klar_kraver_kurser_och_stangning(tmp_path):
    import json

    def write(generated, retry=None):
        d = {"generated_at": generated}
        if retry is not None:
            d["retry"] = retry
        (tmp_path / "2026-10-22.json").write_text(json.dumps(d), encoding="utf-8")
        return afternoon.is_complete(TODAY, tmp_path / "2026-10-22.json")

    assert not afternoon.is_complete(TODAY, tmp_path / "2026-10-22.json")  # saknas
    assert write("2026-10-22T17:46:00+02:00", [])
    assert write("2026-10-22T17:46:00+02:00")  # äldre fil utan retry
    assert not write("2026-10-22T17:46:00+02:00", ["kurser"])
    assert not write("2026-10-22T14:05:00+02:00", [])  # "Uppdatera nu" före stängning
    assert write("2026-10-23T08:10:00+02:00", [])  # morgonflödet dagen efter


def test_forra_handelsdagen_bara_om_filen_saknas_eller_ar_ofullstandig(tmp_path, monkeypatch, capsys):
    """Morgonflödet: förra handelsdagens kursreaktion tas fram om eftermiddagen missades eller saknade kurser."""
    import json

    monkeypatch.setattr(afternoon, "AFTERNOON_DIR", tmp_path)
    friday = tmp_path / "2026-10-23.json"
    friday.write_text(json.dumps({"generated_at": "2026-10-23T17:46:00+02:00", "retry": []}), encoding="utf-8")
    monday = datetime(2026, 10, 26, 8, 10, tzinfo=TZ)
    assert afternoon.main(["--forra-om-saknas"], fetch=lambda *a: {}, now=monday) == 0
    assert "2026-10-23 finns redan" in capsys.readouterr().out
    assert afternoon.main(["--forra-om-saknas", "--klar"], now=monday) == 0
    friday.write_text(json.dumps({"generated_at": "2026-10-23T17:46:00+02:00", "retry": ["kurser"]}), encoding="utf-8")
    assert afternoon.main(["--forra-om-saknas", "--klar"], now=monday) == 4


def test_ett_bolag_med_fel_stoppar_inte_eftermiddagen(tmp_path):
    """Ett bolag som inte klarar valideringen hoppas över ("skipped") i stället för att stoppa filen."""
    from validate import prune_invalid

    data = afternoon.build(TODAY, NOW, INSTRUMENTS, fetch_today, results_dir(tmp_path), list_fn)
    by = {c["ticker"]: c for c in data["companies"]}
    by["ALFA"]["relative_pp"] = 5.0  # fel mot index
    res, skipped = prune_invalid(data, lambda d: validate_afternoon(d, "2026-10-22", INSTRUMENTS),
                                 lambda d: [d["companies"]])
    assert res.errors == [] and [s["ticker"] for s in skipped] == ["ALFA"] and "mot index" in skipped[0]["reason"]
    assert [c["ticker"] for c in data["companies"]] == ["GAMMA", "BETA"] and data["skipped"] == skipped
