"""Tester för rapportutfall: scripts/report_list.py, scripts/validate_results.py och scripts/results.py
(påhittade bolag och rapporttexter)."""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import results  # noqa: E402
from report_list import is_report_title, list_reports  # noqa: E402
from validate import Instrument, Result, _normalize_ws, load_sector_kpis  # noqa: E402
from validate_results import check_kpi, period_before, surprise_pct, validate_batch, validate_results, verdict  # noqa: E402

WEEK = "2026-W43"
NOW = datetime(2026, 10, 26, 7, 30, tzinfo=ZoneInfo("Europe/Stockholm"))
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", mfn_slug="alfaverken", sektor="industri"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", sektor="fastigheter"),
}

ALFA_TEXT = """Rubrik: Alfaverken delårsrapport januari-september 2026
---
Delårsrapport januari - september 2026

Juli - september 2026
* Orderingången ökade med 12 % till 1 344 MSEK (1 200)
* Nettoomsättningen uppgick till 1 500 MSEK (1 400)
* Rörelsemarginalen var 11,2 % (10,5)
* Resultat per aktie ökade med 25 % till 2,50 SEK (2,00)

Januari - september 2026
* Nettoomsättningen uppgick till 4 300 MSEK (4 000)
* Resultat per aktie uppgick till 7,10 SEK (6,00)
"""


