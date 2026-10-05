"""Tester för scripts/validate.py – utgår från mockveckan och bryter den på ett ställe i taget."""

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402

MOCK_WEEK = ROOT / "data" / "mock" / "weeks" / "2026-W40.json"
MOCK_PRICES = ROOT / "data" / "mock" / "prices" / "2026-W40.json"


@pytest.fixture(scope="module")
def instruments():
    return validate.load_instruments(validate.MOCK_DIR / "instruments.csv")


@pytest.fixture(scope="module")
def sector_ids():
    return validate.load_sector_ids()


@pytest.fixture
def week():
    return json.loads(MOCK_WEEK.read_text(encoding="utf-8"))


def run(data, instruments, sector_ids, raw_dir=None, expected="2026-W40"):
    return validate.validate_week(data, expected, instruments, sector_ids, raw_dir=raw_dir)


def item(data, item_id):
    for s in data["sectors"]:
        for it in s["items"]:
            if it["id"] == item_id:
                return it
    raise KeyError(item_id)


# --- Grundfall -------------------------------------------------------------


def test_mockveckan_ar_giltig(week, instruments, sector_ids):
    assert run(week, instruments, sector_ids).errors == []


def test_mockpriser_ar_giltiga():
    data = json.loads(MOCK_PRICES.read_text(encoding="utf-8"))
    assert validate.validate_prices(data, "2026-W40").errors == []


def test_mockdata_namner_inga_riktiga_instrument(week):
    """Repot är publikt – påhittade nyheter får inte handla om riktiga bolag."""
    real = validate.load_instruments()
    real_names = {n.casefold() for i in real.values() for n in (i.namn, *i.alias)}
    for s in week["sectors"]:
        for it in s["items"]:
            for c in it["companies"]:
                assert c["ticker"] not in real
                assert c["name"].casefold() not in real_names


def test_validering_via_cli_godkanner_mockdata(capsys):
    assert validate.main([str(MOCK_WEEK), str(MOCK_PRICES)]) == 0


def test_iso_vecka_period():
    assert validate.iso_week_period("2026-W40") == (
        validate.date(2026, 9, 28),
        validate.date(2026, 10, 4),
    )


# --- Siffror ---------------------------------------------------------------


@pytest.mark.parametrize(
    "summary, quote",
    [
        ("2 000 miljoner", "2000 miljoner"),
        ("2 000 miljoner", "2 000 miljoner"),  # hårt mellanslag
        ("12 500 användare", "12 500 användare"),  # smalt hårt mellanslag
        ("1,75 procent", "1.75 %"),
        ("2,1 %", "2,1 procent"),
        ("1,50", "1,5"),  # samma värde, inte avrundning
        ("−3,2 %", "-3,2 procent"),  # typografiskt minus
        ("–3,2 %", "−3,2 %"),  # tankstreck som minus
        ("minskade 3,2 procent", "förändring −3,2 %"),  # positivt belopp ur negativt
        ("2,1", "2,1 procent"),  # enheten får utelämnas
    ],
)
def test_normaliserade_siffror_godkanns(summary, quote):
    assert validate.unsupported_numbers(summary, [quote]) == []


@pytest.mark.parametrize(
    "summary, quote",
    [
        ("1,8 procent", "1,75 procent"),  # avrundad
        ("2,1 procent", "2,13 procent"),  # avrundad
        ("2 miljarder", "2 000 miljoner"),  # omräknad
        ("12 000 användare", "12 500 användare"),  # avrundad
        ("2,1 procent", "2,1 procentenheter"),  # procentenheter ≠ procent
        ("−3,2 %", "3,2 %"),  # negativt kräver negativt i citatet
    ],
)
def test_avvikande_siffror_underkanns(summary, quote):
    assert validate.unsupported_numbers(summary, [quote]) != []


def test_intervall_ger_tva_positiva_tal():
    assert [n.value for n in validate.extract_numbers("2026–2027")] == [Decimal(2026), Decimal(2027)]


def test_avrundad_siffra_i_sammanfattning_underkanns(week, instruments, sector_ids):
    # Citatet säger 1,75 procent – avrundat till 1,8 ska underkännas.
    item(week, "makro-centralbank-rantebesked")["summary"] = "Styrräntan ligger kvar på cirka 1,8 procent."
    res = run(week, instruments, sector_ids)
    assert any("makro-centralbank-rantebesked: siffror utan exakt stöd" in e and "1,8 procent" in e for e in res.errors)


