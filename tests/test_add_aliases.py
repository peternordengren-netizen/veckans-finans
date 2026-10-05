"""Tester för scripts/add_aliases.py (påhittade bolag)."""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import add_aliases  # noqa: E402
import validate  # noqa: E402


def row(namn, ticker, alias=""):
    return {"namn": namn, "alias": alias, "ticker": ticker, "yahoo_ticker": f"{ticker}.MOCK",
            "börs": "Exempelbörsen", "sektor": "", "mfn_slug": ""}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Kustbanken Bank Holding", ["Kustbanken Bank", "Kustbanken"]),
        ("Fjällhem Group AB (publ)", ["Fjällhem Group AB", "Fjällhem Group", "Fjällhem"]),
        ("Norrsken Pharma Oyj", ["Norrsken Pharma"]),
        ("Vågnät Hold.", ["Vågnät"]),
        ("Molnbyrån Software", []),  # ingen ändelse
        ("Group", []),  # tar aldrig bort sista ordet
        ("Bank Group", ["Bank"]),
    ],
)
def test_andelser_tas_bort_stegvis(name, expected):
    assert add_aliases.stripped_variants(name) == expected


def test_unika_alias_laggs_till():
    rows = [row("Kustbanken Bank Holding", "KUST"), row("Fjällhem Group", "FJLH")]
    res = add_aliases.add_aliases(rows)
    assert rows[0]["alias"] == "Kustbanken Bank|Kustbanken"
    assert rows[1]["alias"] == "Fjällhem"
    assert res.collisions == {}


def test_krock_mellan_tva_rader_laggs_inte_till():
    rows = [row("Norrsken Group", "NRSK"), row("Norrsken Holding", "NRSH")]
    res = add_aliases.add_aliases(rows)
    assert rows[0]["alias"] == "" and rows[1]["alias"] == ""
    assert res.collisions == {"Norrsken": ["NRSH", "NRSK"]}


def test_krock_med_annan_rads_namn_eller_alias_laggs_inte_till():
    rows = [row("Albion Group", "ALBN"), row("Albion Biotech", "ALBB", alias="Albion")]
    res = add_aliases.add_aliases(rows)
    assert rows[0]["alias"] == ""
    assert res.collisions == {"Albion": ["ALBB", "ALBN"]}


def test_for_korta_alias_laggs_inte_till():
    rows = [row("XY Group", "XY")]
    res = add_aliases.add_aliases(rows)
    assert rows[0]["alias"] == ""
    assert res.too_short == [("XY", "XY")]


def test_vanliga_ord_blir_inte_alias():
    rows = [row("Momentum Group", "MMGR"), row("Humble Group", "HUMB"), row("Kustbanken Group", "KUST")]
    res = add_aliases.add_aliases(rows)
    assert rows[0]["alias"] == "" and rows[1]["alias"] == ""
    assert rows[2]["alias"] == "Kustbanken"
    assert sorted(res.stopwords) == [("HUMB", "Humble"), ("MMGR", "Momentum")]


def test_projektets_lista_har_inga_alias_pa_stopplistan():
    with (ROOT / "data" / "instruments.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    offenders = [
        (r["ticker"], a) for r in rows for a in (r["alias"] or "").split("|")
        if a and a.casefold() in add_aliases.STOPWORDS
    ]
    assert offenders == []


def test_befintligt_alias_dupliceras_inte_och_skriptet_ar_idempotent(tmp_path):
    p = tmp_path / "instruments.csv"
    fields = ["namn", "alias", "ticker", "yahoo_ticker", "börs", "sektor", "mfn_slug"]
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows([row("Kustbanken Bank", "KUST", alias="Kustbanken"), row("Fjällhem Group", "FJLH")])
    add_aliases.main(["--csv", str(p)])
    first = p.read_text(encoding="utf-8")
    assert "Kustbanken Bank,Kustbanken," in first  # inget nytt för KUST
    assert "Fjällhem Group,Fjällhem," in first
    add_aliases.main(["--csv", str(p)])
    assert p.read_text(encoding="utf-8") == first


def test_validate_underkanner_namn_som_delas_av_tva_rader(tmp_path):
    p = tmp_path / "instruments.csv"
    p.write_text(
        "namn,alias,ticker,yahoo_ticker,börs\n"
        "Fjällhem,,FJLH B,FJLH-B.MOCK,Exempelbörsen\n"
        "Fjällhem Pref,Fjällhem,FJLH PREF,FJLH-PREF.MOCK,Exempelbörsen\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rad 3: namnet/aliaset 'Fjällhem' används redan av 'FJLH B'"):
        validate.load_instruments(p)


def test_projektets_lista_har_inga_krockar_kvar():
    with (ROOT / "data" / "instruments.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    res = add_aliases.add_aliases(rows)
    assert res.added == []  # allt unikt är redan tillagt
    validate.load_instruments()  # inga namn delas mellan rader