def make_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / WEEK / "mfn").mkdir(parents=True)
    (raw / WEEK / "mfn" / "alfa.txt").write_text(ALFA_TEXT, encoding="utf-8")
    (raw / WEEK / "mfn" / "beta.txt").write_text("Rubrik: x\n---\nInga siffror i utdraget.\n", encoding="utf-8")
    items = [
        {"source": "mfn", "source_name": "MFN", "url": "https://mfn.example/alfa", "published": "2026-10-22T07:00:00+02:00",
         "title": "Alfaverken delårsrapport januari-september 2026", "company": "alfaverken", "path": "mfn/alfa.txt",
         "text_kind": "fulltext"},
        {"source": "mfn", "source_name": "MFN", "url": "https://mfn.example/beta-datum", "published": "2026-10-19T08:00:00+02:00",
         "title": "Betabolaget publicerar delårsrapport den 30 oktober", "company": "betabolaget", "path": "mfn/x.txt"},
        {"source": "mfn", "source_name": "MFN", "url": "https://mfn.example/beta", "published": "2026-10-23T07:00:00+02:00",
         "title": "Betabolaget: Delårsrapport Q3 2026", "company": "betabolaget", "path": "mfn/beta.txt",
         "text_kind": "fulltext"},
    ]
    (raw / WEEK / "index.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return raw


def kpi(id_, value, unit, prev, change, period, quote):
    return {"id": id_, "value": value, "unit": unit, "previous_value": prev, "change_pct": change,
            "period_kind": period, "quote": quote}


GOOD_KPIS = [
    kpi("orderingang", 1344, "MSEK", 1200, 12, "kvartal", "Orderingången ökade med 12 % till 1 344 MSEK (1 200)"),
    kpi("rorelsemarginal", 11.2, "procent", 10.5, None, "kvartal", "Rörelsemarginalen var 11,2 % (10,5)"),
    kpi("nettoomsattning", 1500, "MSEK", 1400, None, "kvartal", "Nettoomsättningen uppgick till 1 500 MSEK (1 400)"),
    kpi("resultat-per-aktie", 2.5, "SEK", 2.0, 25, "kvartal", "Resultat per aktie ökade med 25 % till 2,50 SEK (2,00)"),
]


def batch(reports):
    return {"week": WEEK, "batch": 1, "reports": reports}


def alfa(kpis=GOOD_KPIS):
    return {"ticker": "ALFA", "raw_file": "mfn/alfa.txt", "period": "Q3 2026", "note": None, "kpis": kpis}


def beta():
    return {"ticker": "BETA", "raw_file": "mfn/beta.txt", "period": "Q3 2026", "note": "utdrag utan siffror", "kpis": []}


def test_rapportrubriker():
    d = date(2026, 10, 19)
    assert is_report_title("Delårsrapport Q3 2026", d)
    assert is_report_title("Atlas: Rapport för tredje kvartalet 2026 och inbjudan till telefonkonferens", d)
    assert not is_report_title("Inbjudan till presentation av delårsrapport", d)
    assert not is_report_title("Bolaget publicerar delårsrapport den 30 oktober", d)
    # Rubriker utan ordet "rapport" (2026-10-20 missades två sådana)
    assert is_report_title("Resultat tredje kvartalet 2026", d)
    assert is_report_title("Alfaverken rapporterar resultat för tredje kvartalet 2026: starkt kassaflöde", d)
    assert not is_report_title("Alfaverken publicerar resultat för tredje kvartalet den 30 oktober", d)
    assert not is_report_title("Alfaverken: preliminärt resultat för tredje kvartalet 2026", d)
    assert is_report_title("Alfaverken AB: Resultat för tredje kvartalet 2026", d)
    assert is_report_title("ALFA:s resultat för tredje kvartalet 2026", d)
    # Resultatvarning, ingen rapport (2026-10-15 räknades en sådan som rapport).
    assert not is_report_title("Alfaverken bedömer att omsättning och resultat för tredje kvartalet 2026 "
                               "väsentligt överstiger marknadens förväntningar", d)
    assert not is_report_title("Inbjudan till presentation av resultat för tredje kvartalet", d)


@pytest.mark.parametrize("title, ok", [
    # Engelska rubriker (2026-11-05 missades en "consolidated quarterly report").
    ("Alfaverken Group consolidated quarterly report for Q3 2026", True),
    ("Alfaverken: Interim report January–September 2026", True),
    ("Alfaverken Half-year report 2026", True),
    ("Alfaverken AB Nine-month report 2026", True),
    ("Alfaverken AB Niomånadersrapport 2026", True),
    ("Invitation to presentation of Alfaverken's quarterly report", False),
    ("Alfaverken will publish its quarterly report on 12 November", False),
])
def test_engelska_rapportrubriker(title, ok):
    assert is_report_title(title, date(2026, 11, 5)) is ok


def test_lista_hoppar_over_datumbesked(tmp_path):
    raw = make_raw(tmp_path)
    reports = list_reports(raw, WEEK, INSTRUMENTS, load_sector_kpis())
    assert [r["ticker"] for r in reports] == ["ALFA", "BETA"]
    assert reports[0]["kpis"] == ["orderingang", "rorelsemarginal", "nettoomsattning", "resultat-per-aktie",
                                  "justerat-resultat-per-aktie"]
    assert reports[1]["kpis"][0] == "forvaltningsresultat-per-aktie"


def test_period_fore_citatet():
    text = _normalize_ws(ALFA_TEXT)
    assert period_before(text, text.find("Orderingången")) == "kvartal"
    assert period_before(text, text.find("Resultat per aktie uppgick till 7,10")) == "delår"
    assert period_before("Helåret 2026: Omsättning 5 MSEK (4)", 15) == "helår"
    assert period_before("Ingen period här", 10) is None


def test_godkant_nyckeltal_och_fel():
    text = _normalize_ws(ALFA_TEXT)

    def errors(k, kind="belopp"):
        r = Result()
        check_kpi(k, kind, text, "t", r)
        return r.errors

    assert errors(GOOD_KPIS[0]) == []
    assert errors(GOOD_KPIS[1], "procent") == []
    assert errors(GOOD_KPIS[3], "per_aktie") == []
    cases = [
        ({**GOOD_KPIS[0], "quote": "Orderingången ökade kraftigt"}, "belopp", "finns inte i rapporttexten"),
        ({**GOOD_KPIS[0], "value": 1345}, "belopp", "värdet 1345"),
        ({**GOOD_KPIS[0], "previous_value": 1100}, "belopp", "föregående års värde 1100"),
        ({**GOOD_KPIS[0], "previous_value": None, "change_pct": None}, "belopp", "varken"),
        ({**GOOD_KPIS[0], "unit": "procent"}, "belopp", "enheten"),
        ({**GOOD_KPIS[1], "change_pct": 6.7}, "procent", "procenttal"),
        # Delårets siffror angivna som kvartal.
        (kpi("nettoomsattning", 4300, "MSEK", 4000, None, "kvartal", "Nettoomsättningen uppgick till 4 300 MSEK (4 000)"),
         "belopp", "delårsangivelse"),
    ]
    for k, kind, msg in cases:
        errs = errors(k, kind)
        assert any(msg in e for e in errs), (msg, errs)


def test_tusentalspunkt_och_decimalkolon_i_citat():
    """Samma sifferfunktion som veckosammanfattningen: "2.500 MSEK (2.342)" och "0:21 SEK (1:26)"
    (Nolato och Xano 2026-10-29 underkändes)."""
    text = _normalize_ws("Juli - september 2026\nOmsättningen ökade till 2.500 MSEK (2.342).\n"
                         "Resultat per aktie var 0:21 SEK (1:26).\n")
    r = Result()
    check_kpi(kpi("nettoomsattning", 2500, "MSEK", 2342, None, "kvartal",
                  "Omsättningen ökade till 2.500 MSEK (2.342)."), "belopp", text, "t", r)
    check_kpi(kpi("resultat-per-aktie", 0.21, "SEK", 1.26, None, "kvartal",
                  "Resultat per aktie var 0:21 SEK (1:26)."), "per_aktie", text, "t", r)
    assert r.errors == []


def test_i_rapporten_valideras_som_nyheterna():
    from validate_results import check_highlight, highlight_required

    text = _normalize_ws(ALFA_TEXT + "\nUtsikter\nVi räknar med att efterfrågan i Norden förblir svag under 2027.\n")
    q_order = "Orderingången ökade med 12 % till 1 344 MSEK (1 200)"
    q_outlook = "Vi räknar med att efterfrågan i Norden förblir svag under 2027."

    def errors(h):
        r = Result()
        check_highlight(h, text, "t", r)
        return r.errors

    assert errors({"text": "Orderingången ökade 12 %; svag efterfrågan i Norden väntas under 2027.",
                   "quotes": [q_order, q_outlook], "tone": "blandad"}) == []
    # Tonen ("Ton i rapporten") krävs och ska vara en av de fyra; raden ska dela innehållsord med citaten.
    for h, msg in [
        ({"text": "Orderingången ökade 12 %.", "quotes": [q_order]}, "tonen saknas"),
        ({"text": "Orderingången ökade 12 %.", "quotes": [q_order], "tone": "glad"}, "tonen saknas eller är okänd"),
        ({"text": "Bolaget räknar med 12 % och svag efterfrågan.", "quotes": [q_order], "tone": "negativ"},
         "innehållsord"),
    ]:
        errs = errors(h)
        assert any(msg in e for e in errs), (msg, errs)
    # Hel mening: punkt sist, stor bokstav först, inget ord sist som kräver fortsättning (Net Insight
    # 2026-10-29 slutade "… och kommersiella framsteg" utan punkt).
    for bad, msg in [
        ("Svagt kvartal men intäkterna förbättrades och kommersiella framsteg", "ska sluta med punkt"),
        ("orderingången ökade 12 %.", "stor bokstav"),
        ("Tillväxtmöjligheter finns bland annat inom.", "slutar på 'inom'"),
        ("Orderingången ökade 12 % och…", "ska sluta med punkt"),
        ("Orderingången ökade (12 %.", "parentes"),
    ]:
        errs = errors({"text": bad, "quotes": [q_order]})
        assert any("inte en hel mening" in e and msg in e for e in errs), (bad, errs)
    cases = [
        ({"text": "Orderingången ökade 13 %", "quotes": [q_order]}, "utan exakt stöd"),
        ({"text": "Orderingången minskade 12 %", "quotes": [q_order]}, "riktningen"),
        ({"text": "Orderingången ökade", "quotes": ["Orderingången ökade kraftigt"]}, "finns inte i rapporttexten"),
        ({"text": " ".join(["ord"] * 16), "quotes": [q_order]}, "16 ord"),
        ({"text": "Orderingången ökade", "quotes": []}, "0 citat"),
        ({"text": "Orderingången ökade, aktien ser billig ut", "quotes": [q_order]}, "aktien"),
    ]
    for h, msg in cases:
        errs = errors(h)
        assert any(msg in e for e in errs), (msg, errs)
    # Krävs från och med vecka 44 2026, för veckor, dagar och uppdateringar.
    assert not highlight_required("2026-W43") and not highlight_required("2026-10-23")
    assert highlight_required("2026-W44") and highlight_required("2026-10-26") and highlight_required("2026-10-29T1745")


def test_i_rapporten_kravs_fran_vecka_44(tmp_path):
    """En rapport utan "I rapporten" i vecka 44 underkänns (och görs om ensam); utan nyckeltal behövs ingen rad."""
    from validate_results import check_batch

    raw = tmp_path / "raw"
    (raw / "2026-W44" / "mfn").mkdir(parents=True)
    (raw / "2026-W44" / "mfn" / "alfa.txt").write_text(ALFA_TEXT, encoding="utf-8")
    (raw / "2026-W44" / "mfn" / "beta.txt").write_text("Rubrik: x\n---\nInga siffror i utdraget.\n", encoding="utf-8")
    items = [
        {"source": "mfn", "source_name": "MFN", "url": "https://mfn.example/alfa", "published": "2026-10-29T07:00:00+01:00",
         "title": "Alfaverken delårsrapport januari-september 2026", "company": "alfaverken", "path": "mfn/alfa.txt",
         "text_kind": "fulltext"},
        {"source": "mfn", "source_name": "MFN", "url": "https://mfn.example/beta", "published": "2026-10-30T07:00:00+01:00",
         "title": "Betabolaget: Delårsrapport Q3 2026", "company": "betabolaget", "path": "mfn/beta.txt",
         "text_kind": "fulltext"},
    ]
    (raw / "2026-W44" / "index.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    b = {"week": "2026-W44", "batch": 1, "reports": [alfa(), beta()]}
    assert list(check_batch(b, "2026-W44", 1, INSTRUMENTS, raw, tmp_path / "morning", tmp_path / "results").rejected) == ["ALFA"]
    h = {"text": "Orderingången ökade 12 % i kvartalet.", "tone": "positiv", "quotes": ["Orderingången ökade med 12 % till 1 344 MSEK (1 200)"]}
    b["reports"][0]["highlight"] = h
    assert check_batch(b, "2026-W44", 1, INSTRUMENTS, raw, tmp_path / "morning", tmp_path / "results").rejected == {}


def test_forandring_som_inte_stammer_med_vardena():
    text = "Juli - september 2026: Vinsten ökade med 40 % till 1 344 MSEK (1 200)"
    r = Result()
    check_kpi(kpi("orderingang", 1344, "MSEK", 1200, 40, "kvartal", text.split(": ")[1]), "belopp", text, "t", r)
    assert any("stämmer inte med värdena" in e for e in r.errors)


def test_omgang_maste_tacka_listan(tmp_path):
    raw = make_raw(tmp_path)
    assert validate_batch(batch([alfa(), beta()]), WEEK, 1, INSTRUMENTS, raw).errors == []
    errs = validate_batch(batch([alfa()]), WEEK, 1, INSTRUMENTS, raw).errors
    assert any("BETA: rapporten saknas" in e for e in errs)
    other = {**beta(), "ticker": "GAMA"}
    errs = validate_batch(batch([alfa(), beta(), other]), WEEK, 1, INSTRUMENTS, raw).errors
    assert any("GAMA" in e for e in errs)
    errs = validate_batch(batch([alfa(), {**beta(), "note": None}]), WEEK, 1, INSTRUMENTS, raw).errors
    assert any("ingen förklaring" in e for e in errs)


def test_avvikelse_och_bedomning():
    assert surprise_pct(2.5, 2.0) == 25
    assert surprise_pct(-0.5, -1.0) == 50  # mindre förlust än väntat är bättre
    assert verdict(1.9) == "i linje" and verdict(2.1) == "över" and verdict(-3) == "under"


def test_sammanslagning_med_konsensus(tmp_path):
    raw = make_raw(tmp_path)
    results_dir, cons_dir = tmp_path / "results", tmp_path / "consensus"
    (results_dir / WEEK).mkdir(parents=True)
    (results_dir / WEEK / "omgang-1.json").write_text(json.dumps(batch([alfa(), beta()])), encoding="utf-8")
    cons_dir.mkdir()
    est = lambda avg, ccy="SEK": {"avg": avg, "low": None, "high": None, "analysts": 4, "year_ago": None, "currency": ccy}  # noqa: E731
    snap = lambda d, nxt: {"date": d, "companies": [{"ticker": "ALFA", "eps": est(2.4), "revenue": est(1.6e9),  # noqa: E731
                                                     "next_report_date": nxt}]}
    # Ögonblicksbilden samma dag som rapporten används inte; dagen före gör det.
    (cons_dir / "2026-10-21.json").write_text(json.dumps(snap("2026-10-21", "2026-10-22")), encoding="utf-8")
    (cons_dir / "2026-10-22.json").write_text(json.dumps(snap("2026-10-22", "2027-01-25")), encoding="utf-8")
    data, problems = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, cons_dir)
    assert problems == []
    a = next(r for r in data["reports"] if r["ticker"] == "ALFA")
    assert [k["id"] for k in a["kpis"]] == ["orderingang", "rorelsemarginal", "nettoomsattning", "resultat-per-aktie"]
    assert (a["kpis"][2]["change_pct"], a["kpis"][2]["yoy_pct"]) == (None, 7.1)  # räknat ur 1 500 / 1 400
    assert a["kpis"][0]["yoy_pct"] == 12  # rapportens egen siffra
    assert a["kpis"][1]["yoy_pct"] is None  # procenttal
    c = a["vs_consensus"]
    assert c["consensus_date"] == "2026-10-21"
    assert (c["eps"]["surprise_pct"], c["eps"]["verdict"]) == (4.2, "över")
    assert (c["revenue"]["actual"], c["revenue"]["surprise_pct"], c["revenue"]["verdict"]) == (1.5e9, -6.2, "under")
    assert validate_results(data, WEEK, INSTRUMENTS, raw).errors == []
    a["kpis"][2]["yoy_pct"] = 8.0
    assert any("yoy_pct" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw).errors)
    a["kpis"][2]["yoy_pct"] = 7.1
    c["eps"]["verdict"] = "under"
    assert any("bedömningen" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw).errors)


