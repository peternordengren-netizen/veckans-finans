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
INSTRUMENTS = {"ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm")}
TEXT = ("Alfaverken announced in October that it would divest its Pump division. "
        "Alfaverken confirms that the regulator in Exampleland has prohibited the acquisition of the Pump business "
        "in Exampleland. The parties are in close dialogue.")
QUOTE = "Alfaverken confirms that the regulator in Exampleland has prohibited the acquisition of the Pump business in Exampleland."


def test_citatet_maste_finnas_i_texten():
    pub = datetime(2026, 11, 5, 7, 25, tzinfo=TZ)
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://alfaverken.example/news/1", pub, "Update on divestment",
                                TEXT, QUOTE, "en")
    assert it.id == "bolag-alfa-202611050725" and it.source == "bolag" and it.data == {"ticker": "ALFA", "quote": QUOTE}
    assert it.text_kind == "fulltext"
    with pytest.raises(ValueError, match="citatet"):
        add_raw_item.make_item("ALFA", "Alfaverken", "https://x.example", pub, "t", TEXT, "Något som inte står där.", "en")


def test_morgonrapporten_tar_med_posten_och_citatet(tmp_path):
    pub = datetime(2026, 11, 5, 7, 25, tzinfo=TZ)
    it = add_raw_item.make_item("ALFA", "Alfaverken", "https://alfaverken.example/news/1", pub, "Update on divestment",
                                TEXT, QUOTE, "en")
    store(RunResult(items=[it], log={"bolag": {"status": "ok", "items": 1}}), tmp_path, pub)
    news = overnight_news([tmp_path], INSTRUMENTS, datetime(2026, 11, 4, 17, 30, tzinfo=TZ),
                          datetime(2026, 11, 5, 8, 15, tzinfo=TZ), set())
    (c,) = news
    assert c["ticker"] == "ALFA"
    assert c["items"] == [{"published": "2026-11-05T07:25:00+01:00", "title": "Update on divestment",
                           "url": "https://alfaverken.example/news/1", "source": "Alfaverken", "quote": QUOTE}]
