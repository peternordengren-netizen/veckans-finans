"""Tester för scripts/fi_fetch.py med påhittade FI-filer (inga nätanrop)."""

import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fi_fetch  # noqa: E402

NOW = datetime(2026, 10, 12, 6, 30, tzinfo=fi_fetch.STOCKHOLM)  # måndag morgon -> as_of söndag 11/10 (v41)


def ods(rows):
    """Minimal ODS med ett blad; en upprepad tom cell för att likna FI:s filer."""
    def cell(v):
        return f'<table:table-cell office:value-type="string"><text:p>{escape(v)}</text:p></table:table-cell>'
    body = "".join(
        "<table:table-row>" + "".join(cell(v) for v in r)
        + '<table:table-cell table:number-columns-repeated="3"/></table:table-row>'
        for r in rows
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f'<office:body><office:spreadsheet><table:table table:name="Blad1">{body}</table:table>'
        "</office:spreadsheet></office:body></office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", xml)
    return buf.getvalue()


TITLE = [["Aggregerade positioner"], ["Aggregerade korta nettopositioner i aktier (blankning)", "", "", "fi.se/blankning"]]
AGGREGATE = ods(TITLE + [
    ["Namn på emittent ", "LEI", "Position i procent", "Positionsdatum senaste position "],
    [" Fjällhem AB", "LEI0000000000000001", "4,72", "2026-10-09"],
    ["Kustbanken AB (publ)", "LEI0000000000000002", "0,8", "2026-10-08"],
])
CURRENT = ods(TITLE + [
    ["Innehavare av positionen ", "Namn på emittent ", "ISIN", "Position i procent", "Datum för positionen ", "Kommentar"],
    ["Exempelfonden LP", "Fjällhem AB", "SE0000000001", "1,05", "2026-10-09"],
    ["Testkapital LLP", "Fjällhem AB", "SE0000000001", "0,5", "2026-10-08", "Ny position"],
])
HISTORY = ods(TITLE + [
    ["Innehavare av positionen", "Namn på emittent", "ISIN", "Position i procent", "Datum för positionen", "Kommentar"],
    ["Exempelfonden LP", "Fjällhem AB", "SE0000000001", "<0,5", "2026-10-08"],
    ["Exempelfonden LP", "Fjällhem AB", "SE0000000001", "0,93", "2026-09-30"],
    ["Gamla fonden", "Fjällhem AB", "SE0000000001", "0,6", "2019-03-01"],
])

INSYN_HEADER = ("Publiceringsdatum;Emittent;LEI-kod;Anmälningsskyldig;Person i ledande ställning;Befattning;Närstående;"
                "Korrigering;Beskrivning av korrigering;Är förstagångsrapportering;Är kopplad till aktieprogram;Karaktär;"
                "Instrumenttyp;Instrumentnamn;ISIN;Transaktionsdatum;Volym;Volymsenhet;Pris;Valuta;Handelsplats;Status;")


def insyn(*rows):
    return ("\r\n".join([INSYN_HEADER, *rows]) + "\r\n").encode("utf-16")


ROW_MON = ("2026-10-05 09:12:00;Fjällhem AB;LEI1;Anna A;Anna A;VD;;;;Ja;;Förvärv;Aktie;Fjällhem B;SE0000000001;"
           "2026-10-02 00:00:00;10000,0;Antal;45,5;SEK;NASDAQ STOCKHOLM AB;Aktuell;")
ROW_WED = ("2026-10-07 14:00:00;Kustbanken AB;LEI2;Bo B;Bo B;CFO;;;;Nej;Ja;Tilldelning;Aktie;Kustbanken A;SE0000000002;"
           "2026-10-06 00:00:00;500,0;Antal;0,0;SEK;Utanför handelsplats;Aktuell;")


def fake_get(table):
    def get(url):
        for key, value in table.items():
            if url.startswith(key):
                if isinstance(value, Exception):
                    raise value
                return value
        raise KeyError(url)
    return get


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- Tolkning ----------------------------------------------------------------


def test_aggregat_tolkas_med_decimalkomma_och_trimmade_namn():
    rows = fi_fetch.parse_aggregate(AGGREGATE)
    assert rows == [
        {"issuer": "Fjällhem AB", "lei": "LEI0000000000000001", "pct": 4.72, "date": "2026-10-09"},
        {"issuer": "Kustbanken AB (publ)", "lei": "LEI0000000000000002", "pct": 0.8, "date": "2026-10-08"},
    ]