def test_validate_py_valjer_resultatvalideringen_for_morgonfiler(tmp_path, monkeypatch):
    """validate.py ska känna igen morgonens omgångar och sammanslagna fil (2026-10-20 validerades
    omgången som en veckofil, så en godkänd omgång kördes alltid om)."""
    import validate
    import validate_results

    calls = []
    monkeypatch.setattr(validate_results, "validate_batch", lambda d, scope, n, i: calls.append(("omgång", scope, n)) or Result())
    monkeypatch.setattr(validate_results, "validate_results", lambda d, scope, i: calls.append(("fil", scope)) or Result())
    paths = [tmp_path / "results" / "morning" / "2026-10-20" / "omgang-1.json",
             tmp_path / "results" / "morning" / "2026-10-20.json",
             tmp_path / "results" / "2026-W43" / "omgang-2.json",
             tmp_path / "results" / "2026-W43.json",
             tmp_path / "results" / "updates" / "2026-10-20T1745" / "omgang-1.json",
             tmp_path / "results" / "updates" / "2026-10-20T1745.json"]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    assert validate.main([str(p) for p in paths]) == 0
    assert calls == [("omgång", "2026-10-20", 1), ("fil", "2026-10-20"), ("omgång", "2026-W43", 2), ("fil", "2026-W43"),
                     ("omgång", "2026-10-20T1745", 1), ("fil", "2026-10-20T1745")]


