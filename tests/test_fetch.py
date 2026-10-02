"""Tester för scripts/fetch.py. Alla flöden är påhittade (repot är publikt) och inga nätanrop görs."""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch  # noqa: E402

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=fetch.STOCKHOLM)  # fredag vecka 40


def mfn_item(news_id, title, pub, scope="SE", lang="sv", tags=(), slug="fjallhem-fastigheter", text="Fulltext."):
    tag_xml = "".join(f"<x:tag>{t}</x:tag>" for t in tags)
    return f"""<item>
      <title>{title}</title>
      <link>https://mfn.se/a/{slug}/{news_id}</link>
      <description></description>
      <pubDate>{pub}</pubDate>
      <x:newsId>{news_id}</x:newsId>
      <x:language>{lang}</x:language>{tag_xml}
      <x:scope>{scope}</x:scope>
      <x:content type="html">&lt;p&gt;{text}&lt;/p&gt;</x:content>
      <x:content type="text">{text}</x:content>
    </item>"""


def mfn_feed(*items):
    return f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">
    <channel xmlns:x="https://mfn.se/schemas/rss-ns-x/"><title>MFN</title>{''.join(items)}</channel></rss>""".encode()


def cision_item(guid, title, pub, slug="fjallhem-fastigheter-ab", text="Utdrag ur texten."):
    return f"""<item><title>{title}</title>
      <link>https://news.cision.com/se/{slug}/r/{guid}</link>
      <guid isPermaLink="false">{guid}</guid>
      <description><![CDATA[{text}]]></description>
      <pubDate>{pub}</pubDate></item>"""


def cision_feed(*items):
    # Cisions flöde inleds med BOM.
    return ("﻿" + f"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
    <title>Cision News</title>{''.join(items)}</channel></rss>""").encode("utf-8")


RIKSBANK = b'{"date":"2026-10-02","value":1.75}'


def fake_get(responses):
    """responses: url -> bytes eller Exception."""

    def get(url):
        r = responses[url]
        if isinstance(r, Exception):
            raise r
        return r

    return get


def read_index(raw, week):
    return json.loads((raw / week / "index.json").read_text(encoding="utf-8"))


# --- Vecka i svensk tid ----------------------------------------------------


def test_sondag_2330_svensk_tid_hor_till_samma_vecka():
    pub = fetch._parse_rfc822("Sun, 04 Oct 2026 23:30:00 +0200")
    assert fetch.iso_week(pub) == "2026-W40"


def test_mandag_0030_svensk_tid_hor_till_nasta_vecka_fast_det_ar_sondag_i_utc():
    pub = fetch._parse_rfc822("Sun, 04 Oct 2026 22:30:00 GMT")  # = måndag 00:30 i Stockholm
    assert pub.astimezone(UTC).isocalendar().week == 40  # naiv UTC-räkning hade gett fel vecka
    assert fetch.iso_week(pub) == "2026-W41"


def test_vintertid_sondag_2330():
    pub = fetch._parse_rfc822("Sun, 01 Nov 2026 22:30:00 GMT")  # 23:30 CET
    assert fetch.iso_week(pub) == "2026-W44"


def test_pressmeddelande_sondag_2330_hamnar_i_ratt_veckomapp(tmp_path):
    feed = mfn_feed(mfn_item("a1", "Fjällhem tecknar avtal", "Sun, 04 Oct 2026 21:30:00 +0000"))
    get = fake_get({fetch.MFN_URL: feed, fetch.CISION_URL: cision_feed(), fetch.RIKSBANK_URL: RIKSBANK})
    fetch.main(["--raw-dir", str(tmp_path)], get=get, now=NOW)
    [entry] = [e for e in read_index(tmp_path, "2026-W40")["items"] if e["source"] == "mfn"]
    assert entry["id"] == "a1"
    assert entry["published"] == "2026-10-04T23:30:00+02:00"
    assert (tmp_path / "2026-W40" / entry["path"]).exists()
    assert not (tmp_path / "2026-W41").exists()


def test_tid_utan_tidszon_avvisas():
    with pytest.raises(ValueError):
        fetch.iso_week(datetime(2026, 10, 4, 23, 30))