def test_avrundad_siffra_i_sektorsammanfattning_underkanns(week, instruments, sector_ids):
    week["sectors"][0]["summary"] = "Räntan ligger kvar på 1,8 procent."
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("makro: sektorsammanfattningen") for e in res.errors)


def test_citat_over_25_ord_underkanns(week, instruments, sector_ids):
    long_quote = " ".join(["ord"] * 26) + " 1,75 procent"
    item(week, "makro-centralbank-rantebesked")["evidence"] = [{"quote": long_quote}]
    res = run(week, instruments, sector_ids)
    assert any("max 25" in e for e in res.errors)


# --- Riktning --------------------------------------------------------------


@pytest.mark.parametrize(
    "summary, quote",
    [
        # Negativt i citatet + ökningsord i sammanfattningen.
        ("Räntenettot ökade 3,2 procent", "Räntenettot förändrades med −3,2 %"),
        ("Räntenettot steg 3,2 procent", "Räntenettot minskade med 3,2 procent"),
        ("Exporten växte med 1,5 procent", "Exporten: -1,5 procent"),
        ("Kronan stärktes 2 procent", "Kronan försvagades med 2 procent"),
        ("Avgiften höjdes med 4 procent", "Avgiften sänks med 4 procent"),
        # Positivt i citatet + minskningsord i sammanfattningen.
        ("Intäkterna minskade 4,5 procent", "Intäkterna ökade med 4,5 procent"),
        ("Aktien sjönk 4,5 procent", "Aktien +4,5 %"),
        ("Priset föll med 7 procent", "Priset steg med 7 procent"),
        ("Räntan sänktes med 0,25 procent", "Räntan höjs med 0,25 procent"),
        ("Valutan försvagades 0,8 procent", "Valutan stärktes med 0,8 procent"),
    ],
)
def test_motsagande_riktning_underkanns(summary, quote):
    assert validate.direction_conflicts(summary, [quote]) != []


@pytest.mark.parametrize(
    "summary, quote",
    [
        ("Räntenettot minskade 3,2 procent", "Räntenettot förändrades med −3,2 %"),
        ("Räntenettot sjönk 3,2 procent", "Räntenettot minskade med 3,2 procent"),
        ("Intäkterna ökade 4,5 procent", "Intäkterna ökade med 4,5 procent"),
        ("Aktien steg 4,5 procent", "Aktien +4,5 %"),
        ("Räntan sänktes till 1,75 procent", "Räntan sänks till 1,75 procent"),
        ("Räntan ligger på 1,75 procent", "Räntan sänks till 1,75 procent"),  # inget riktningsord
        # "till" direkt före talet = nivå, riktningen kontrolleras inte.
        ("räntan höjdes till 2 procent", "räntan är 2 procent"),
        ("Räntan sänktes till 1,75 procent", "Räntan höjs till 1,75 procent"),
        # Två tal i samma mening – varje tal får sitt eget riktningsord.
        (
            "Intäkterna steg 2 procent medan kostnaderna sjönk 3 procent",
            "intäkterna steg med 2 procent, kostnaderna minskade med 3 procent",
        ),
    ],
)
def test_forenlig_riktning_godkanns(summary, quote):
    assert validate.direction_conflicts(summary, [quote]) == []


def test_till_ar_niva_men_med_kontrolleras():
    quote = ["räntan är 2 procent"]
    assert validate.direction_conflicts("räntan sänktes till 2 procent", quote) == []
    assert validate.direction_conflicts("räntan sänktes med 2 procent", quote) != []


def test_riktningsord_i_foregaende_mening_raknas_inte():
    summary = "Intäkterna ökade. Rörelsemarginalen var 3,2 procent"
    assert validate.direction_conflicts(summary, ["marginalen −3,2 %"]) == []


def test_okningsord_om_negativt_tal_underkanns_i_veckofil(week, instruments, sector_ids):
    # Citatet: "Räntenettot förändrades med −3,2 % ..."
    item(week, "bank-kustbanken-kvartal")["headline"] = "Kustbankens räntenetto ökade 3,2 procent"
    res = run(week, instruments, sector_ids)
    assert any("bank-kustbanken-kvartal: riktningen motsäger evidence" in e for e in res.errors)


def test_minskningsord_om_positivt_tal_underkanns_i_sektorsammanfattning(week, instruments, sector_ids):
    # Citatet: "Inflationstakten var 2,1 procent i september." – positivt utan riktningsord.
    week["sectors"][0]["summary"] = "Inflationen sjönk 2,1 procent."
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("makro: sektorsammanfattningens riktning") for e in res.errors)