def test_osaker_konsensus_ger_ingen_bedomning(tmp_path):
    """Förväntad förändring över ±50 % mot fjol från färre än 3 analytiker: "osäker konsensus"
    i stället för över/under, för båda jämförelserna."""
    raw = make_raw(tmp_path)
    results_dir, cons_dir = tmp_path / "results", tmp_path / "consensus"
    (results_dir / WEEK).mkdir(parents=True)
    (results_dir / WEEK / "omgang-1.json").write_text(json.dumps(batch([alfa(), beta()])), encoding="utf-8")
    cons_dir.mkdir()
    eps = {"avg": 2.4, "low": None, "high": None, "analysts": 1, "year_ago": 1.2, "currency": "SEK"}  # +100 %
    rev = {"avg": 1.6e9, "low": None, "high": None, "analysts": 8, "year_ago": 1.5e9, "currency": "SEK"}
    snap = {"date": "2026-10-21", "companies": [{"ticker": "ALFA", "eps": eps, "revenue": rev,
                                                 "next_report_date": "2026-10-22"}]}
    (cons_dir / "2026-10-21.json").write_text(json.dumps(snap), encoding="utf-8")
    data, _ = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, cons_dir)
    c = next(r for r in data["reports"] if r["ticker"] == "ALFA")["vs_consensus"]
    assert c["uncertain"] is True
    assert (c["eps"]["verdict"], c["revenue"]["verdict"]) == ("osäker konsensus", "osäker konsensus")
    assert c["eps"]["surprise_pct"] == 4.2  # avvikelsen sparas men visas inte
    assert validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors == []
    # Över/under trots varningen underkänns, liksom en flagga som inte stämmer med konsensusbilden.
    c["eps"]["verdict"] = "över"
    assert any("bedömningen" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors)
    c["eps"]["verdict"], c["revenue"]["verdict"], c["uncertain"] = "över", "under", False
    assert any("uncertain" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors)
    # Med tre analytiker är konsensus inte osäker.
    eps["analysts"] = 3
    (cons_dir / "2026-10-21.json").write_text(json.dumps(snap), encoding="utf-8")
    data, _ = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, cons_dir)
    c = next(r for r in data["reports"] if r["ticker"] == "ALFA")["vs_consensus"]
    assert (c["uncertain"], c["eps"]["verdict"]) == (False, "över")


def test_bedomning_utan_procent():
    """Utredningen W43: teckenbyte (Electrolux −0,46 mot 0,53), nära noll (Volvo Car −1,63 mot
    −0,14), bolagshändelse före allt annat, kontrollera vid över ±50 % (Hexagon 0,166 mot 0,074 EUR)."""
    from validate_results import comparison_verdict as cv

    assert cv(-0.46, 0.53, -187.6, False, False) == "teckenbyte"
    assert cv(-1.63, -0.14, -1064.3, False, False) == "nära noll"
    assert cv(0.01, 0.5, -98.0, False, False) == "nära noll"
    assert cv(-0.46, 0.53, -187.6, False, True) == "bolagshändelse"
    assert cv(2.5, 2.4, 4.2, False, True) == "bolagshändelse"
    assert cv(0.166, 0.07357, 125.6, False, False) == "kontrollera"
    assert cv(1.5, 1.0, 50.0, False, False) == "över"  # gränsen: över ±50 % krävs
    assert cv(2.5, 2.4, 4.2, True, False) == "osäker konsensus"
    assert cv(-0.26, 0.71, -136.8, True, False) == "teckenbyte"  # båda värdena säger mer än "osäker"


def test_bolagshandelse_och_cent_mot_forvantan(tmp_path, monkeypatch):
    import validate_results as vr

    raw = make_raw(tmp_path)
    results_dir, cons_dir = tmp_path / "results", tmp_path / "consensus"
    (results_dir / WEEK).mkdir(parents=True)
    (results_dir / WEEK / "omgang-1.json").write_text(json.dumps(batch([alfa(), beta()])), encoding="utf-8")
    cons_dir.mkdir()
    est = lambda avg: {"avg": avg, "low": None, "high": None, "analysts": 4, "year_ago": None, "currency": "SEK"}  # noqa: E731
    snap = {"date": "2026-10-21", "companies": [{"ticker": "ALFA", "eps": est(2.4), "revenue": est(1.6e9),
                                                 "next_report_date": "2026-10-22"}]}
    (cons_dir / "2026-10-21.json").write_text(json.dumps(snap), encoding="utf-8")
    ca = tmp_path / "corporate_actions.json"
    ca.write_text(json.dumps({"actions": [], "investigated": [{"ticker": "ALFA", "label": "nyemission"}]}), encoding="utf-8")
    monkeypatch.setattr(vr, "CORPORATE_ACTIONS", ca)
    data, _ = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, cons_dir)
    c = next(r for r in data["reports"] if r["ticker"] == "ALFA")["vs_consensus"]
    assert c["corporate_event"] and c["eps"]["verdict"] == c["revenue"]["verdict"] == "bolagshändelse"
    assert validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors == []
    c["corporate_event"] = False
    assert any("corporate_event" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors)
    # Eurocent räknas om till euro före jämförelsen: 16,6 cent = 0,166 EUR.
    euro = {"avg": 0.07357, "low": None, "high": None, "analysts": 6, "year_ago": 0.096, "currency": "EUR"}
    k = kpi("resultat-per-aktie", 16.6, "Euro cent", -1.6, None, "kvartal", "Resultatet per aktie uppgick till 16,6 Euro cent (-1,6)")
    cmp = results.compare(k, euro)
    assert (cmp["actual"], cmp["currency"], cmp["surprise_pct"], cmp["verdict"]) == (0.166, "EUR", 125.6, "kontrollera")


def test_justerat_resultat_per_aktie_mot_forvantan():
    """Justerat resultat per aktie jämförs med förväntan när rapporten anger ett (Hexagon 2026-10-23:
    redovisat 16,6, justerat 8,2 eurocent mot förväntan 0,0736 EUR), annars det redovisade."""
    from validate_results import eps_for_comparison

    text = _normalize_ws("Juli - september 2026\nResultatet per aktie uppgick till 16,6 Euro cent (-1,6)\n"
                         "Justerat resultat per aktie uppgick till 8,2 Euro cent (6,9)\n")
    rep = kpi("resultat-per-aktie", 16.6, "Euro cent", -1.6, None, "kvartal",
              "Resultatet per aktie uppgick till 16,6 Euro cent (-1,6)")
    adj = kpi("justerat-resultat-per-aktie", 8.2, "Euro cent", 6.9, None, "kvartal",
              "Justerat resultat per aktie uppgick till 8,2 Euro cent (6,9)")
    r = Result()
    check_kpi(adj, "per_aktie", text, "t", r)
    assert r.errors == []
    # Ett "justerat" nyckeltal vars citat inte säger att det är justerat underkänns.
    r = Result()
    check_kpi({**rep, "id": "justerat-resultat-per-aktie"}, "per_aktie", text, "t", r)
    assert any("justerat" in e for e in r.errors)

    assert eps_for_comparison({"resultat-per-aktie": rep, "justerat-resultat-per-aktie": adj}, "EUR") == (adj, "justerat")
    assert eps_for_comparison({"resultat-per-aktie": rep}, "EUR") == (rep, "redovisat")
    assert eps_for_comparison({"resultat-per-aktie": rep, "justerat-resultat-per-aktie": {**adj, "period_kind": "delår"}},
                              "EUR") == (rep, "redovisat")  # bara kvartalets justerade värde
    euro = {"avg": 0.07357, "low": None, "high": None, "analysts": 6, "year_ago": 0.096, "currency": "EUR"}
    cmp = results.compare(adj, euro)
    assert (cmp["actual"], cmp["surprise_pct"], cmp["verdict"]) == (0.082, 11.5, "över")


