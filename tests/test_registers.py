"""Tester för scripts/registers.py med påhittad FI-data för mockbolagen (inga nätanrop)."""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import registers  # noqa: E402
import validate  # noqa: E402

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=registers.STOCKHOLM)
FJLH, FJLH_A, KUST, NRSK = "SEMOCKFJLHB1", "SEMOCKFJLHA1", "SEMOCKKUSTA1", "SEMOCKNRSK01"


@pytest.fixture(scope="module")
def instruments():
    return validate.load_instruments(validate.MOCK_DIR / "instruments.csv")


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def pos(holder, isin, pct, d, issuer="Fjällhem AB"):
    return {"holder": holder, "issuer": issuer, "isin": isin, "pct": pct, "date": d, "comment": ""}


def snapshot(raw, as_of, week, aggregate, current):
    write(raw / week / "fi" / f"blankning-{as_of}.json",
          {"as_of": as_of, "fetched_at": f"{as_of}T06:40:00+02:00", "sources": {}, "aggregate": aggregate, "current": current})


def history(raw, positions, fetched="2026-10-12"):
    write(raw / "fi-historik" / f"blankning-historik-{fetched}.json",
          {"fetched_at": f"{fetched}T10:00:00+02:00", "source": "x", "since": "2025-01-01", "positions": positions})


def insyn_row(issuer, isin, person, nature, volume, price, day="2026-10-06", currency="SEK", program=False,
              status="Aktuell", instrument_type="Aktie"):
    return {"published": f"{day} 09:00:00", "published_date": day, "issuer": issuer, "lei": "", "reporter": person,
            "person": person, "position": "VD", "closely_associated": "", "correction": "", "correction_note": "",
            "initial_report": False, "share_program": program, "nature": nature, "instrument_type": instrument_type,
            "instrument_name": issuer, "isin": isin, "transaction_date": day, "volume": volume, "volume_unit": "Antal",
            "price": price, "currency": currency, "venue": "", "status": status}


def insyn_days(raw, rows_by_day, days=range(5, 12)):
    for d in days:
        day = f"2026-10-{d:02d}"
        write(raw / "2026-W41" / "fi" / f"insyn-{day}.json",
              {"publication_date": day, "fetched_at": "", "source": "x", "rows": rows_by_day.get(day, [])})


def build(raw, instruments):
    return registers.build("2026-W41", raw, instruments, NOW)


# --- Blankning ≥ 0,5 % -------------------------------------------------------


def test_forandring_ur_historik_och_ogonblicksbild(tmp_path, instruments):
    history(tmp_path, [
        pos("Fond A", FJLH, 0.8, "2026-09-20"),           # före v40 slut
        pos("Fond A", FJLH, None, "2026-10-07"),          # under 0,5 i v41
        pos("Fond B", FJLH, 0.6, "2026-10-08"),           # ny i v41
        pos("Fond C", KUST, 1.2, "2026-09-01"),           # oförändrad
    ])
    snapshot(tmp_path, "2026-10-11", "2026-W41", [], [pos("Fond B", FJLH, 0.7, "2026-10-09"), pos("Fond C", KUST, 1.2, "2026-09-01")])
    sp = build(tmp_path, instruments)["short_positions"]
    assert sp["available"] and (sp["start_as_of"], sp["end_as_of"]) == ("2026-10-04", "2026-10-11")
    [c] = sp["changes"]  # Kustbanken oförändrad -> inte med
    assert (c["ticker"], c["start_pct"], c["end_pct"], c["change_pp"]) == ("FJLH B", 0.8, 0.7, -0.1)
    assert {h["holder"]: (h["start_pct"], h["end_pct"]) for h in c["holders"]} == {"Fond A": (0.8, 0.0), "Fond B": (0.0, 0.7)}


def test_positionsdatum_per_innehavare_och_senaste_for_bolaget(tmp_path, instruments):
    history(tmp_path, [
        pos("Fond A", FJLH, 0.8, "2026-09-20"),
        pos("Fond A", FJLH, None, "2026-10-07"),  # under 0,5 den 7/10
        pos("Fond B", FJLH, 0.6, "2026-10-08"),   # ny den 8/10
        pos("Fond B", FJLH, 0.7, "2026-10-12"),   # efter veckan – räknas inte
    ])
    snapshot(tmp_path, "2026-10-12", "2026-W42", [], [])
    [c] = build(tmp_path, instruments)["short_positions"]["changes"]
    assert {h["holder"]: h["date"] for h in c["holders"]} == {"Fond A": "2026-10-07", "Fond B": "2026-10-08"}
    assert c["last_change_date"] == "2026-10-08"