# --- Filter och taggar -----------------------------------------------------


def test_mfn_filter_och_rutintaggar():
    pub = "Fri, 02 Oct 2026 08:00:00 +0000"
    items = fetch.parse_mfn(mfn_feed(
        mfn_item("ok", "Fjällhem tecknar avtal", pub, tags=[":regulatory", "sub:ci:other"]),
        mfn_item("en", "Fjällhem signs agreement", pub, lang="en"),
        mfn_item("insider", "Fjällhem: insynshandel", pub, tags=["sub:ci", "sub:ci:insider"]),
        mfn_item("fi", "Finskt bolag", pub, scope="FI"),
        mfn_item("de", "Deutsch", pub, lang="de"),
        mfn_item("gm", "Kallelse till årsstämma", pub, tags=["sub:ci:gm:notice"]),
        mfn_item("aterkop", "Återköp av aktier", pub, tags=["sub:ca:shares:repurchase"]),
        mfn_item("brev", "Fjällhem: Nyhetsbrev oktober 2026", pub),  # otaggad – rubriken avgör
    ))
    by_id = {i.id: i for i in items}
    assert set(by_id) == {"ok", "en", "gm", "aterkop", "brev"}
    assert by_id["brev"].routine
    assert by_id["gm"].routine and "rutin" in by_id["gm"].tags
    assert by_id["aterkop"].routine
    assert not by_id["ok"].routine and "rutin" not in by_id["ok"].tags
    assert by_id["ok"].text_kind == "fulltext" and by_id["ok"].text == "Fulltext."
    assert by_id["ok"].company == "fjallhem-fastigheter"


def test_cision_markeras_som_utdrag_och_rutin_via_rubrik():
    pub = "Fri, 02 Oct 2026 08:00:00 GMT"
    items = fetch.parse_cision(cision_feed(
        cision_item("c1", "Tallmo vinner order", pub, slug="tallmo-ab"),
        cision_item("c2", "Kallelse till extra bolagsstämma i Tallmo AB", pub, slug="tallmo-ab"),
    ))
    assert [i.text_kind for i in items] == ["utdrag", "utdrag"]
    assert [i.routine for i in items] == [False, True]
    assert items[0].company == "tallmo-ab"


def test_riksbanken_blir_en_mening_med_svensk_decimal():
    [item] = fetch.parse_riksbank(RIKSBANK)
    assert item.text == "Riksbankens styrränta (serie SECBREPOEFF) var 1,75 procent den 2026-10-02."
    assert fetch.iso_week(item.published) == "2026-W40"


# --- Lagring, dubbletter och fel -------------------------------------------


def run(tmp_path, mfn=None, cision=None, riksbank=RIKSBANK, now=NOW):
    get = fake_get({
        fetch.MFN_URL: mfn if mfn is not None else mfn_feed(),
        fetch.CISION_URL: cision if cision is not None else cision_feed(),
        fetch.RIKSBANK_URL: riksbank,
    })
    return fetch.main(["--raw-dir", str(tmp_path)], get=get, now=now)


def test_textfil_och_index_skrivs(tmp_path):
    assert run(tmp_path, mfn=mfn_feed(mfn_item("a1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000"))) == 0
    idx = read_index(tmp_path, "2026-W40")
    mfn_entry = next(e for e in idx["items"] if e["source"] == "mfn")
    assert set(mfn_entry) >= {"id", "source", "url", "published", "title", "company", "tags", "routine", "text_kind", "path"}
    text = (tmp_path / "2026-W40" / mfn_entry["path"]).read_text(encoding="utf-8")
    assert "Publicerad: 2026-10-02 10:00 (Europe/Stockholm)" in text
    assert text.rstrip().endswith("Fulltext.")
    assert idx["runs"][0]["sources"]["mfn"] == {"status": "ok", "items": 1, "new": 1}