# --- Datum och period ------------------------------------------------------


@pytest.mark.parametrize("published", ["2026-09-27", "2026-10-05"])
def test_datum_utanfor_veckan_underkanns(week, instruments, sector_ids, published):
    item(week, "makro-statistikbyran-inflation")["published"] = published
    res = run(week, instruments, sector_ids)
    assert any("makro-statistikbyran-inflation: published" in e and "utanför" in e for e in res.errors)


def test_fel_period_och_filnamn_underkanns(week, instruments, sector_ids):
    week["period"]["start"] = "2026-09-27"
    res = run(week, instruments, sector_ids, expected="2026-W41")
    assert any("period ska vara" in e for e in res.errors)
    assert any("matchar inte filnamnet" in e for e in res.errors)


def test_tidsstampel_utan_tidszon_underkanns(week, instruments, sector_ids):
    week["generated_at"] = "2026-10-04T18:00:00"
    res = run(week, instruments, sector_ids)
    assert any("saknar tidszon" in e for e in res.errors)


# --- Bolag och sektorer ----------------------------------------------------


def test_pahittad_ticker_underkanns(week, instruments, sector_ids):
    item(week, "fast-fjallhem-refinansiering")["companies"][0]["ticker"] = "FJLH X"
    res = run(week, instruments, sector_ids)
    assert any("finns inte i instruments.csv" in e for e in res.errors)


def test_fel_yahoo_ticker_underkanns(week, instruments, sector_ids):
    item(week, "fast-fjallhem-refinansiering")["companies"][0]["yahoo_ticker"] = "FJLH-B"
    res = run(week, instruments, sector_ids)
    assert any("yahoo_ticker" in e for e in res.errors)


def test_onoterat_med_ticker_underkanns_av_schemat(week, instruments, sector_ids):
    item(week, "fast-tallmo-forsaljning")["companies"][0]["ticker"] = "TALL"
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("schema:") for e in res.errors)


def test_missad_matchning_via_alias_underkanns(week, instruments, sector_ids):
    item(week, "fast-tallmo-forsaljning")["companies"][0]["name"] = "Norrsken"  # alias i mock-listan
    res = run(week, instruments, sector_ids)
    assert any("markerat onoterat men matchar 'NRSK'" in e for e in res.errors)


def test_okand_sektor_och_egen_sektor_i_impacts(week, instruments, sector_ids):
    week["sectors"][0]["items"][0]["sector_impacts"][0]["sector"] = "makro"
    week["sectors"][1]["id"] = "rymd"
    res = run(week, instruments, sector_ids)
    assert any("egen sektor" in e for e in res.errors)
    assert any("'rymd' finns inte" in e for e in res.errors)


# --- instruments.csv och mfn_slug ------------------------------------------

CSV_HEAD = "namn,alias,ticker,yahoo_ticker,börs"


def write_csv(tmp_path, *rows, head=CSV_HEAD + ",mfn_slug"):
    p = tmp_path / "instruments.csv"
    p.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    return p


def test_mfn_slug_lases_och_ar_valfri(tmp_path):
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,fjallhem-fastigheter", "Albion,,ALBN,ALBN.MOCK,Exempelbörsen,")
    inst = validate.load_instruments(p)
    assert inst["FJLH B"].mfn_slug == "fjallhem-fastigheter"
    assert inst["ALBN"].mfn_slug is None


def test_csv_utan_mfn_slug_kolumn_fungerar(tmp_path):
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen", head=CSV_HEAD)
    assert validate.load_instruments(p)["FJLH B"].mfn_slug is None


@pytest.mark.parametrize("slug", ["Fjallhem", "fjällhem", "fjallhem_fastigheter", "-fjallhem", "fjallhem--ab", "mfn.se/a/fjallhem"])
def test_ogiltig_mfn_slug_underkanns(tmp_path, slug):
    p = write_csv(tmp_path, f"Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,{slug}")
    with pytest.raises(ValueError, match="ogiltig mfn_slug"):
        validate.load_instruments(p)


def test_dubblerad_mfn_slug_underkanns(tmp_path):
    p = write_csv(
        tmp_path,
        "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,fjallhem",
        "Fjällhem pref,,FJLH PREF,FJLH-PREF.MOCK,Exempelbörsen,fjallhem",
    )
    with pytest.raises(ValueError, match="rad 3: mfn_slug 'fjallhem' används redan av 'FJLH B'"):
        validate.load_instruments(p)