def test_sen_anmalan_over_tre_arbetsdagar(tmp_path, instruments):
    late = insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0, day="2026-10-07")
    late["transaction_date"] = "2026-10-01"  # tor -> ons: fre, mån, tis, ons = 4
    ok = insyn_row("Fjällhem AB", FJLH, "Bo B", "Förvärv", 3000, 50.0, day="2026-10-07")
    ok["transaction_date"] = "2026-10-02"    # fre -> ons: 3
    insyn_days(tmp_path, {"2026-10-07": [late, ok]})
    [c] = build(tmp_path, instruments)["insider"]["companies"]
    flags = {t["person"]: (t["business_days_to_publication"], t["late_report"]) for t in c["transactions"]}
    assert flags == {"Anna A": (4, True), "Bo B": (3, False)}


def test_alla_aktieslag_summeras_per_bolag(tmp_path, instruments):
    history(tmp_path, [pos("Fond A", FJLH, 0.6, "2026-10-06"), pos("Fond A", FJLH_A, 0.5, "2026-10-06")])
    [c] = build(tmp_path, instruments)["short_positions"]["changes"]
    assert (c["ticker"], c["end_pct"]) == ("FJLH B", 1.1)


def test_position_som_forsvinner_ur_ogonblicksbild_rakans_som_noll(tmp_path, instruments):
    snapshot(tmp_path, "2026-10-04", "2026-W40", [], [pos("Fond A", FJLH, 0.9, "2026-10-01")])
    snapshot(tmp_path, "2026-10-11", "2026-W41", [], [])
    [c] = build(tmp_path, instruments)["short_positions"]["changes"]
    assert (c["start_pct"], c["end_pct"], c["change_pp"]) == (0.9, 0.0, -0.9)


def test_underlag_som_inte_tacker_veckan_ger_ej_tillganglig(tmp_path, instruments):
    snapshot(tmp_path, "2026-10-09", "2026-W41", [], [pos("Fond A", FJLH, 0.9, "2026-10-01")])
    sp = build(tmp_path, instruments)["short_positions"]
    assert not sp["available"] and "2026-10-09" in sp["note"] and sp["changes"] == []


def test_bolag_utanfor_listan_ignoreras(tmp_path, instruments):
    history(tmp_path, [pos("Fond A", "SE0000000000", 2.0, "2026-10-06", issuer="Annat AB")])
    assert build(tmp_path, instruments)["short_positions"]["changes"] == []


# --- Blankning aggregat ------------------------------------------------------


def agg(issuer, pct):
    return {"issuer": issuer, "lei": "", "pct": pct, "date": "2026-10-09"}


def test_aggregat_fran_egna_ogonblicksbilder(tmp_path, instruments):
    snapshot(tmp_path, "2026-10-03", "2026-W40", [agg("Fjällhem AB", 1.0), agg("Kustbanken AB (publ)", 0.4)], [])
    snapshot(tmp_path, "2026-10-04", "2026-W40", [agg("Fjällhem AB", 1.1), agg("Kustbanken AB (publ)", 0.4)], [])
    snapshot(tmp_path, "2026-10-11", "2026-W41",
             [agg("Fjällhem AB", 1.6), agg("Kustbanken AB (publ)", 0.2), agg("Okänt AB", 3.0)],
             [pos("Fond A", FJLH, 0.6, "2026-10-09")])  # Fjällhem kopplas via ISIN, Kustbanken via namn
    ag = build(tmp_path, instruments)["short_aggregate"]
    assert ag["available"] and (ag["start_as_of"], ag["end_as_of"]) == ("2026-10-04", "2026-10-11")
    assert [(c["ticker"], c["start_pct"], c["end_pct"], c["change_pp"]) for c in ag["changes"]] == [
        ("FJLH B", 1.1, 1.6, 0.5), ("KUST A", 0.4, 0.2, -0.2)]


def test_aggregat_utan_forra_veckans_bild_ar_ej_tillgangligt(tmp_path, instruments):
    snapshot(tmp_path, "2026-10-11", "2026-W41", [agg("Fjällhem AB", 1.6)], [])
    ag = build(tmp_path, instruments)["short_aggregate"]
    assert not ag["available"] and "förra veckans slut" in ag["note"] and "2026-10-11" in ag["note"]


# --- Insyn -------------------------------------------------------------------


