"""Tester för scripts/validate.py – utgår från mockveckan och bryter den på ett ställe i taget."""

import copy
from datetime import date
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


def test_engelska_tal_komma_som_tusentalsavgransare_och_punkt_som_decimaltecken():
    vals = lambda t, lang="en": [n.value for n in validate.extract_numbers(t, lang)]  # noqa: E731
    assert vals("Revenue of $1,234.5 million (2025: $987.6 million)") == [
        Decimal("1234.5"), Decimal(2025), Decimal("987.6")]
    assert vals("Sales of 412,345 oz, up 12%, and EPS of $0.85 (0.33)") == [
        Decimal(412345), Decimal(12), Decimal("0.85"), Decimal("0.33")]
    assert vals("1,234,567 tonnes") == [Decimal(1234567)]
    assert vals("cash of $1,234, compared with") == [Decimal(1234)]
    assert [n.percent for n in validate.extract_numbers("up 12 percent", "en")] == [True]
    # Ett komma utan exakt tre siffror efter är ett skiljetecken.
    assert vals("in 2026, 15 sites") == [Decimal(2026), Decimal(15)]
    # Svenskt skrivsätt (standard och "sv") som förut.
    assert vals("1,25 MSEK", None) == [Decimal("1.25")]
    assert vals("1,250 MSEK", "sv") == [Decimal("1.250")]


def test_engelsk_rapport_valideras_med_engelskt_skrivsatt():
    from validate_results import check_kpi

    text = "Third quarter 2026: Revenue was $1,234.5 million compared to $1,100.2 million in the third quarter of 2025."
    k = {"id": "nettoomsattning", "value": 1234.5, "unit": "MUSD", "previous_value": 1100.2, "change_pct": None,
         "period_kind": "kvartal", "quote": "Revenue was $1,234.5 million compared to $1,100.2 million"}
    res = validate.Result()
    check_kpi(k, "belopp", text, "ALFA nettoomsattning", res, "en")
    assert res.errors == []
    res = validate.Result()
    check_kpi(k, "belopp", text, "ALFA nettoomsattning", res)  # svenskt skrivsätt: 1,234 blir 1.234
    assert any("värdet 1234.5 finns inte i citatet" in e for e in res.errors)


def test_punkt_som_tusentalsavgransare_i_belopp():
    vals = lambda t: [n.value for n in validate.extract_numbers(t)]  # noqa: E731
    assert vals("Omsättningen ökade till 2.500 MSEK (2.342).") == [Decimal(2500), Decimal(2342)]
    assert vals("Rörelseresultatet blev 1.204 Mkr") == [Decimal(1204)]
    # Utan enhet, eller med procent, är punkten fortfarande decimaltecken.
    assert vals("ökade 2.500 gånger") == [Decimal("2.500")]
    assert vals("marginal 12.345 %") == [Decimal("12.345")]
    assert vals("ökade 2.5 procent") == [Decimal("2.5")]


def test_kolon_som_decimaltecken_men_inte_klockslag():
    vals = lambda t: [n.value for n in validate.extract_numbers(t)]  # noqa: E731
    assert vals("Resultat per aktie var 0:21 SEK (1:26).") == [Decimal("0.21"), Decimal("1.26")]
    assert vals("Vinst per aktie 3:05 kr") == [Decimal("3.05")]
    assert vals("Rapporten publiceras kl 08:00 och telefonkonferens 10:30.") == [
        Decimal(8), Decimal(0), Decimal(10), Decimal(30)]


def test_avrundad_siffra_i_sammanfattning_underkanns(week, instruments, sector_ids):
    # Citatet säger 2,1 procent – avrundat till 2 ska underkännas.
    item(week, "makro-statistikbyran-inflation")["summary"] = "Inflationen var cirka 2 procent."
    res = run(week, instruments, sector_ids)
    assert any("makro-statistikbyran-inflation: siffror utan exakt stöd" in e and "2 procent" in e for e in res.errors)


def test_avrundad_siffra_i_sektorsammanfattning_underkanns(week, instruments, sector_ids):
    week["sectors"][0]["summary"] = "Räntan ligger kvar på 1,8 procent."
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("makro: sektorsammanfattningen") for e in res.errors)


