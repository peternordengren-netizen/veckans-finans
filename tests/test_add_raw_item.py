"""Tester för pressmeddelanden från bolagets egen webbplats (scripts/add_raw_item.py) i morgonrapporten
(påhittat bolag och påhittad text)."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import add_raw_item  # noqa: E402
from fetch import RunResult, store  # noqa: E402
from morning import overnight_news  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
PUB = datetime(2026, 11, 5, 7, 25, tzinfo=TZ)
INSTRUMENTS = {"ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm")}
TEXT = ("Alfaverken announced in October that it would divest its Pump division. "
        "Alfaverken confirms that the regulator in Exampleland has prohibited the acquisition of the Pump business "
        "in Exampleland. The parties are in close dialogue.")
QUOTE = "Alfaverken confirms that the regulator in Exampleland has prohibited the acquisition of the Pump business in Exampleland."
SUMMARY = "Alfaverken meddelar att tillsynsmyndigheten i Exempelland har stoppat köpet av Pumpdivisionens verksamhet där."


def news_for(tmp_path, it):
    store(RunResult(items=[it], log={"bolag": {"status": "ok", "items": 1}}), tmp_path, PUB)
    (c,) = overnight_news([tmp_path], INSTRUMENTS, datetime(2026, 11, 4, 17, 30, tzinfo=TZ),
                          datetime(2026, 11, 5, 8, 15, tzinfo=TZ), set())
    assert c["ticker"] == "ALFA"
    return c["items"]


def test_egen_sammanfattning_ar_standard_och_sparar_ingen_text_ur_pressmeddelandet(tmp_path):
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://alfaverken.example/news/1", PUB, "Update on divestment",
                                summary=SUMMARY)
    assert it.id == "bolag-alfa-202611050725" and it.source == "bolag"
    assert it.text == SUMMARY and it.text_kind == "sammanfattning"
    assert it.data == {"ticker": "ALFA", "summary": SUMMARY}
    assert "manuellt tillagd" in it.tags
    store(RunResult(items=[it], log={"bolag": {"status": "ok", "items": 1}}), tmp_path, PUB)
    saved = (tmp_path / "2026-W45" / "bolag" / "bolag-alfa-202611050725.txt").read_text(encoding="utf-8")
    assert "Exampleland" not in saved and SUMMARY in saved


def test_morgonrapporten_visar_sammanfattningen_markt_manuellt_tillagd(tmp_path):
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://alfaverken.example/news/1", PUB, "Update on divestment",
                                summary=SUMMARY)
    assert news_for(tmp_path, it) == [{"published": "2026-11-05T07:25:00+01:00", "title": "Update on divestment",
                                       "url": "https://alfaverken.example/news/1", "source": "Alfaverken",
                                       "manual": True, "summary": SUMMARY}]


def test_ordagrann_text_bara_nar_villkoren_tillater_atergivning():
    with pytest.raises(ValueError, match="villkor"):
        add_raw_item.make_item("ALFA", "Alfaverken", "https://x.example", PUB, "t", text=TEXT, quote=QUOTE)
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://x.example", PUB, "t", text=TEXT, quote=QUOTE,
                                reproduction_allowed=True)
    assert it.text_kind == "fulltext" and it.data == {"ticker": "ALFA", "quote": QUOTE}
    with pytest.raises(ValueError, match="citatet"):
        add_raw_item.make_item("ALFA", "Alfaverken", "https://x.example", PUB, "t", text=TEXT,
                               quote="Något som inte står där.", reproduction_allowed=True)


def test_morgonrapporten_visar_citatet_nar_atergivning_ar_tillaten(tmp_path):
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://alfaverken.example/news/1", PUB, "Update on divestment",
                                text=TEXT, quote=QUOTE, reproduction_allowed=True)
    (entry,) = news_for(tmp_path, it)
    assert entry["quote"] == QUOTE and entry["manual"] is True and "summary" not in entry


@pytest.mark.parametrize("kwargs, match", [
    ({}, "antingen"),
    ({"summary": SUMMARY, "text": TEXT}, "antingen"),
    ({"summary": "   "}, "tom"),
    ({"summary": " ".join(["ord"] * 61)}, "60 ord"),
    ({"summary": SUMMARY, "quote": QUOTE}, "citat"),
])
def test_ogiltiga_kombinationer(kwargs, match):
    with pytest.raises(ValueError, match=match):
        add_raw_item.make_item("ALFA", "Alfaverken", "https://x.example", PUB, "t", **kwargs)