def test_jamforelsen_anger_justerat_eller_redovisat(tmp_path):
    raw = make_raw(tmp_path)
    alfa_text = ALFA_TEXT.replace("* Resultat per aktie ökade med 25 % till 2,50 SEK (2,00)\n",
                                  "* Resultat per aktie ökade med 25 % till 2,50 SEK (2,00)\n"
                                  "* Justerat resultat per aktie uppgick till 2,60 SEK (2,10)\n")
    (raw / WEEK / "mfn" / "alfa.txt").write_text(alfa_text, encoding="utf-8")
    adj = kpi("justerat-resultat-per-aktie", 2.6, "SEK", 2.1, None, "kvartal",
              "Justerat resultat per aktie uppgick till 2,60 SEK (2,10)")
    results_dir, cons_dir = tmp_path / "results", tmp_path / "consensus"
    (results_dir / WEEK).mkdir(parents=True)
    (results_dir / WEEK / "omgang-1.json").write_text(json.dumps(batch([alfa(GOOD_KPIS + [adj]), beta()])),
                                                      encoding="utf-8")
    cons_dir.mkdir()
    est = lambda avg: {"avg": avg, "low": None, "high": None, "analysts": 4, "year_ago": None, "currency": "SEK"}  # noqa: E731
    (cons_dir / "2026-10-21.json").write_text(json.dumps({"date": "2026-10-21", "companies": [
        {"ticker": "ALFA", "eps": est(2.5), "revenue": est(1.5e9), "next_report_date": "2026-10-22"}]}), encoding="utf-8")
    data, problems = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, cons_dir)
    assert problems == []
    a = next(r for r in data["reports"] if r["ticker"] == "ALFA")
    eps = a["vs_consensus"]["eps"]
    assert (eps["basis"], eps["actual"], eps["surprise_pct"], eps["verdict"]) == ("justerat", 2.6, 4.0, "över")
    # Förändringen mot fjolåret för resultat per aktie är fortfarande det redovisade nyckeltalets.
    rep = next(k for k in a["kpis"] if k["id"] == "resultat-per-aktie")
    assert (rep["value"], rep["yoy_pct"]) == (2.5, 25)
    assert validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors == []
    eps["basis"] = "redovisat"
    assert any("ska använda justerat" in e for e in validate_results(data, WEEK, INSTRUMENTS, raw, consensus_dir=cons_dir).errors)


def test_per_aktie_kraver_kvartalsangivelse():
    """"Mot fjol" visar per aktie mot samma kvartal i fjol: utan kvartalsangivelse före citatet underkänns
    värdet, och en angivelse för årets första nio månader underkänns som kvartal."""
    no_period = _normalize_ws("Sammanfattning\nResultat per aktie uppgick till 2,50 SEK (2,00).\n"
                              "Nettoomsättningen uppgick till 1 500 MSEK (1 400).\n")
    eps = kpi("resultat-per-aktie", 2.5, "SEK", 2.0, None, "kvartal", "Resultat per aktie uppgick till 2,50 SEK (2,00).")
    r = Result()
    check_kpi(eps, "per_aktie", no_period, "t", r)
    assert any("kvartalsangivelse" in e for e in r.errors)
    r = Result()  # belopp utan angivelse: bara en varning, som tidigare
    check_kpi(kpi("nettoomsattning", 1500, "MSEK", 1400, None, "kvartal",
                  "Nettoomsättningen uppgick till 1 500 MSEK (1 400)."), "belopp", no_period, "t", r)
    assert r.errors == [] and r.warnings
    nine = _normalize_ws("Januari – september 2026\nResultat per aktie uppgick till 2,50 SEK (2,00).\n")
    r = Result()
    check_kpi(eps, "per_aktie", nine, "t", r)
    assert any("delårsangivelse" in e for e in r.errors)
    q3 = _normalize_ws("Juli – september 2026\nResultat per aktie uppgick till 2,50 SEK (2,00).\n")
    r = Result()
    check_kpi(eps, "per_aktie", q3, "t", r)
    assert r.errors == []


def test_urvalskontroll_large_cap(tmp_path, monkeypatch):
    """En sektor får inte vara tom om ett Large Cap-bolag i sektorn rapporterat under veckan (W43 med
    Sonnet: telekom tom trots Tele2 och Telia). Bolag utanför Large Cap kräver ingen sektor."""
    import validate

    raw = make_raw(tmp_path)  # Alfaverken (industri) och Betabolaget (fastigheter) rapporterar vecka 43
    inst = {
        "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", mfn_slug="alfaverken", sektor="industri",
                           isin=("SE0000000001",)),
        "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", sektor="fastigheter",
                           isin=("SE0000000002",)),
    }
    lc = tmp_path / "large_cap.json"
    lc.write_text(json.dumps({"isins": ["SE0000000001"]}), encoding="utf-8")  # bara Alfaverken är Large Cap
    monkeypatch.setattr(validate, "LARGE_CAP_PATH", lc)
    assert validate.load_large_cap(inst) == {"ALFA"}

    def check(sectors):
        res = Result()
        validate._check_selection({"sectors": sectors}, WEEK, inst, raw, res)
        return res.errors

    errors = check([{"id": "fastigheter", "items": [{}]}])
    assert errors == ["sektorn industri är tom, men Large Cap-bolag i sektorn rapporterade under veckan: Alfaverken"]
    assert check([{"id": "industri", "items": []}])  # sektorn finns men saknar nyheter
    assert check([{"id": "industri", "items": [{}]}]) == []  # fastigheter tom går bra: Betabolaget är inte Large Cap


def test_saknad_omgang_listas(tmp_path):
    raw = make_raw(tmp_path)
    data, problems = results.build(WEEK, INSTRUMENTS, NOW, raw, tmp_path / "results", tmp_path / "consensus")
    assert data["missing_batches"] == [1] and problems == ["omgång 1 saknas"]