def test_citat_over_25_ord_underkanns(week, instruments, sector_ids):
    long_quote = " ".join(["ord"] * 26) + " 2,1 procent"
    item(week, "makro-statistikbyran-inflation")["evidence"] = [{"quote": long_quote}]
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


def test_ej_i_listan_med_ticker_underkanns_av_schemat(week, instruments, sector_ids):
    item(week, "fast-tallmo-forsaljning")["companies"][0]["ticker"] = "TALL"
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("schema:") for e in res.errors)


def test_missad_matchning_via_alias_underkanns(week, instruments, sector_ids):
    item(week, "fast-tallmo-forsaljning")["companies"][0]["name"] = "Norrsken"  # alias i mock-listan
    res = run(week, instruments, sector_ids)
    assert any("markerat 'ej i listan' men matchar 'NRSK'" in e for e in res.errors)


def test_gamla_vardet_onoterat_underkanns(week, instruments, sector_ids):
    item(week, "fast-tallmo-forsaljning")["companies"][0]["match"] = "onoterat"
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("schema:") and "onoterat" in e for e in res.errors)


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


def test_avnoterad_med_datum_och_kalla(tmp_path):
    """avnoterad (sista handelsdag) och avnoterad_kalla läses; listed hoppar över bolaget efter dagen."""
    head = CSV_HEAD + ",avnoterad,avnoterad_kalla"
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,2026-10-27,https://exempel.test/fjallhem-avnoteras",
                  "Albion,,ALBN,ALBN.MOCK,Exempelbörsen,,", head=head)
    inst = validate.load_instruments(p)
    assert inst["FJLH B"].avnoterad == date(2026, 10, 27)
    assert inst["FJLH B"].avnoterad_kalla == "https://exempel.test/fjallhem-avnoteras"
    assert inst["ALBN"].avnoterad is None and inst["ALBN"].avnoterad_kalla is None
    assert set(validate.listed(inst, date(2026, 10, 27))) == {"FJLH B", "ALBN"}  # sista handelsdagen räknas
    assert set(validate.listed(inst, date(2026, 10, 28))) == {"ALBN"}
    for row, msg in [("2026-10-27,", "anges tillsammans"), (",https://exempel.test/x", "anges tillsammans"),
                     ("27/10/2026,https://exempel.test/x", "ogiltigt datum"), ("2026-10-27,exempel.test/x", "https-adress")]:
        with pytest.raises(ValueError, match=msg):
            validate.load_instruments(write_csv(tmp_path, f"Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,{row}", head=head))


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


def test_analytikerlankar_lases_och_kontrolleras(tmp_path):
    head = CSV_HEAD + ",placera_tagg,afv_aktie"
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,fjallhem,fjallhem-fastigheter",
                  "Albion,,ALBN,ALBN.MOCK,Exempelbörsen,,", head=head)
    inst = validate.load_instruments(p)
    assert inst["FJLH B"].analyst_links() == {"placera": "https://www.placera.se/taggar/fjallhem",
                                               "affarsvarlden": "https://www.affarsvarlden.se/aktie/fjallhem-fastigheter"}
    assert inst["ALBN"].analyst_links() == {"placera": None, "affarsvarlden": None}
    p = write_csv(tmp_path, "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,https://www.placera.se/taggar/fjallhem,", head=head)
    with pytest.raises(ValueError, match="ogiltig placera_tagg"):
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


def test_isin_lases_som_lista_och_ar_valfri(tmp_path):
    p = write_csv(
        tmp_path,
        "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,SE0000000001|SE0000000002,",
        "Albion,,ALBN,ALBN.MOCK,Exempelbörsen,,",
        head=CSV_HEAD + ",isin,mfn_slug",
    )
    inst = validate.load_instruments(p)
    assert inst["FJLH B"].isin == ("SE0000000001", "SE0000000002")
    assert inst["ALBN"].isin == ()