def test_samma_pressmeddelande_i_mfn_och_cision_blir_en_post(tmp_path):
    mfn = mfn_feed(mfn_item("m1", "Fjällhem Fastigheter AB: Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000"))
    cision = cision_feed(cision_item("c1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:03:00 GMT"))
    run(tmp_path, mfn=mfn, cision=cision)
    entries = [e for e in read_index(tmp_path, "2026-W40")["items"] if e["source"] != "riksbanken"]
    assert len(entries) == 1
    assert entries[0]["source"] == "mfn"
    assert entries[0]["also_in"] == [{"source": "cision", "url": "https://news.cision.com/se/fjallhem-fastigheter-ab/r/c1"}]
    assert not (tmp_path / "2026-W40" / "cision").exists()


def test_mfn_ersatter_cision_utdrag_som_kom_i_tidigare_korning(tmp_path):
    run(tmp_path, cision=cision_feed(cision_item("c1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:03:00 GMT")))
    assert (tmp_path / "2026-W40" / "cision" / "c1.txt").exists()

    run(tmp_path, mfn=mfn_feed(mfn_item("m1", "Fjällhem Fastigheter AB: Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000")))
    entries = [e for e in read_index(tmp_path, "2026-W40")["items"] if e["source"] != "riksbanken"]
    assert [(e["source"], e["id"]) for e in entries] == [("mfn", "m1")]
    assert entries[0]["also_in"][0]["source"] == "cision"
    assert not (tmp_path / "2026-W40" / "cision" / "c1.txt").exists()


def test_olika_rubriker_eller_langt_isar_i_tid_ar_inte_dubbletter(tmp_path):
    mfn = mfn_feed(mfn_item("m1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000"))
    cision = cision_feed(
        cision_item("c1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 10:00:00 GMT"),  # 2 h senare
        cision_item("c2", "Tallmo vinner order", "Fri, 02 Oct 2026 08:00:00 GMT"),
    )
    run(tmp_path, mfn=mfn, cision=cision)
    sources = sorted(e["id"] for e in read_index(tmp_path, "2026-W40")["items"] if e["source"] != "riksbanken")
    assert sources == ["c1", "c2", "m1"]


def test_upprepad_korning_ger_inga_dubbletter(tmp_path):
    mfn = mfn_feed(mfn_item("m1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000"))
    run(tmp_path, mfn=mfn)
    run(tmp_path, mfn=mfn)
    idx = read_index(tmp_path, "2026-W40")
    assert len(idx["items"]) == 2  # MFN-posten + styrräntan
    assert len(idx["runs"]) == 2
    assert idx["runs"][1]["sources"]["mfn"]["new"] == 0


def test_fallerande_kalla_loggas_och_ovriga_sparas(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    mfn = mfn_feed(mfn_item("m1", "Fjällhem tecknar avtal", "Fri, 02 Oct 2026 08:00:00 +0000"))
    code = run(tmp_path, mfn=mfn, cision=TimeoutError("timed out"))
    assert code == 0  # delvisa fel gör inte körningen röd
    assert "::warning" not in capsys.readouterr().out  # bara i Actions
    idx = read_index(tmp_path, "2026-W40")
    assert {e["source"] for e in idx["items"]} == {"mfn", "riksbanken"}
    assert idx["runs"][0]["sources"]["cision"]["status"] == "fel"
    assert "timed out" in idx["runs"][0]["sources"]["cision"]["error"]


def test_delvisa_fel_ger_warning_i_github_actions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert run(tmp_path, cision=TimeoutError("timed out")) == 0
    out = capsys.readouterr().out
    assert "::warning title=Källfel: cision::TimeoutError: timed out" in out
    assert "Källfel: mfn" not in out


def test_trasigt_flode_raknas_som_kallfel(tmp_path):
    assert run(tmp_path, mfn=b"<html>inte rss", riksbank=b"{}") == 0
    runs = read_index(tmp_path, "2026-W40")["runs"][0]["sources"]
    assert runs["mfn"]["status"] == "fel" and runs["riksbanken"]["status"] == "fel"


def test_alla_kallor_fallerar_ger_exitkod_1(tmp_path):
    err = ConnectionError("nere")
    assert run(tmp_path, mfn=err, cision=err, riksbank=err) == 1
    assert read_index(tmp_path, "2026-W40")["runs"][0]["sources"]["mfn"]["status"] == "fel"