def test_underkand_rapport_underkanner_inte_omgangen(tmp_path):
    """En underkänd rapport markeras med orsaken; omgångens övriga rapporter tas med (2026-10-29 gick
    hela morgonens omgång förlorad på ett enda citat)."""
    from validate_results import batch_status, check_batch

    raw = make_raw(tmp_path)
    bad = alfa([{**GOOD_KPIS[0], "value": 1345}, *GOOD_KPIS[1:]])
    check = check_batch(batch([bad, beta()]), WEEK, 1, INSTRUMENTS, raw)
    assert check.errors == [] and list(check.rejected) == ["ALFA"]
    assert validate_batch(batch([bad, beta()]), WEEK, 1, INSTRUMENTS, raw).errors  # hela filen är inte godkänd
    # En saknad rapport underkänner bara den rapporten.
    assert list(check_batch(batch([beta()]), WEEK, 1, INSTRUMENTS, raw).rejected) == ["ALFA"]
    # Fel omgång i filen: inget går att använda.
    assert check_batch({**batch([alfa(), beta()]), "batch": 2}, WEEK, 1, INSTRUMENTS, raw).errors

    results_dir = tmp_path / "results"
    (results_dir / WEEK).mkdir(parents=True)
    path = results_dir / WEEK / "omgang-1.json"
    path.write_text(json.dumps(batch([bad, beta()])), encoding="utf-8")
    data, problems = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, tmp_path / "consensus")
    assert [r["ticker"] for r in data["reports"]] == ["BETA"] and data["missing_batches"] == []
    assert [(r["ticker"], r["name"]) for r in data["rejected"]] == [("ALFA", "Alfaverken")]
    assert data["rejected"][0]["reason"].startswith("orderingang: ") and "1345" in data["rejected"][0]["reason"]
    assert any("ALFA underkänd" in p for p in problems)
    assert validate_results(data, WEEK, INSTRUMENTS, raw).errors == []

    import validate_results as vr
    from unittest import mock

    with mock.patch.object(vr, "check_batch", lambda d, w, n, i: check_batch(d, w, n, i, raw)):
        assert batch_status(path, INSTRUMENTS) == "ALFA"
        path.write_text(json.dumps(batch([alfa(), beta()])), encoding="utf-8")
        assert batch_status(path, INSTRUMENTS) == "godkänd"
        assert batch_status(path.with_name("omgang-9.json"), INSTRUMENTS) == "hela"


def test_period_inne_i_citatet():
    text = _normalize_ws("Januari - september 2026\nResultat per aktie uppgick till +12,93 (+7,45) för hela perioden "
                         "och för kvartalet till -1,13 (+1,70).")
    quote = "Resultat per aktie uppgick till +12,93 (+7,45) för hela perioden och för kvartalet till -1,13 (+1,70)"
    r = Result()
    check_kpi(kpi("resultat-per-aktie", -1.13, "SEK", 1.70, None, "kvartal", quote), "per_aktie", text, "t", r)
    assert r.errors == []
    r = Result()
    check_kpi(kpi("resultat-per-aktie", 12.93, "SEK", 7.45, None, "kvartal", quote), "per_aktie", text, "t", r)
    assert any("delårsangivelse" in e for e in r.errors)


def _alfa_snapshot(d, nxt="2026-10-22", after=False):
    est = {"avg": 2.4, "low": None, "high": None, "analysts": 4, "year_ago": None, "currency": "SEK"}
    return {"date": d, "companies": [{"ticker": "ALFA", "eps": est, "revenue": None, "next_report_date": nxt,
                                      "after_report": after}]}


def test_konsensus_samma_dag_eller_efter_rapport_anvands_aldrig(tmp_path):
    report = {"ticker": "ALFA", "kpis": [GOOD_KPIS[3]]}
    cons = tmp_path / "consensus"
    cons.mkdir()
    published = date(2026, 10, 22)
    # Bara en bild samma dag som rapporten: ingen jämförelse.
    (cons / "2026-10-22.json").write_text(json.dumps(_alfa_snapshot("2026-10-22")), encoding="utf-8")
    assert results.vs_consensus(report, published, cons) is None
    # Bild dagen före, men bolaget hade redan rapporterat när den togs: ingen jämförelse.
    (cons / "2026-10-21.json").write_text(json.dumps(_alfa_snapshot("2026-10-21", after=True)), encoding="utf-8")
    assert results.vs_consensus(report, published, cons) is None
    # Bild dagen före utan after_report: jämförs.
    (cons / "2026-10-21.json").write_text(json.dumps(_alfa_snapshot("2026-10-21")), encoding="utf-8")
    assert results.vs_consensus(report, published, cons)["consensus_date"] == "2026-10-21"


def _morning_setup(tmp_path):
    """Actions- och morgonrådata med samma rapport i båda, och ett fönster för 22 okt."""
    from report_list import create_window

    raw = make_raw(tmp_path)
    morning_raw = tmp_path / "morning-raw"
    (morning_raw / WEEK / "mfn").mkdir(parents=True)
    (morning_raw / WEEK / "mfn" / "alfa.txt").write_text(ALFA_TEXT, encoding="utf-8")
    item = {"id": "alfa-1", "source": "mfn", "source_name": "MFN", "url": "https://mfn.example/alfa",
            "published": "2026-10-22T07:00:00+02:00", "title": "Alfaverken delårsrapport januari-september 2026",
            "company": "alfaverken", "path": "mfn/alfa.txt", "text_kind": "fulltext"}
    (morning_raw / WEEK / "index.json").write_text(json.dumps({"items": [item]}), encoding="utf-8")
    results_dir = tmp_path / "results"
    tz = ZoneInfo("Europe/Stockholm")
    create_window("2026-10-22", datetime(2026, 10, 22, 8, 10, tzinfo=tz), results_dir)
    return raw, morning_raw, results_dir


def test_dagslage_fonster_och_dubbletter(tmp_path):
    from report_list import list_scope, load_window

    raw, morning_raw, results_dir = _morning_setup(tmp_path)
    since, until = load_window("2026-10-22", results_dir)
    assert since.isoformat() == "2026-10-21T08:10:00+02:00"  # förra handelsdagen 08:10
    listed = list_scope("2026-10-22", INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir)
    # Alfaverken 07:00 (finns i båda rådatamapparna, räknas en gång); Betabolaget 23 okt ligger efter fönstret.
    assert [r["ticker"] for r in listed] == ["ALFA"]
    ok = {"week": "2026-10-22", "batch": 1, "reports": [alfa()]}
    assert validate_batch(ok, "2026-10-22", 1, INSTRUMENTS, raw, morning_raw, results_dir).errors == []


def test_nasta_fonster_borjar_dar_forra_slutade(tmp_path):
    from report_list import create_window

    tz = ZoneInfo("Europe/Stockholm")
    d = tmp_path / "results"
    create_window("2026-10-22", datetime(2026, 10, 22, 9, 30, tzinfo=tz), d)
    since, _ = create_window("2026-10-23", datetime(2026, 10, 23, 8, 10, tzinfo=tz), d)
    assert since.isoformat() == "2026-10-22T09:30:00+02:00"
    # Ett befintligt fönster ändras aldrig.
    again = create_window("2026-10-23", datetime(2026, 10, 23, 15, 0, tzinfo=tz), d)
    assert again[1].isoformat() == "2026-10-23T08:10:00+02:00"