@pytest.mark.parametrize("isin", ["SE000000000", "se0000000001", "SE00000000AB", "SE0000000001|X"])
def test_ogiltig_isin_underkanns(tmp_path, isin):
    p = write_csv(tmp_path, f"Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,{isin},", head=CSV_HEAD + ",isin,mfn_slug")
    with pytest.raises(ValueError, match="ogiltig ISIN"):
        validate.load_instruments(p)


def test_isin_pa_tva_rader_underkanns(tmp_path):
    p = write_csv(
        tmp_path,
        "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen,SE0000000001,",
        "Kustbanken,,KUST A,KUST-A.MOCK,Exempelbörsen,SE0000000001,",
        head=CSV_HEAD + ",isin,mfn_slug",
    )
    with pytest.raises(ValueError, match="rad 3: ISIN 'SE0000000001' används redan av 'FJLH B'"):
        validate.load_instruments(p)


def test_projektets_lista_har_isin_for_stockholmsbolagen():
    real = validate.load_instruments()
    assert real["SEB A"].isin == ("SE0000148884", "SE0000120784")
    assert all(i.isin for i in real.values() if i.bors == "Nasdaq Stockholm")


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


# --- Sentiment ---------------------------------------------------------------


def sentiment(week, item_id, **kw):
    s = item(week, item_id)["sentiment"]
    s.update(kw)
    return s


def test_mockveckans_sentiment_ar_giltigt(week, instruments, sector_ids):
    assert all("sentiment" in it for s in week["sectors"] for it in s["items"])
    assert run(week, instruments, sector_ids).errors == []


def test_basis_som_pekar_utanfor_evidence_underkanns(week, instruments, sector_ids):
    sentiment(week, "bank-kustbanken-kvartal", basis=[0, 3])
    res = run(week, instruments, sector_ids)
    assert any("basis pekar på citat som inte finns: [3]" in e for e in res.errors)


def test_siffra_maste_finnas_i_just_de_refererade_citaten(week, instruments, sector_ids):
    # 2027 finns i citat 1, men motiveringen refererar bara citat 0.
    sentiment(week, "fast-fjallhem-refinansiering", rationale="Obligationer som förfaller under 2027 ersätts.", basis=[0])
    res = run(week, instruments, sector_ids)
    assert any("siffror som inte finns i de refererade citaten: ['2027']" in e for e in res.errors)
    sentiment(week, "fast-fjallhem-refinansiering", basis=[0, 1])
    assert run(week, instruments, sector_ids).errors == []


def test_riktning_i_motiveringen_maste_stamma_med_citaten(week, instruments, sector_ids):
    # Citatet: "Räntenettot förändrades med −3,2 % …"
    sentiment(week, "bank-kustbanken-kvartal", rationale="Räntenettot ökade med 3,2 procent mot föregående kvartal.")
    res = run(week, instruments, sector_ids)
    assert any("motsäger riktningen" in e for e in res.errors)


def test_motivering_utan_gemensamt_innehallsord_underkanns(week, instruments, sector_ids):
    sentiment(week, "halso-norrsken-studie", rationale="Ett bra besked för framtiden.")
    res = run(week, instruments, sector_ids)
    assert any("delar inget innehållsord" in e for e in res.errors)


@pytest.mark.parametrize(
    "rationale",
    [
        "Fas 3-studien nådde sitt effektmått, vilket bör lyfta kursen.",
        "Fas 3-studien nådde sitt effektmått och aktien ser undervärderad ut.",
        "Fas 3-studien nådde sitt effektmått – ett köpläge.",
        "Fas 3-studien nådde sitt effektmått; vi rekommenderar bolaget.",
        "Fas 3-studien nådde sitt effektmått, höjt kursmål väntas.",
    ],
)
def test_kurs_och_rad_ar_forbjudet_i_motiveringen(week, instruments, sector_ids, rationale):
    sentiment(week, "halso-norrsken-studie", rationale=rationale)
    res = run(week, instruments, sector_ids)
    assert any("får inte handla om kurs eller råd" in e for e in res.errors)


def test_bolag_som_kopar_ar_inte_forbjudet(week, instruments, sector_ids):
    sentiment(week, "fast-tallmo-forsaljning", rationale="Fjällhem köper ett bestånd av kontorsfastigheter.")
    assert run(week, instruments, sector_ids).errors == []


