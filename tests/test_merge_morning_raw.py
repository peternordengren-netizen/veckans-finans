"""Tester för kompletteringen av veckans rådata med morgonens lokala hämtning (påhittade poster)."""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from merge_morning_raw import merge  # noqa: E402

NOW = datetime(2026, 10, 26, 8, 20, tzinfo=ZoneInfo("Europe/Stockholm"))


def item(id_, source, title, published, path, url=None, also_in=None):
    it = {"id": id_, "source": source, "title": title, "published": published, "path": path,
          "url": url or f"https://example.test/{id_}"}
    if also_in:
        it["also_in"] = also_in
    return it


def write(base: Path, items: list[dict]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for it in items:
        (base / it["path"]).parent.mkdir(parents=True, exist_ok=True)
        (base / it["path"]).write_text("Rubrik: x\n---\ntext\n", encoding="utf-8")
    (base / "index.json").write_text(json.dumps({"week": "2026-W43", "items": items}), encoding="utf-8")


def test_lagger_till_bara_det_som_saknas(tmp_path):
    actions = [item("a1", "mfn", "Alfaverken delårsrapport", "2026-10-21T08:00:00+02:00", "mfn/a1.txt",
                    also_in=[{"source": "cision", "url": "https://example.test/c9"}])]
    morning = [
        item("a1", "mfn", "Alfaverken delårsrapport", "2026-10-21T08:00:00+02:00", "mfn/a1.txt"),  # samma post
        item("c9", "cision", "Alfaverken delårsrapport", "2026-10-21T08:01:00+02:00", "cision/c9.txt"),  # also_in
        item("b2", "mfn", "Betabolaget: Q3 2026", "2026-10-22T07:00:00+02:00", "mfn/b2.txt"),  # saknas
        item("b3", "cision", "BETABOLAGET: Q3 2026", "2026-10-22T07:00:30+02:00", "cision/b3.txt"),  # samma rubrik och minut
    ]
    write(tmp_path / "raw", actions)
    write(tmp_path / "morning", morning)
    index, added = merge(tmp_path / "raw", tmp_path / "morning", NOW)
    assert [it["id"] for it in added] == ["b2"]
    assert [it["id"] for it in index["items"]] == ["a1", "b2"]  # i publiceringsordning
    assert index["merged_from_morning"] == [{"time": "2026-10-26T08:20:00+01:00", "items": 1}]
    assert index["items"][0] == actions[0]  # befintliga poster oförändrade


def test_inget_att_lagga_till(tmp_path):
    same = [item("a1", "mfn", "Alfaverken delårsrapport", "2026-10-21T08:00:00+02:00", "mfn/a1.txt")]
    write(tmp_path / "raw", same)
    write(tmp_path / "morning", same)
    index, added = merge(tmp_path / "raw", tmp_path / "morning", NOW)
    assert added == [] and "merged_from_morning" not in index


# --- Avsändaren i jämförelsen och rensningen (2026-11-09) -----------------------------------

import morning  # noqa: E402
import merge_morning_raw  # noqa: E402
from datetime import date  # noqa: E402
from validate import Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
INSTRUMENTS = {
    "ALFA": Instrument("Alfaverken", (), "ALFA", "ALFA.ST", "Stockholm", mfn_slug="alfaverken"),
    "BETA": Instrument("Betabolaget", (), "BETA", "BETA.ST", "Stockholm", mfn_slug="betabolaget"),
}
PUB = "2026-10-23T07:30:00+02:00"
TITLE = "Delårsrapport januari-september 2026"


def citem(n, company, source="mfn", title=TITLE, published=PUB):
    return {"id": n, "source": source, "url": f"https://mfn.example/{company}/{n}", "published": published,
            "title": title, "company": company, "path": f"{source}/{n}.txt"}


def write_week(base: Path, items: list[dict]) -> Path:
    w = base / "2026-W43"
    w.mkdir(parents=True, exist_ok=True)
    (w / "index.json").write_text(json.dumps({"week": "2026-W43", "items": items}, ensure_ascii=False), encoding="utf-8")
    for it in items:
        (w / it["path"]).parent.mkdir(parents=True, exist_ok=True)
        (w / it["path"]).write_text(f"Text {it['id']}", encoding="utf-8")
    return w


def test_samma_rubrik_samma_minut_fran_olika_bolag_ar_ingen_dubblett(tmp_path):
    raw = write_week(tmp_path / "raw", [citem("a1", "alfaverken")])
    mrn = write_week(tmp_path / "morning", [
        citem("b1", "betabolaget"),  # samma rubrik och minut, annat bolag: ska läggas till
        citem("a2", "alfaverken"),  # samma bolag, rubrik och minut (t.ex. språkversion med annat id): dubblett
        citem("c1", "alfaverken-ab", source="cision"),  # Cisions slug för samma bolag: dubblett
    ])
    index, added = merge_morning_raw.merge(raw, mrn, datetime(2026, 10, 26, tzinfo=TZ), INSTRUMENTS)
    assert [it["id"] for it in added] == ["b1"]
    assert {it["id"] for it in index["items"]} == {"a1", "b1"}


def test_rensningen_kopierar_forst_och_behaller_veckan_vid_fel(tmp_path):
    mrn = tmp_path / "morning"
    write_week(mrn, [citem("b1", "betabolaget")])
    (mrn / "2026-W45").mkdir()  # inom KEEP_RAW_WEEKS: rörs inte
    calls = []

    def failing(week, morning_raw):
        calls.append(week)
        return False, "git pull misslyckades", 0

    errors = morning._prune_morning_raw(date(2026, 11, 9), mrn, failing)
    assert calls == ["2026-W43"] and (mrn / "2026-W43").exists()
    assert errors and "står kvar" in errors[0]

    def ok(week, morning_raw):
        calls.append(week)
        return True, "Komplettering 2026-W43: 1 poster", 1

    assert morning._prune_morning_raw(date(2026, 11, 9), mrn, ok) == []
    assert not (mrn / "2026-W43").exists() and (mrn / "2026-W45").exists()


def test_komplettering_utan_veckan_i_radatarepot_misslyckas(tmp_path):
    mrn = tmp_path / "morning"
    write_week(mrn, [citem("b1", "betabolaget")])
    ok, msg, n = merge_morning_raw.complete("2026-W43", tmp_path / "raw", mrn, dry_run=True, instruments=INSTRUMENTS)
    assert not ok and n == 0 and "saknar index.json" in msg
    write_week(tmp_path / "raw", [citem("a1", "alfaverken")])
    ok, msg, n = merge_morning_raw.complete("2026-W43", tmp_path / "raw", mrn, dry_run=True, instruments=INSTRUMENTS)
    assert ok and n == 1