def test_uppdateringar_och_morgnar_kedjas_utan_overlapp(tmp_path):
    """Varje fönster börjar där det senast slutande fönstret slutade, oavsett ordning och körningstyp."""
    from report_list import create_window

    tz = ZoneInfo("Europe/Stockholm")
    d = tmp_path / "results"
    at = lambda day, h, m: datetime(2026, 10, day, h, m, tzinfo=tz)  # noqa: E731
    create_window("2026-10-22", at(22, 8, 10), d)
    # Eftermiddagen samma dag fortsätter från morgonen, nästa morgon från eftermiddagen.
    assert create_window("2026-10-22T1745", at(22, 17, 45), d)[0] == at(22, 8, 10)
    # "Uppdatera nu" före morgonkörningen: morgonen fortsätter från uppdateringen.
    assert create_window("2026-10-23T0730", at(23, 7, 30), d)[0] == at(22, 17, 45)
    assert create_window("2026-10-23", at(23, 8, 10), d)[0] == at(23, 7, 30)
    assert create_window("2026-10-23T1745", at(23, 17, 45), d)[0] == at(23, 8, 10)
    # En uppdatering på lördagen räknas in när måndagens morgon börjar (förra handelsdagen är fredag).
    assert create_window("2026-10-24T1100", at(24, 11, 0), d)[0] == at(23, 17, 45)
    assert create_window("2026-10-26", at(26, 8, 10), d)[0] == at(24, 11, 0)
    assert (d / "updates" / "2026-10-22T1745" / "window.json").exists()


REPORT_BODY = ("Juli – september 2026\n* Nettoomsättningen ökade till 1 500 MSEK (1 400)\n"
               "* Rörelseresultatet uppgick till 160 MSEK (140)\n* Resultat per aktie 2,50 SEK (2,00)\n")
# Rubrikformerna som missades vecka 43 (påhittade bolag): bara en rubrikmening, "Kv3", "Q3",
# "Delårsredogörelse", "rapporterar resultatet", versaler.
HEADLINE_REPORTS = [
    "Alfaverken Kv3 2026: Förbättrad marginal och ökad efterfrågan i vissa segment",
    "Organisk tillväxt i orderingång samt marginalförbättring inom samtliga affärsområden",
    "DRIVA VÄRDE GENOM LÖNSAMHET OCH KASSAKONVERTERING, TREDJE KVARTALET 2026",
    "Alfaverken: Fortsatt volymtillväxt i en dynamisk marknad",
    "Alfaverken AB: Delårsredogörelse januari - september 2026",
    "Alfaverken rapporterar resultatet för tredje kvartalet och niomånadersperioden 2026",
    "Disciplin i ett svagt kvartal och en utmanande marknad",
    "Tredje kvartalet 2026",
    "Stärkt balansräkning, fortsatt press på intäkterna",
    "Stärkt lönsamhet och ökad takt i omställningen",
    "Stark tillväxt; justerad EBITA ökade med 30 %",
    "Alfaverken AB: Kraftig ordertillväxt med fortsatta framsteg",
    "Alfaverken AB: Ökad tillväxt och stärkt resultat i portföljen",
    "Alfaverkens resultat Kv3 2026: Positionerade för ökad efterfrågan",
    "Alfakoncernen - det tredje kvartalet 2026",
    "Alfaverken Q3 2026: vidtar åtgärder i en utmanande omvärld",
]


def test_kalenderregeln_rubrikmeningar_och_undantag():
    from report_list import is_calendar_report

    d = date(2026, 10, 23)
    for title in HEADLINE_REPORTS:
        assert is_calendar_report(title, REPORT_BODY, d), title
    # Resultatvarning (som MilDef 2026-10-15), preliminära siffror, inbjudan, datumbesked.
    assert not is_calendar_report("Alfaverken bedömer att omsättning och resultat för tredje kvartalet 2026 "
                                  "väsentligt överstiger marknadens förväntningar", REPORT_BODY, d)
    assert not is_calendar_report("Alfaverken lämnar preliminärt resultat för tredje kvartalet", REPORT_BODY, d)
    assert not is_calendar_report("Alfaverken: Information", "Preliminära siffror för tredje kvartalet. " + REPORT_BODY, d)
    assert not is_calendar_report("Inbjudan till presentation av Alfaverkens tredje kvartal", REPORT_BODY, d)
    # Förvärv samma dag: nyckeltal men ingen rapportperiod (som Indutrade 2026-10-23).
    assert not is_calendar_report("Alfaverken förvärvar Gammateknik",
                                  "Gammateknik har en omsättning om 100 MSEK och EBITA om 20 MSEK.", d)
    # För få nyckeltal.
    assert not is_calendar_report("Tredje kvartalet 2026", "Juli – september 2026. Vi växer.", d)