def test_rutinmeddelande_maste_vara_neutralt(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    raw = make_raw(week, tmp_path)
    idx = json.loads((raw / "index.json").read_text(encoding="utf-8"))
    for e in idx["items"]:
        if e["path"] == item(week, "halso-norrsken-studie")["source"]["raw_file"]:
            e["routine"] = True
    (raw / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("rutinmeddelanden ska ha sentiment 'neutral', inte 'positiv'" in e for e in res.errors)
    sentiment(week, "halso-norrsken-studie", label="neutral")
    assert run(week, instruments, sector_ids, raw_dir=tmp_path).errors == []


def test_saknat_sentiment_ger_varning_for_riktig_vecka(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    del item(week, "halso-norrsken-studie")["sentiment"]
    make_raw(week, tmp_path)
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert res.errors == []
    assert any("halso-norrsken-studie: sentiment saknas" in w for w in res.warnings)


@pytest.mark.parametrize("bad", [{"label": "glad"}, {"basis": []}, {"rationale": ""}])
def test_sentiment_schema(week, instruments, sector_ids, bad):
    sentiment(week, "halso-norrsken-studie", **bad)
    assert any(e.startswith("schema:") for e in run(week, instruments, sector_ids).errors)


# --- Uppköpsbud (bid_target) -------------------------------------------------


def takeover_item(week):
    it = item(week, "fast-tallmo-forsaljning")
    it["headline"] = "Fjällhem lägger offentligt uppköpsbud på Albion Biotech"
    it["summary"] = "Albion Biotech ansöker om avnotering."
    it["companies"].append({"name": "Albion Biotech", "match": "matchad", "ticker": "ALBN", "yahoo_ticker": "ALBN.MOCK",
                            "exchange": "Exempelbörsen", "impact": "direkt", "rationale": "Målbolaget.",
                            "bid_target": True})
    return it


def test_bid_target_pa_malbolaget_i_en_budnyhet_godkanns(week, instruments, sector_ids):
    takeover_item(week)
    assert run(week, instruments, sector_ids).errors == []
    assert validate.bid_targets(week) == {"ALBN"}


def test_bid_target_utan_budord_i_nyheten_underkanns(week, instruments, sector_ids):
    it = takeover_item(week)
    it["headline"], it["summary"] = "Fjällhem och Albion Biotech samarbetar", "Ett samarbetsavtal."
    res = run(week, instruments, sector_ids)
    assert any("nämner inget uppköpsbud" in e for e in res.errors)


@pytest.mark.parametrize("change", [{"impact": "indirekt"}, {"match": "ej i listan", "ticker": None, "yahoo_ticker": None, "exchange": None}])
def test_bid_target_maste_vara_matchat_och_direkt(week, instruments, sector_ids, change):
    takeover_item(week)["companies"][-1].update(change)
    res = run(week, instruments, sector_ids)
    assert any("inte ett matchat, direkt berört bolag" in e for e in res.errors)


# --- Rådata och mock-spärr -------------------------------------------------


POLICY_RATE = {"value": 1.75, "date": "2026-10-02", "previous_value": 2.0, "changed_on": "2025-10-01"}


def make_raw(week, tmp_path, policy_rate=POLICY_RATE, extra_index=()):
    """Skapar rådata (textfiler + index.json) som stämmer med veckofilen."""
    raw = tmp_path / week["week"]
    entries = []
    for s in week["sectors"]:
        for it in s["items"]:
            f = raw / it["source"]["raw_file"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("Inledning.\n" + "\n".join(e["quote"] for e in it["evidence"]), encoding="utf-8")
            entries.append({"path": it["source"]["raw_file"], "source": "mfn", "url": it["source"]["url"]})
    kf = week.get("key_figures", {}).get("policy_rate")
    if kf:
        entries.append({"path": kf["raw_file"], "source": "riksbanken", "url": kf["source_url"],
                        "data": {"policy_rate": dict(policy_rate)}})
    entries += list(extra_index)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "index.json").write_text(json.dumps({"items": entries}), encoding="utf-8")
    return raw


def real_week(week):
    w = copy.deepcopy(week)
    w["mock"] = False
    return w


def test_evidence_kontrolleras_mot_radata(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    raw = make_raw(week, tmp_path)
    assert run(week, instruments, sector_ids, raw_dir=tmp_path).errors == []

    (raw / "statistikbyran" / "inflation.txt").write_text("Inflationstakten var 2,3 procent i september.", encoding="utf-8")
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("citatet finns inte i källtexten" in e for e in res.errors)


def test_mockvecka_kontrolleras_inte_mot_riktig_radata_for_samma_vecka(week, instruments, sector_ids, tmp_path):
    (tmp_path / "2026-W40" / "mfn").mkdir(parents=True)  # riktig rådata för vecka 40 finns
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert res.errors == []


def test_source_url_maste_stamma_med_index(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    make_raw(week, tmp_path)
    item(week, "fast-fjallhem-refinansiering")["source"]["url"] = "https://example.com/fjallhem/fel-adress"
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any(
        "fast-fjallhem-refinansiering: source.url 'https://example.com/fjallhem/fel-adress' skiljer sig från indexets"
        in e for e in res.errors
    )


def test_raw_file_som_saknas_i_index_underkanns(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    raw = make_raw(week, tmp_path)
    idx = json.loads((raw / "index.json").read_text(encoding="utf-8"))
    idx["items"] = [e for e in idx["items"] if e["path"] != "press/tallmo-forsaljning.txt"]
    (raw / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("'press/tallmo-forsaljning.txt' finns inte i rådatans index" in e for e in res.errors)


def test_saknat_index_underkanns(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    raw = make_raw(week, tmp_path)
    (raw / "index.json").unlink()
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("rådatans index saknas" in e for e in res.errors)


# --- Nyckeltal: styrräntan --------------------------------------------------


def test_riksbank_url_ar_samma_i_fetch_och_validate():
    import fetch

    assert validate.RIKSBANK_PAGE_URL == fetch.RIKSBANK_PAGE_URL


def test_nyckeltal_kontrolleras_mot_radatans_index(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    make_raw(week, tmp_path, policy_rate={**POLICY_RATE, "previous_value": 2.25})
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("key_figures.policy_rate.previous_value = 2.0, rådatan säger 2.25" in e for e in res.errors)


def test_nyckeltal_maste_vara_senaste_observationen_i_veckan(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    week["key_figures"]["policy_rate"].update(date="2026-10-01", raw_file="riksbanken/styrranta-2026-10-01.txt")
    later = {"path": "riksbanken/styrranta-2026-10-02.txt", "source": "riksbanken", "url": validate.RIKSBANK_PAGE_URL,
             "data": {"policy_rate": POLICY_RATE}}
    make_raw(week, tmp_path, policy_rate={**POLICY_RATE, "date": "2026-10-01"}, extra_index=[later])
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert any("senaste observationen i veckan är 2026-10-02" in e for e in res.errors)


def test_nyckeltal_med_fel_kallank_och_datum_utanfor_veckan(week, instruments, sector_ids):
    kf = week["key_figures"]["policy_rate"]
    kf["source_url"] = "https://api.riksbank.se/swea/v1/Observations/Latest/SECBREPOEFF"
    kf["date"] = "2026-10-05"
    res = run(week, instruments, sector_ids)
    assert any("source_url ska vara Riksbankens sida" in e for e in res.errors)
    assert any("ligger utanför" in e for e in res.errors)


def test_makrosammanfattningen_far_hanvisa_till_nyckeltalet(week, instruments, sector_ids):
    week["sectors"][0]["summary"] = "Styrräntan är 1,75 procent sedan 2025-10-01, efter 2 procent tidigare."
    assert run(week, instruments, sector_ids).errors == []
    # Men inte med ett värde som inte stämmer.
    week["sectors"][0]["summary"] = "Styrräntan är 1,5 procent."
    assert any("['1,5 procent']" in e for e in run(week, instruments, sector_ids).errors)


@pytest.mark.parametrize(
    "summary",
    [
        "Styrräntan sänktes till 1,75 procent.",
        "Riksbanken har höjt styrräntan till 1,75 procent.",
        "Inflationen var 2,1 procent. Styrräntan sänks till 1,75 procent.",
    ],
)
def test_sankt_eller_hojd_underkanns_nar_andringen_skett_fore_veckan(week, instruments, sector_ids, summary):
    # Mockens changed_on är 2025-10-01, alltså före vecka 40.
    week["sectors"][0]["summary"] = summary
    res = run(week, instruments, sector_ids)
    assert any("säger att styrräntan ändrats, men den är oförändrad under veckan (senast ändrad 2025-10-01)" in e
               for e in res.errors)


def test_sankt_godkanns_nar_andringen_skett_under_veckan(week, instruments, sector_ids):
    week["key_figures"]["policy_rate"].update(value=1.5, previous_value=1.75, changed_on="2026-09-30")
    week["sectors"][0]["summary"] = "Styrräntan sänktes till 1,5 procent från 1,75 procent."
    assert run(week, instruments, sector_ids).errors == []


@pytest.mark.parametrize(
    "summary",
    [
        "Styrräntan ligger kvar på 1,75 procent, oförändrad sedan 1 oktober 2025.",
        "Hushållen tror att styrräntan höjs under nästa år.",  # förväntan, inte påstående
        "Riksbankens signaler om en höjning av styrräntan blir tydligare.",
        "Bankerna höjde sina bolåneräntor.",  # inte styrräntan
    ],
)
def test_oforandrad_och_forvantningar_godkanns(week, instruments, sector_ids, summary):
    week["sectors"][0]["summary"] = summary
    assert run(week, instruments, sector_ids).errors == []


def test_ingen_andring_i_hamtad_period_raknas_som_oforandrad(week, instruments, sector_ids):
    week["key_figures"]["policy_rate"].update(previous_value=None, changed_on=None)
    week["sectors"][0]["summary"] = "Styrräntan sänktes till 1,75 procent."
    res = run(week, instruments, sector_ids)
    assert any("(ingen ändring i hämtad period)" in e for e in res.errors)


def test_annan_sektor_far_inte_luta_sig_mot_nyckeltalet(week, instruments, sector_ids):
    week["sectors"][1]["summary"] = "Styrräntan på 1,75 procent påverkar fastigheter."
    res = run(week, instruments, sector_ids)
    assert any(e.startswith("fastigheter: sektorsammanfattningen har siffror") for e in res.errors)


def test_styrrantan_som_nyhet_underkanns(week, instruments, sector_ids):
    item(week, "makro-statistikbyran-inflation")["source"]["raw_file"] = "riksbanken/styrranta-2026-10-02.txt"
    res = run(week, instruments, sector_ids)
    assert any("styrräntan är ett nyckeltal" in e for e in res.errors)


def test_saknat_nyckeltal_ger_varning_for_riktig_vecka(week, instruments, sector_ids, tmp_path):
    week = real_week(week)
    del week["key_figures"]
    week["sectors"][0]["summary"] = "Inflationen landade på 2,1 procent."
    make_raw(week, tmp_path)
    res = run(week, instruments, sector_ids, raw_dir=tmp_path)
    assert res.errors == []
    assert any("policy_rate saknas" in w for w in res.warnings)


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


def test_nyckeltal_per_sektor():
    from validate import KPI_KINDS, load_sector_ids, load_sector_kpis

    kpis = load_sector_kpis()
    assert set(kpis) == load_sector_ids()
    assert kpis["makro"] == []
    for sector, items in kpis.items():
        if sector != "makro":
            assert len(items) == 2, sector
            assert all(k["kind"] in KPI_KINDS for k in items)
    assert kpis["fastigheter"][0]["id"] == "forvaltningsresultat-per-aktie"


def test_ogiltigt_nyckeltal_underkanns(tmp_path):
    import json as _json

    import pytest

    from validate import load_sector_kpis

    p = tmp_path / "sectors.json"
    p.write_text(_json.dumps([{"id": "x", "name": "X", "kpis": [{"id": "a", "name": "A", "kind": "okänd"}]}]),
                 encoding="utf-8")
    with pytest.raises(ValueError):
        load_sector_kpis(p)