def test_insyn_filter_trosklar_och_flera_insynspersoner(tmp_path, instruments):
    insyn_days(tmp_path, {
        "2026-10-06": [
            insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0),          # 150 000 kr – med
            insyn_row("Fjällhem AB", FJLH, "Bo B", "Förvärv", 1000, 101.0),           # 101 000 kr – med
            insyn_row("Fjällhem AB", FJLH, "Bo B", "Förvärv", 1000, 100.0),           # exakt 100 000 – inte över
            insyn_row("Kustbanken AB", KUST, "Cia C", "Avyttring", 10000, 20.0),      # 200 000 kr – med
            insyn_row("Kustbanken AB", KUST, "Cia C", "Avyttring", 10000, 20.0),      # samma person igen
            insyn_row("Kustbanken AB", KUST, "Dan D", "Förvärv", 10000, 20.0, program=True),
            insyn_row("Kustbanken AB", KUST, "Dan D", "Tilldelning", 10000, 20.0),
            insyn_row("Kustbanken AB", KUST, "Dan D", "Förvärv", 10000, 20.0, currency="EUR"),
            insyn_row("Kustbanken AB", KUST, "Dan D", "Förvärv", 10000, 20.0, status="Reviderad"),
            insyn_row("Kustbanken AB", KUST, "Dan D", "Förvärv", 10000, 20.0, instrument_type="Option"),
            insyn_row("Annat AB", "SE0000000000", "Eva E", "Förvärv", 10000, 20.0),
        ],
    }, days=range(5, 10))  # lördag–söndag saknas
    ins = build(tmp_path, instruments)["insider"]
    by = {c["ticker"]: c for c in ins["companies"]}
    f, k = by["FJLH B"], by["KUST A"]
    assert (f["buy_value_sek"], f["buyers"], f["cluster_buy"]) == (251000, ["Anna A", "Bo B"], True)
    assert (k["sell_value_sek"], k["sellers"], k["cluster_sell"], k["cluster_buy"]) == (400000, ["Cia C"], False, False)
    assert k["net_value_sek"] == -400000
    assert ins["excluded"] == {"other_nature": 1, "not_share": 1, "share_program": 1, "not_current": 1,
                               "non_sek": 1, "below_threshold": 1, "not_in_list": 1}
    assert ins["days_missing"] == ["2026-10-10", "2026-10-11"]


def test_affar_utanfor_handelsplats_markeras_men_raknas_med(tmp_path, instruments):
    off = insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0)
    off["venue"] = "Utanför handelsplats"
    on = insyn_row("Fjällhem AB", FJLH, "Bo B", "Förvärv", 3000, 50.0)
    on["venue"] = "NASDAQ STOCKHOLM AB"
    insyn_days(tmp_path, {"2026-10-06": [off, on]})
    [c] = build(tmp_path, instruments)["insider"]["companies"]
    assert c["buy_value_sek"] == 300000  # båda räknas
    assert {t["person"]: t["off_exchange"] for t in c["transactions"]} == {"Anna A": True, "Bo B": False}
    assert validate.validate_analysis(build(tmp_path, instruments), "2026-W41").errors == []


def test_forsaljning_i_malbolag_markeras_under_uppkopsbud(tmp_path, instruments):
    insyn_days(tmp_path, {"2026-10-06": [
        insyn_row("Kustbanken AB", KUST, "Cia C", "Avyttring", 10000, 20.0),
        insyn_row("Fjällhem AB", FJLH, "Anna A", "Avyttring", 10000, 20.0),
    ]})
    ins = registers.build("2026-W41", tmp_path, instruments, NOW, under_offer={"KUST A"})["insider"]
    by = {c["ticker"]: c["under_offer"] for c in ins["companies"]}
    assert by == {"KUST A": True, "FJLH B": False}


def test_main_laser_bid_target_ur_veckofilen(tmp_path, instruments):
    raw = tmp_path / "raw"
    insyn_days(raw, {"2026-10-06": [insyn_row("Kustbanken AB", KUST, "Cia C", "Avyttring", 10000, 20.0)]})
    week_file = tmp_path / "w.json"
    week_file.write_text(json.dumps({"sectors": [{"items": [{"companies": [
        {"ticker": "KUST A", "bid_target": True}, {"ticker": "FJLH B"}]}]}]}), encoding="utf-8")
    out = tmp_path / "a.json"
    registers.main(["2026-W41", "--raw-dir", str(raw), "--instruments", str(validate.MOCK_DIR / "instruments.csv"),
                    "--out", str(out), "--week-file", str(week_file)], now=NOW)
    [c] = json.loads(out.read_text(encoding="utf-8"))["insider"]["companies"]
    assert c["under_offer"] is True


def test_insyn_publicerad_utanfor_veckan_tas_inte_med(tmp_path, instruments):
    insyn_days(tmp_path, {})
    write(tmp_path / "2026-W41" / "fi" / "insyn-2026-10-04.json",  # felplacerad fil från förra veckan
          {"rows": [insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 5000, 50.0, day="2026-10-04")]})
    assert build(tmp_path, instruments)["insider"]["companies"] == []


# --- Hela kedjan och validering ----------------------------------------------


