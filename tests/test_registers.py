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


# Blankning som den såg ut i äldre analysfiler (till 2026-11-11), påhittad.
OLD_SHORT_POSITIONS = {
    "available": True, "note": None, "start_as_of": "2026-10-04", "end_as_of": "2026-10-11",
    "changes": [{"ticker": "FJLH B", "name": "Fjällhem Fastigheter", "start_pct": 0.0, "end_pct": 0.8, "change_pp": 0.8,
                 "last_change_date": "2026-10-06",
                 "holders": [{"holder": "Fond A", "start_pct": 0.0, "end_pct": 0.8, "change_pp": 0.8, "date": "2026-10-06"}]}],
}


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


def test_sen_anmalan_over_tre_arbetsdagar(tmp_path, instruments):
    late = insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0, day="2026-10-07")
    late["transaction_date"] = "2026-10-01"  # tor -> ons: fre, mån, tis, ons = 4
    ok = insyn_row("Fjällhem AB", FJLH, "Bo B", "Förvärv", 3000, 50.0, day="2026-10-07")
    ok["transaction_date"] = "2026-10-02"    # fre -> ons: 3
    insyn_days(tmp_path, {"2026-10-07": [late, ok]})
    [c] = build(tmp_path, instruments)["insider"]["companies"]
    flags = {t["person"]: (t["business_days_to_publication"], t["late_report"]) for t in c["transactions"]}
    assert flags == {"Anna A": (4, True), "Bo B": (3, False)}


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
    insyn_days(tmp_path, {"2026-10-06": [insyn_row("Fjällhem AB", FJLH, "Anna A", "Förvärv", 3000, 50.0)]}, days=range(5, 11))
    a = build(tmp_path, instruments)
    assert "short_positions" not in a and "short_aggregate" not in a  # blankning räknas inte sedan 2026-11-11
    # Äldre analysfiler har blankningen kvar; den kontrolleras där den finns.
    a["short_positions"] = OLD_SHORT_POSITIONS
    a["short_aggregate"] = {"available": False, "note": "ingen bild för förra veckan", "start_as_of": None,
                            "end_as_of": None, "changes": []}
    assert validate.validate_analysis(a, "2026-W41").errors == []
    bad = copy.deepcopy(a)
    breaker(bad)
    assert any(message in e for e in validate.validate_analysis(bad, "2026-W41").errors)