def test_aktuella_positioner_och_kommentar():
    rows = fi_fetch.parse_positions(CURRENT)
    assert rows[0] == {"holder": "Exempelfonden LP", "issuer": "Fjällhem AB", "isin": "SE0000000001",
                       "pct": 1.05, "date": "2026-10-09", "comment": ""}
    assert rows[1]["comment"] == "Ny position"


def test_historik_under_rapportgransen_blir_none():
    rows = fi_fetch.parse_positions(HISTORY)
    assert rows[0]["pct"] is None and rows[1]["pct"] == 0.93


def test_okand_ods_ger_tydligt_fel():
    with pytest.raises(ValueError, match="rubrikraden"):
        fi_fetch.parse_positions(ods([["Något annat"]]))


def test_insyn_utf16_semikolon_och_tal():
    [r] = fi_fetch.parse_insyn(insyn(ROW_MON))
    assert (r["published_date"], r["issuer"], r["person"], r["nature"], r["isin"]) == (
        "2026-10-05", "Fjällhem AB", "Anna A", "Förvärv", "SE0000000001")
    assert (r["volume"], r["price"], r["currency"], r["share_program"], r["transaction_date"]) == (
        10000.0, 45.5, "SEK", False, "2026-10-02")


def test_insyn_med_annat_format_ger_fel():
    with pytest.raises(ValueError, match="saknar fält"):
        fi_fetch.parse_insyn("Datum;Namn\r\n2026-10-05;X\r\n".encode("utf-16"))


def test_insyn_url_har_formularets_falt():
    url = fi_fetch.insyn_url(fi_fetch.date(2026, 10, 5), fi_fetch.date(2026, 10, 11))
    assert url.startswith("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search?SearchFunctionType=Insyn&")
    assert "Publiceringsdatum.From=2026-10-05&Publiceringsdatum.To=2026-10-11&button=export&Page=1" in url


# --- Körning -----------------------------------------------------------------


def manual(tmp_path, **files):
    """data/fi_manual/ med nedladdade filer, namngivna som FI:s knappar gör."""
    d = tmp_path / "fi_manual"
    d.mkdir(exist_ok=True)
    names = {"aktuella": "AktuellaPositioner.ods", "aggregerade": "Aggregerade positioner.ods",
             "historiska": "HistoriskaPositioner.ods"}
    for kind, data in files.items():
        (d / names[kind]).write_bytes(data)
    return d


def test_daglig_korning_last_manuella_filer_med_filens_datum(tmp_path):
    """Blankningen läses ur nedladdade filer (FI avvisar skriptade nedladdningar sedan 29/10 2026), med
    filens eget datum = senaste positionsdatumet; insynen hämtas och dateras till gårdagen."""
    d = manual(tmp_path, aktuella=CURRENT, aggregerade=AGGREGATE)
    raw = tmp_path / "raw"
    get = fake_get({fi_fetch.INSYN_URL: insyn()})
    assert fi_fetch.main(["--raw-dir", str(raw), "--manual-dir", str(d)], get=get, now=NOW) == 0
    snap = read(raw / "2026-W41" / "fi" / "blankning-2026-10-09.json")  # senaste positionsdatum 9/10
    assert snap["as_of"] == "2026-10-09" and len(snap["aggregate"]) == 2 and len(snap["current"]) == 2
    assert "AktuellaPositioner.ods" in snap["sources"]["current"]
    assert fi_fetch.last_written == [raw / "2026-W41" / "fi" / "blankning-2026-10-09.json"]
    ins = read(raw / "2026-W41" / "fi" / "insyn-2026-10-11.json")
    assert ins["rows"] == [] and "Publiceringsdatum.From=2026-10-11" in ins["source"]


def test_senaste_nedladdningen_lases(tmp_path):
    import os

    d = manual(tmp_path, aktuella=CURRENT, aggregerade=AGGREGATE)
    newer = ods(TITLE + [
        ["Innehavare av positionen (Position holder)", "Namn på emittent (Name of the issuer)", "ISIN",
         "Position i procent(Position in per cent)", "Datum för positionen (Position date)", "Kommentar(Comment)"],
        ["Exempelfonden LP", "Fjällhem AB", "SE0000000001", "1,2", "2026-10-12"],
    ])
    (d / "AktuellaPositioner (1).ods").write_bytes(newer)
    os.utime(d / "AktuellaPositioner.ods", (1, 1))  # den gamla filen är äldst
    fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(d)], get=fake_get({fi_fetch.INSYN_URL: insyn()}),
                  now=NOW)
    snap = read(tmp_path / "raw" / "2026-W42" / "fi" / "blankning-2026-10-12.json")
    assert [r["pct"] for r in snap["current"]] == [1.2]  # tvåspråkiga rubriker (FI 2026-11) läses