def test_main_skriver_giltig_fil(tmp_path, instruments):
    raw = tmp_path / "raw"
    history(raw, [pos("Fond A", FJLH, 0.8, "2026-10-06")])
    insyn_days(raw, {"2026-10-06": [insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0)]})
    out = tmp_path / "a.json"
    code = registers.main(["2026-W41", "--raw-dir", str(raw), "--instruments",
                           str(validate.MOCK_DIR / "instruments.csv"), "--out", str(out)], now=NOW)
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert validate.validate_analysis(data, "2026-W41").errors == []


def test_main_kraschar_inte_pa_windows_konsol(tmp_path, instruments, monkeypatch):
    """Utskriften innehåller '≥'; med en cp1252-konsol får det inte bli UnicodeEncodeError."""
    import io
    raw = tmp_path / "raw"
    history(raw, [pos("Fond A", FJLH, 0.8, "2026-10-06")])
    insyn_days(raw, {})
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    code = registers.main(["2026-W41", "--raw-dir", str(raw), "--instruments", str(validate.MOCK_DIR / "instruments.csv"),
                           "--out", str(tmp_path / "a.json")], now=NOW)
    assert code == 0


@pytest.mark.parametrize(
    "breaker, message",
    [
        (lambda a: a["insider"]["companies"][0].update(net_value_sek=1), "net_value_sek"),
        (lambda a: a["insider"]["companies"][0].update(cluster_buy=True), "flera insynspersoner"),
        (lambda a: a["insider"]["companies"][0]["transactions"][0].update(value_sek=90000, volume=1800), "under tröskeln"),
        (lambda a: a["insider"]["companies"][0]["transactions"][0].update(value_sek=160000), "volym × pris"),
        (lambda a: a["short_positions"]["changes"][0].update(change_pp=0.5), "change_pp"),
        (lambda a: a["insider"].update(days_missing=[]), "sju dagar"),
        (lambda a: a["short_positions"]["changes"][0]["holders"][0].update(date="2026-10-04"), "utanför veckan"),
        (lambda a: a["short_positions"]["changes"][0].update(last_change_date="2026-10-05"), "last_change_date"),
        (lambda a: a["insider"]["companies"][0]["transactions"][0].update(late_report=True), "sen anmälan"),
    ],
)
def test_validering_fangar_inkonsekvent_analys(tmp_path, instruments, breaker, message):
    history(tmp_path, [pos("Fond A", FJLH, 0.8, "2026-10-06")])
    insyn_days(tmp_path, {"2026-10-06": [insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0)]}, days=range(5, 11))
    a = build(tmp_path, instruments)
    assert validate.validate_analysis(a, "2026-W41").errors == []
    bad = copy.deepcopy(a)
    breaker(bad)
    assert any(message in e for e in validate.validate_analysis(bad, "2026-W41").errors)


def test_laget_vid_dagens_slut_ur_en_senare_ogonblicksbild():
    """Manuell nedladdning 2/11 2026 med en ändring samma dag: läget vid 1/11 räknas fram ur den (rader
    daterade senast 1/11 gällde redan; senare rader tas från bilden före)."""
    from datetime import date

    old = {"as_of": "2026-10-27",
           "aggregate": [{"issuer": "Fjällhem AB", "lei": "L1", "pct": 2.0, "date": "2026-10-20"},
                         {"issuer": "Kustbanken AB", "lei": "L2", "pct": 1.0, "date": "2026-10-21"}],
           "current": [{"holder": "Exempelfonden LP", "issuer": "Fjällhem AB", "isin": "SE0000000001", "pct": 1.0,
                        "date": "2026-10-20", "comment": ""}]}
    new = {"as_of": "2026-11-02",
           "aggregate": [{"issuer": "Fjällhem AB", "lei": "L1", "pct": 2.6, "date": "2026-10-29"},
                         {"issuer": "Kustbanken AB", "lei": "L2", "pct": 1.4, "date": "2026-11-02"},
                         {"issuer": "Nybolaget AB", "lei": "L3", "pct": 0.3, "date": "2026-11-02"}],
           "current": [{"holder": "Exempelfonden LP", "issuer": "Fjällhem AB", "isin": "SE0000000001", "pct": 1.6,
                        "date": "2026-11-02", "comment": ""}]}
    at = registers.snapshot_at([old, new], date(2026, 11, 1))
    assert at["as_of"] == "2026-11-01" and at["derived_from"] == "2026-11-02"
    assert {r["issuer"]: r["pct"] for r in at["aggregate"]} == {"Fjällhem AB": 2.6, "Kustbanken AB": 1.0}
    assert [r["pct"] for r in at["current"]] == [1.0]
    # Finns en bild för dagen, eller ingen senare, används den som den är.
    assert registers.snapshot_at([old, new], date(2026, 11, 2)) is new
    assert registers.snapshot_at([old], date(2026, 11, 1)) is old
    assert registers.snapshot_at([new], date(2026, 10, 1))["aggregate"] == []