def test_kalenderregeln_i_listan(tmp_path):
    """Bolaget står i kalendern samma dag och meddelandet kommer från bolaget: rapporten listas.
    En post med rapportrubrik samma dag går före; utan kalenderrad listas ingenting."""
    from report_list import list_scope

    raw = tmp_path / "raw"
    (raw / WEEK / "mfn").mkdir(parents=True)
    texts = {"forvarv.txt": "Rubrik: x\n---\nGammateknik har en omsättning om 100 MSEK och EBITA om 20 MSEK.\n",
             "kv3.txt": "Rubrik: x\n---\n" + REPORT_BODY, "warn.txt": "Rubrik: x\n---\n" + REPORT_BODY}
    for name, text in texts.items():
        (raw / WEEK / "mfn" / name).write_text(text, encoding="utf-8")
    item = lambda t, title, company, path: {"source": "mfn", "source_name": "MFN", "id": path + t,  # noqa: E731
                                           "url": f"https://mfn.example/{path}", "published": f"2026-10-23T{t}:00+02:00",
                                           "title": title, "company": company, "path": f"mfn/{path}", "text_kind": "fulltext"}
    items = [item("06:30", "Alfaverken förvärvar Gammateknik", "alfaverken", "forvarv.txt"),
             item("07:00", "Stark tillväxt; justerad EBITA ökade med 30 %", "alfaverken", "kv3.txt"),
             item("07:05", "Alfaverken: Strong Q3 – initial take", "analyshuset", "kv3.txt"),
             item("07:30", "Betabolaget bedömer att omsättning och resultat för tredje kvartalet 2026 överstiger "
                           "marknadens förväntningar", "betabolaget", "warn.txt")]
    (raw / WEEK / "index.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    data = tmp_path / "data"
    (data / "morning").mkdir(parents=True)
    morning = {"reports_today": [{"ticker": "ALFA", "kind": "rapport"}, {"ticker": "BETA", "kind": "rapport"}]}
    (data / "morning" / "2026-10-23.json").write_text(json.dumps(morning), encoding="utf-8")

    listed = list_scope(WEEK, INSTRUMENTS, load_sector_kpis(), raw, None, None, data)
    assert [(r["ticker"], r["published"][11:16], r["basis"]) for r in listed] == [("ALFA", "07:00", "kalender")]
    assert list_scope(WEEK, INSTRUMENTS, load_sector_kpis(), raw, None, None, tmp_path / "tom") == []
    # En rapportrubrik senare samma dag går före kalenderträffen.
    (raw / WEEK / "mfn" / "q3.txt").write_text("Rubrik: x\n---\n" + REPORT_BODY, encoding="utf-8")
    items.append(item("08:00", "Alfaverken delårsrapport januari–september 2026", "alfaverken", "q3.txt"))
    (raw / WEEK / "index.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    listed = list_scope(WEEK, INSTRUMENTS, load_sector_kpis(), raw, None, None, data)
    assert [(r["ticker"], r["published"][11:16], r["basis"]) for r in listed] == [("ALFA", "08:00", "rubrik")]


def test_per_aktie_i_cent_och_ore():
    from decimal import Decimal

    from validate_results import unit_factor

    assert unit_factor("Euro cent") == (Decimal("0.01"), "EUR")
    assert unit_factor("US cent") == (Decimal("0.01"), "USD")
    assert unit_factor("öre") == (Decimal("0.01"), "SEK")
    text = _normalize_ws("Juli - september 2026\nResultat per aktie 27,1 Euro cent (24,0)\n")
    r = Result()
    check_kpi(kpi("resultat-per-aktie", 27.1, "Euro cent", 24.0, None, "kvartal",
                  "Resultat per aktie 27,1 Euro cent (24,0)"), "per_aktie", text, "t", r)
    assert r.errors == []
    r = Result()
    check_kpi(kpi("nettoomsattning", 27.1, "Euro cent", 24.0, None, "kvartal",
                  "Resultat per aktie 27,1 Euro cent (24,0)"), "belopp", text, "t", r)
    assert any("enheten" in e for e in r.errors)  # cent bara per aktie


def test_omskickad_rapport_far_inget_andra_utfall(tmp_path):
    """Samma rapport publicerad igen (ny adress) efter morgonens fönster: uppdateringen hoppar över bolaget."""
    from report_list import create_window, list_scope

    raw, morning_raw, results_dir = _morning_setup(tmp_path)
    tz = ZoneInfo("Europe/Stockholm")
    idx = json.loads((morning_raw / WEEK / "index.json").read_text(encoding="utf-8"))
    idx["items"].append({**idx["items"][0], "id": "alfa-2", "url": "https://mfn.example/alfa-1",
                         "published": "2026-10-22T08:25:00+02:00"})
    (morning_raw / WEEK / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    scope = "2026-10-22T1745"
    create_window(scope, datetime(2026, 10, 22, 17, 45, tzinfo=tz), results_dir)
    assert [r["ticker"] for r in list_scope(scope, INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir)] \
        == ["ALFA"]  # morgonen har inget godkänt utfall: omskicket tas med
    (results_dir / "morning" / "2026-10-22" / "omgang-1.json").write_text(
        json.dumps({"week": "2026-10-22", "batch": 1, "reports": [alfa()]}), encoding="utf-8")
    day, _ = results.build("2026-10-22", INSTRUMENTS, datetime(2026, 10, 22, 8, 15, tzinfo=tz), raw, results_dir,
                           tmp_path / "consensus", morning_raw)
    (results_dir / "morning" / "2026-10-22.json").write_text(json.dumps(day), encoding="utf-8")
    assert list_scope(scope, INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir) == []
    # Morgonens egen lista påverkas inte av sin egen fil.
    assert [r["ticker"] for r in list_scope("2026-10-22", INSTRUMENTS, load_sector_kpis(), raw, morning_raw,
                                            results_dir)] == ["ALFA"]


def test_uppdatering_listas_slas_ihop_och_tas_med_i_veckan(tmp_path):
    from report_list import create_window, list_scope

    raw, morning_raw, results_dir = _morning_setup(tmp_path)  # morgonfönster 21 okt 08:10 - 22 okt 08:10
    tz = ZoneInfo("Europe/Stockholm")
    scope = "2026-10-23T1745"
    create_window(scope, datetime(2026, 10, 23, 17, 45, tzinfo=tz), results_dir)
    listed = list_scope(scope, INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir)
    assert [r["ticker"] for r in listed] == ["BETA"]  # Alfaverken (22 okt 07:00) låg i morgonens fönster
    (results_dir / "updates" / scope / "omgang-1.json").write_text(
        json.dumps({"week": scope, "batch": 1, "reports": [beta()]}), encoding="utf-8")
    upd, problems = results.build(scope, INSTRUMENTS, datetime(2026, 10, 23, 17, 50, tzinfo=tz), raw, results_dir,
                                  tmp_path / "consensus", morning_raw)
    assert problems == [] and upd["period"] == {"start": "2026-10-23", "end": "2026-10-23"}
    assert validate_results(upd, scope, INSTRUMENTS, raw, morning_raw, results_dir).errors == []
    assert results.output_path(scope, results_dir) == results_dir / "updates" / f"{scope}.json"
    results.output_path(scope, results_dir).write_text(json.dumps(upd), encoding="utf-8")
    # Måndagens uppsamling hoppar över bolaget och tar med uppdateringens utfall.
    assert [r["ticker"] for r in list_scope(WEEK, INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir)] \
        == ["ALFA"]
    week, _ = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, tmp_path / "consensus", morning_raw)
    assert [(r["ticker"], r["scope"]) for r in week["reports"]] == [("BETA", scope)]
    assert validate_results(week, WEEK, INSTRUMENTS, raw, morning_raw, results_dir).errors == []


def test_veckan_hoppar_over_morgonens_bolag_och_tar_med_dem(tmp_path):
    from report_list import list_scope

    raw, morning_raw, results_dir = _morning_setup(tmp_path)
    (results_dir / "morning" / "2026-10-22").mkdir(parents=True, exist_ok=True)
    (results_dir / "morning" / "2026-10-22" / "omgang-1.json").write_text(
        json.dumps({"week": "2026-10-22", "batch": 1, "reports": [alfa()]}), encoding="utf-8")
    tz = ZoneInfo("Europe/Stockholm")
    day, _ = results.build("2026-10-22", INSTRUMENTS, datetime(2026, 10, 22, 8, 15, tzinfo=tz), raw, results_dir,
                           tmp_path / "consensus", morning_raw)
    assert [r["scope"] for r in day["reports"]] == ["2026-10-22"]
    (results_dir / "morning" / "2026-10-22.json").write_text(json.dumps(day), encoding="utf-8")
    week_list = list_scope(WEEK, INSTRUMENTS, load_sector_kpis(), raw, morning_raw, results_dir)
    assert [r["ticker"] for r in week_list] == ["BETA"]  # Alfaverken klarades redan på morgonen
    (results_dir / WEEK).mkdir()
    (results_dir / WEEK / "omgang-1.json").write_text(json.dumps(batch([beta()])), encoding="utf-8")
    week, problems = results.build(WEEK, INSTRUMENTS, NOW, raw, results_dir, tmp_path / "consensus", morning_raw)
    assert problems == []
    assert [(r["ticker"], r["scope"]) for r in week["reports"]] == [("ALFA", "2026-10-22"), ("BETA", WEEK)]
    assert validate_results(week, WEEK, INSTRUMENTS, raw, morning_raw, results_dir).errors == []
