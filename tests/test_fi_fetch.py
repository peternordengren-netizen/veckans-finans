"""Tester för scripts/fi_fetch.py med påhittade FI-filer (inga nätanrop)."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fi_fetch  # noqa: E402

NOW = datetime(2026, 10, 12, 6, 30, tzinfo=fi_fetch.STOCKHOLM)  # måndag morgon -> as_of söndag 11/10 (v41)


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


def test_bara_insyn_och_ingen_blankning(tmp_path):
    """Bara insynen hämtas, med eller utan --utan-blankning (Actions skickar den fortfarande). Blankning
    läses inte sedan 2026-11-11, inte heller ur data/fi_manual/."""
    get = fake_get({fi_fetch.INSYN_URL: insyn()})
    for extra in ([], ["--utan-blankning"]):
        assert fi_fetch.main(["--raw-dir", str(tmp_path / "raw"), *extra], get=get, now=NOW) == 0
    assert not list((tmp_path / "raw").glob("*/fi/blankning-*.json"))
    assert (tmp_path / "raw" / "2026-W41" / "fi" / "insyn-2026-10-11.json").exists()
    assert not hasattr(fi_fetch, "MANUAL_DIR") and not hasattr(fi_fetch, "import_blankning")


def test_efterhamtning_av_insyn_delas_per_publiceringsdag(tmp_path):
    get = fake_get({fi_fetch.INSYN_URL: insyn(ROW_MON, ROW_WED)})
    code = fi_fetch.main(["--raw-dir", str(tmp_path), "--insyn-from", "2026-10-05", "--insyn-to", "2026-10-11"],
                         get=get, now=NOW)
    assert code == 0
    files = sorted(p.name for p in (tmp_path / "2026-W41" / "fi").glob("insyn-*.json"))
    assert files == [f"insyn-2026-10-{d:02d}.json" for d in range(5, 12)]  # även tomma dagar
    assert [r["issuer"] for r in read(tmp_path / "2026-W41" / "fi" / "insyn-2026-10-07.json")["rows"]] == ["Kustbanken AB"]
    assert read(tmp_path / "2026-W41" / "fi" / "insyn-2026-10-06.json")["rows"] == []


def test_fel_i_insynen_i_actions(tmp_path, monkeypatch, capsys):
    """Ett fel ger ::error:: i Actions och exit 1 (insynen är enda delen)."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    get = fake_get({fi_fetch.INSYN_URL: TimeoutError("timed out")})
    assert fi_fetch.main(["--raw-dir", str(tmp_path / "raw")], get=get, now=NOW) == 1
    assert "::error title=FI-fel: insyn::TimeoutError: timed out" in capsys.readouterr().out
    assert fi_fetch.last_errors == {"insyn": "TimeoutError: timed out"}


def test_allt_fallerar_ger_exitkod_1(tmp_path):
    get = fake_get({"https://": ConnectionError("nere")})
    assert fi_fetch.main(["--raw-dir", str(tmp_path), "--utan-blankning"], get=get, now=NOW) == 1


def test_efterhamtning_kraver_bada_datum(tmp_path):
    with pytest.raises(SystemExit):
        fi_fetch.main(["--raw-dir", str(tmp_path), "--insyn-from", "2026-10-05"], get=fake_get({}), now=NOW)