def test_utan_nedladdade_filer_hoppas_blankningen_over_utan_fel(tmp_path):
    get = fake_get({fi_fetch.INSYN_URL: insyn()})
    assert fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(tmp_path / "saknas")],
                         get=get, now=NOW) == 0
    assert not list((tmp_path / "raw").glob("*/fi/blankning-*.json"))


def test_bara_en_av_filerna_ar_fel(tmp_path):
    d = manual(tmp_path, aktuella=CURRENT)
    code = fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(d)],
                         get=fake_get({fi_fetch.INSYN_URL: insyn()}), now=NOW)
    assert code == 2 and "Aggregerade positioner" in fi_fetch.last_errors["blankning"]


def test_utan_blankning_rors_inga_filer(tmp_path):
    """Actions: --utan-blankning läser inga nedladdade filer, bara insynen hämtas."""
    d = manual(tmp_path, aktuella=CURRENT, aggregerade=AGGREGATE)
    get = fake_get({fi_fetch.INSYN_URL: insyn()})
    assert fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(d), "--utan-blankning"],
                         get=get, now=NOW) == 0
    assert not list((tmp_path / "raw").glob("*/fi/blankning-*.json"))
    assert (tmp_path / "raw" / "2026-W41" / "fi" / "insyn-2026-10-11.json").exists()


def test_efterhamtning_av_insyn_delas_per_publiceringsdag(tmp_path):
    get = fake_get({fi_fetch.INSYN_URL: insyn(ROW_MON, ROW_WED)})
    code = fi_fetch.main(["--raw-dir", str(tmp_path), "--insyn-from", "2026-10-05", "--insyn-to", "2026-10-11"],
                         get=get, now=NOW)
    assert code == 0
    files = sorted(p.name for p in (tmp_path / "2026-W41" / "fi").glob("insyn-*.json"))
    assert files == [f"insyn-2026-10-{d:02d}.json" for d in range(5, 12)]  # även tomma dagar
    assert [r["issuer"] for r in read(tmp_path / "2026-W41" / "fi" / "insyn-2026-10-07.json")["rows"]] == ["Kustbanken AB"]
    assert read(tmp_path / "2026-W41" / "fi" / "insyn-2026-10-06.json")["rows"] == []


def test_historik_med_filens_datum_och_filtrerad(tmp_path):
    d = manual(tmp_path, historiska=HISTORY)
    fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(d), "--history-since", "2025-01-01"],
                  get=fake_get({fi_fetch.INSYN_URL: insyn()}), now=NOW)
    data = read(tmp_path / "raw" / "fi-historik" / "blankning-historik-2026-10-08.json")
    assert data["as_of"] == "2026-10-08"
    assert [r["date"] for r in data["positions"]] == ["2026-10-08", "2026-09-30"]  # 2019 utesluten


def test_en_del_fallerar_ovriga_sparas_och_fel_i_actions(tmp_path, monkeypatch, capsys):
    """Ett fel i en del räknas (kod 2, ::error:: i Actions), och den andra delen sparas ändå."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    d = manual(tmp_path, aktuella=CURRENT, aggregerade=AGGREGATE)
    get = fake_get({fi_fetch.INSYN_URL: TimeoutError("timed out")})
    assert fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), "--manual-dir", str(d)], get=get, now=NOW) == 2
    assert (tmp_path / "raw" / "2026-W41" / "fi" / "blankning-2026-10-09.json").exists()
    assert "::error title=FI-fel: insyn::TimeoutError: timed out" in capsys.readouterr().out
    assert fi_fetch.last_errors == {"insyn": "TimeoutError: timed out"}


def test_allt_fallerar_ger_exitkod_1(tmp_path):
    get = fake_get({"https://": ConnectionError("nere")})
    assert fi_fetch.main(["--raw-dir", str(tmp_path), "--utan-blankning"], get=get, now=NOW) == 1


def test_efterhamtning_kraver_bada_datum(tmp_path):
    with pytest.raises(SystemExit):
        fi_fetch.main(["--raw-dir", str(tmp_path), "--insyn-from", "2026-10-05"], get=fake_get({}), now=NOW)