def test_projektets_instrumentlistor_ar_giltiga():
    real = validate.load_instruments()
    assert real["SEB A"].sektor == "banker-finans" and real["VOLV B"].sektor == "industri"
    assert real["NOVO B"].sektor is None  # inte i Nasdaq Stockholm-datan – lämnas tom
    mock = validate.load_instruments(validate.MOCK_DIR / "instruments.csv")
    assert mock["FJLH B"].mfn_slug == "fjallhem-fastigheter"
    assert mock["KUST A"].sektor == "banker-finans"


def test_sektor_lases_och_ar_valfri(tmp_path):
    p = write_csv(
        tmp_path,
        "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,fastigheter,",
        "Albion,,ALBN,ALBN.MOCK,Exempelbörsen,,",
        head=CSV_HEAD + ",sektor,mfn_slug",
    )
    inst = validate.load_instruments(p)
    assert inst["FJLH B"].sektor == "fastigheter"
    assert inst["ALBN"].sektor is None


def test_okand_sektor_underkanns(tmp_path):
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,Real Estate,", head=CSV_HEAD + ",sektor,mfn_slug")
    with pytest.raises(ValueError, match="rad 2: okänd sektor 'Real Estate'"):
        validate.load_instruments(p)


# --- Indirekta kopplingar via sektor ---------------------------------------


def test_indirekt_bolag_i_sektor_ur_sector_impacts_godkanns(week, instruments, sector_ids):
    # Kustbanken (banker-finans) är indirekt i en fastighetsnyhet med banker-finans i sector_impacts.
    assert run(week, instruments, sector_ids).errors == []


def test_indirekt_bolag_utan_sektorkoppling_underkanns(week, instruments, sector_ids):
    item(week, "fast-fjallhem-refinansiering")["sector_impacts"] = []
    res = run(week, instruments, sector_ids)
    assert any(
        "fast-fjallhem-refinansiering: bolaget 'Kustbanken' är indirekt kopplat men tillhör sektor 'banker-finans'" in e
        for e in res.errors
    )


def test_indirekt_bolag_i_nyhetens_egen_sektor_godkanns(week, instruments, sector_ids):
    # Albion Biotech (halsovard) är indirekt i en hälsovårdsnyhet utan sector_impacts.
    assert item(week, "halso-norrsken-studie")["sector_impacts"] == []
    assert run(week, instruments, sector_ids).errors == []


def test_direkt_bolag_i_annan_sektor_kontrolleras_inte(week, instruments, sector_ids):
    # Vågnät (telekom) är direkt part i en tekniknyhet – det är tillåtet.
    it = item(week, "teknik-molnbyran-vagnat")
    it["sector_impacts"] = []
    assert run(week, instruments, sector_ids).errors == []


# --- Rådata och mock-spärr -------------------------------------------------


def test_evidence_kontrolleras_mot_radata(week, instruments, sector_ids, tmp_path):
    week = copy.deepcopy(week)
    week["mock"] = False
    raw = tmp_path / "2026-W40"
    for s in week["sectors"]:
        for it in s["items"]:
            f = raw / it["source"]["raw_file"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("Inledning.\n" + "\n".join(e["quote"] for e in it["evidence"]), encoding="utf-8")
    assert run(week, instruments, sector_ids, raw_dir=tmp_path).errors == []

    (raw / "statistikbyran" / "inflation.txt").write_text("Inflationstakten var 2,3 procent i september.", encoding="utf-8")
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("citatet finns inte i källtexten" in e for e in res.errors)


def test_mockvecka_kontrolleras_inte_mot_riktig_radata_for_samma_vecka(week, instruments, sector_ids, tmp_path):
    (tmp_path / "2026-W40" / "mfn").mkdir(parents=True)  # riktig rådata för vecka 40 finns
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert res.errors == []


def test_saknad_radata_ger_varning_inte_fel(week, instruments, sector_ids, tmp_path):
    week["mock"] = False
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert res.errors == []
    assert any("rådata saknas" in w for w in res.warnings)


def test_mockfil_utanfor_mockmappen_underkanns(week, tmp_path, capsys):
    path = tmp_path / "2026-W40.json"
    path.write_text(json.dumps(week), encoding="utf-8")
    assert validate.main([str(path)]) == 1
    assert "får inte vara mock" in capsys.readouterr().out
