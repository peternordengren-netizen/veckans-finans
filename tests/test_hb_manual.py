"""Tester för Handelsbanken-rapporterna som laddas ner för hand (scripts/hb_manual.py) och poddens avsnitt
(scripts/podcast.py). Påhittade rapporter, bolag och avsnitt."""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hb_manual  # noqa: E402
import podcast  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken Fastigheter", (), "ALFA", "ALFA.ST", "Stockholm"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm"),
}
SOURCE = ("Fastighetsrapporten hösten 2099. Marknaden för kommersiella fastigheter fortsätter att återhämta sig "
          "i lugn takt medan räntorna ligger kvar på en högre nivå än före pandemin. Vi bedömer att direktavkastningen "
          "har nått botten för logistikfastigheter men inte för kontor i ytterområden. Alfaverken Fastigheter har en "
          "stark balansräkning enligt vår genomgång.")


def make_pptx(path: Path, slides: list[list[str]]) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for lines in slides:
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = lines[0]
        box = s.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(3))
        box.text_frame.text = lines[1]
        for extra in lines[2:]:
            box.text_frame.add_paragraph().text = extra
    prs.save(str(path))


def make_pdf(path: Path, text: str) -> None:
    """En minimal PDF med en textrad (Helvetica), skriven för hand."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_text_ur_powerpoint_och_pdf_och_rapporttyp(tmp_path):
    pptx = tmp_path / "991111 Final.pptx"
    make_pptx(pptx, [["Morgonrapporten", "Rubrik om räntor", "Kalendern i dag"], ["Marknaden", "Börserna steg"]])
    text = hb_manual.extract_text(pptx)
    assert "--- Bild 1 ---" in text and "Kalendern i dag" in text and "Börserna steg" in text
    assert hb_manual.guess_kind(pptx, text) == ("morgonrapport", "2099-11-11")
    assert hb_manual.file_created(pptx) is not None  # PowerPointens core_properties.created
    pdf = tmp_path / "rapport.pdf"
    make_pdf(pdf, "Fastighetsrapporten hosten 2099")
    text = hb_manual.extract_text(pdf)
    assert "Fastighetsrapporten hosten 2099" in text
    assert hb_manual.guess_kind(pdf, text) == ("fastighetsrapport", None)
    assert hb_manual.guess_kind(tmp_path / "annat.pdf", "Konjunkturprognos") == ("annan", None)
    with pytest.raises(ValueError):
        hb_manual.extract_text(tmp_path / "x.docx")


def test_nya_filer_en_gang_per_innehall(tmp_path):
    manual, raw, work = tmp_path / "manual", tmp_path / "raw", tmp_path / "work"
    manual.mkdir()
    make_pdf(manual / "a.pdf", "Fastighetsrapporten hosten 2099")
    (manual / "kopia.pdf").write_bytes((manual / "a.pdf").read_bytes())  # samma innehåll, annat namn
    (manual / "anteckning.txt").write_text("hoppas över")
    found = hb_manual.new_files(manual, raw)
    assert len(found) == 1
    fid = found[0][0]
    assert hb_manual.cmd_new(manual, raw, work) == [fid]
    meta = json.loads((work / f"{fid}.meta.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "fastighetsrapport" and meta["source_url"] == hb_manual.SOURCE_URLS["fastighetsrapport"]
    assert meta["label"] == "manuellt tillagd" and (work / f"{fid}.txt").exists()
    assert "file_created" in meta  # PDF:en utan metadata: null
    # Redan sammanfattad (finns i rådatarepot): ingen ny körning.
    (raw / "hb").mkdir(parents=True)
    (raw / "hb" / f"{fid}.json").write_text("{}")
    assert hb_manual.new_files(manual, raw) == []
    # Högst MAX_ATTEMPTS körningar per fil.
    (raw / "hb" / f"{fid}.json").unlink()
    for _ in range(hb_manual.MAX_ATTEMPTS):
        hb_manual.add_attempt(fid, work)
    assert hb_manual.cmd_new(manual, raw, work) == []


def good_summary(meta):
    return {
        "id": meta["id"], "file_name": meta["file_name"], "kind": "fastighetsrapport",
        "title": "Fastighetsrapporten hösten 2099", "report_date": "2099-10-15",
        "headline": "Återhämtningen i kommersiella fastigheter fortsätter långsamt.",
        "points": ["Räntenivån väntas ligga kvar högre än före pandemin.", "Logistik ser starkare ut än kontor."],
        "rates": "Banken räknar med att räntorna stannar på en högre nivå en tid.",
        "valuations": "Avkastningskraven har planat ut för logistik men kan stiga för kontor utanför centrum.",
        "segments": [{"name": "logistik", "view": "botten nådd för avkastningskraven"},
                     {"name": "kontor", "view": "svagare i ytterområden"}],
        "companies": [{"ticker": "ALFA", "name": "Alfaverken Fastigheter", "note": "lyfts fram för sin starka balansräkning"}],
        "label": "manuellt tillagd", "source_url": hb_manual.SOURCE_URLS["fastighetsrapport"],
    }


META = {"id": "0123456789abcdef", "file_name": "fastighet.pdf", "kind": "fastighetsrapport", "file_date": None}


def test_kontrollen_godkanner_egna_ord():
    assert hb_manual.check(good_summary(META), META, SOURCE, INSTRUMENTS, date(2099, 11, 11)) == []


@pytest.mark.parametrize("change, fel", [
    ({"headline": "Marknaden för kommersiella fastigheter fortsätter att återhämta sig i lugn takt."}, "citat"),
    ({"headline": "Banken skriver \"lugn takt\" om marknaden."}, "citattecken"),
    ({"companies": [{"ticker": "GAMMA", "name": "Gammagruppen", "note": "x"}]}, "instruments.csv"),
    ({"companies": [{"ticker": "ALFA", "name": "Alfaverken", "note": "x"}]}, "instruments.csv"),
    ({"rates": None}, "rates"),
    ({"segments": []}, "delsegment"),
    ({"label": "Handelsbanken"}, "märkningen"),
    ({"source_url": "https://exempel.test/rapport.pdf"}, "källsidan"),
    ({"kind": "morgonrapport"}, "rapporttypen"),
    ({"report_date": "2100-01-01"}, "framtiden"),
    ({"report_date": "15 okt"}, "inget datum"),
    ({"points": ["bara en"]}, "punkterna"),
    ({"points": ["Banken anser att man bör köpa logistikbolag.", "Kontor svagare."]}, "köpa"),
])
def test_kontrollen_underkanner(change, fel):
    s = {**good_summary(META), **change}
    errors = hb_manual.check(s, META, SOURCE, INSTRUMENTS, date(2099, 11, 11))
    assert any(fel in e for e in errors), errors


def test_morgonrapportens_datum_ur_filnamnet():
    meta = {**META, "kind": "morgonrapport", "file_name": "991111 Final.pptx", "file_date": "2099-11-11"}
    s = {**good_summary(meta), "kind": "morgonrapport", "report_date": "2099-11-10", "rates": None,
         "valuations": None, "segments": [], "source_url": hb_manual.SOURCE_URLS["morgonrapport"]}
    errors = hb_manual.check(s, meta, SOURCE, INSTRUMENTS, date(2099, 11, 11))
    assert errors == ["rapportdatumet 2099-11-10 ska vara 2099-11-11 (filnamnet)"]
    s["report_date"] = "2099-11-11"
    assert hb_manual.check(s, meta, SOURCE, INSTRUMENTS, date(2099, 11, 11)) == []


def test_klar_till_radatarepot_och_arbetsfilerna_bort(tmp_path, monkeypatch):
    hb, work, raw = tmp_path / "hb", tmp_path / "hb" / "work", tmp_path / "raw"
    work.mkdir(parents=True)
    fid = META["id"]
    (work / f"{fid}.meta.json").write_text(json.dumps(META), encoding="utf-8")
    (work / f"{fid}.txt").write_text(SOURCE, encoding="utf-8")
    (hb / f"{fid}.json").write_text(json.dumps(good_summary(META), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(hb_manual, "load_instruments", lambda: INSTRUMENTS)
    committed = []

    def commit(raw_dir, files, message):
        for src, rel in files:
            committed.append((rel, json.loads(src.read_text(encoding="utf-8")), message))
        return True, "abc1234"

    now = datetime(2099, 11, 11, 9, 20, tzinfo=TZ)
    ok, msg = hb_manual.cmd_done(fid, now, raw, hb_dir=hb, work_dir=work, commit=commit)
    assert ok, msg
    rel, saved, message = committed[0]
    assert rel == f"hb/{fid}.json" and saved["summarized_at"] == now.isoformat(timespec="seconds")
    assert "manuellt tillagd" in message
    assert not any(work.iterdir()) and not (hb / f"{fid}.json").exists()  # texten och utfilen borttagna
    # Underkänd: inget till rådatarepot, arbetsfilerna står kvar.
    (work / f"{fid}.meta.json").write_text(json.dumps(META), encoding="utf-8")
    (work / f"{fid}.txt").write_text(SOURCE, encoding="utf-8")
    (hb / f"{fid}.json").write_text(json.dumps({**good_summary(META), "label": "x"}), encoding="utf-8")
    ok, msg = hb_manual.cmd_done(fid, now, raw, hb_dir=hb, work_dir=work, commit=commit)
    assert not ok and "märkningen" in msg and (work / f"{fid}.txt").exists() and len(committed) == 1


FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Exempelpodden</title>
<item><title>991111 - Ett lugnt marknadsl\xc3\xa4ge</title><link>https://exempel.test/avsnitt/991111</link>
<pubDate>Wed, 11 Nov 2099 05:15:14 +0000</pubDate></item>
<item><title>991110 - Ig\xc3\xa5r</title><link>https://exempel.test/avsnitt/991110</link>
<pubDate>Tue, 10 Nov 2099 06:11:44 +0000</pubDate></item>
<item><title>utan datum</title><link>https://exempel.test/x</link></item>
</channel></rss>"""


def test_poddens_avsnitt_i_dag():
    eps = podcast.episodes(FEED)
    assert [e["title"] for e in eps] == ["991111 - Ett lugnt marknadsläge", "991110 - Igår"]
    assert eps[0]["published"] == "2099-11-11T06:15+01:00"  # Stockholmstid
    ep = podcast.today_episode(FEED, date(2099, 11, 11))
    assert ep == {"title": "991111 - Ett lugnt marknadsläge", "url": "https://exempel.test/avsnitt/991111",
                  "published": "2099-11-11T06:15+01:00"}
    assert podcast.today_episode(FEED, date(2099, 11, 12)) is None
    calls = []
    res = podcast.for_morning(date(2099, 11, 11), get=lambda url: calls.append(url) or FEED)
    assert calls == [podcast.FEED_URL] and res["episode"]["url"].endswith("991111") and res["error"] is None

    def fail(url):
        raise TimeoutError("timeout")

    res = podcast.for_morning(date(2099, 11, 11), get=fail)
    assert res["episode"] is None and "TimeoutError" in res["error"]
