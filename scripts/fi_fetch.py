"""Finansinspektionens blanknings- och insynsregister till <raw-dir>/<vecka>/fi/.

Insyn hämtas från FI (ett anrop): gårdagens publiceringar i insynsregistret, daterade
"as_of" = gårdagen (svensk tid).

Blankning läses från filer som du laddat ner för hand, eftersom FI sedan 2026-10-29 avvisar
skriptade nedladdningar av blankningsregistret (404 från skript, 200 i webbläsaren, samma
adresser). Spärren kringgås inte. Ladda ner med knapparna på
https://www.fi.se/sv/vara-register/blankningsregistret/ och spara i data/fi_manual/:
    "Aktuella positioner"     AktuellaPositioner*.ods      enskilda positioner ≥ 0,5 %
    "Aggregerade positioner"  Aggregerade positioner*.ods  summa per emittent av positioner ≥ 0,1 %
    "Historiska positioner"   HistoriskaPositioner*.ods    (valfri) alla positioner ≥ 0,5 % bakåt
Den senaste filen av varje sort (ändringstid) läses. Datumet är filens eget: det senaste
positionsdatumet i filen (FI:s filer har inget annat datum). Aktuella och aggregerade läses
tillsammans till en ögonblicksbild.

Utdata:
    <vecka>/fi/blankning-<as_of>.json   aggregat + aktuella positioner (as_of = filens datum)
    <vecka>/fi/insyn-<as_of>.json       insynstransaktioner publicerade as_of
    fi-historik/blankning-historik-<as_of>.json   historiska positioner (om filen finns)
Skrivna blankningsfiler listas i last_written; morgonflödet (morning.py) kopierar dem till
data/raw/ och pushar dem till det privata repot, där veckans analys och signaler läser dem.

Användning:
    python scripts/fi_fetch.py [--raw-dir data/raw]                            # insyn + manuella filer
    python scripts/fi_fetch.py --utan-blankning                                # bara insyn (Actions)
    python scripts/fi_fetch.py --las-in    # direkt efter nedladdningen: läs in och säkerhetskopiera
    python scripts/fi_fetch.py --insyn-from 2026-10-05 --insyn-to 2026-10-10   # efterhämtning, ett anrop
Saknas manuella filer hoppas blankningen över utan fel; att ögonblicksbilden är för gammal
upptäcks av morgonrapporten, som visar en varning med instruktion.
Exit-kod: 0 = allt lyckades, 2 = någon del misslyckades (den andra sparades), 1 = allt misslyckades.
Fel skrivs som ::error:: i Actions, och körningen blir röd i båda felfallen.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch import ROOT, STOCKHOLM, USER_AGENT, _write_atomic, iso_week  # noqa: E402

import urllib.request  # noqa: E402

FI_TIMEOUT_S = 90
INSYN_URL = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
BLANKNING_PAGE = "https://www.fi.se/sv/vara-register/blankningsregistret/"
# Driftkopian (scripts\drift.ps1) pekar hit arbetskopians data\fi_manual, där filerna sparas för hand.
MANUAL_DIR = Path(os.environ["VF_FI_MANUAL_DIR"]) if os.environ.get("VF_FI_MANUAL_DIR") else ROOT / "data" / "fi_manual"
# Filnamnen som FI:s knappar ger (webbläsaren kan lägga till " (1)" o.d.).
MANUAL_FILES = {
    "aktuella": re.compile(r"^aktuella[ _-]?positioner.*\.ods$", re.IGNORECASE),
    "aggregerade": re.compile(r"^aggregerade[ _-]?positioner.*\.ods$", re.IGNORECASE),
    "historiska": re.compile(r"^historiska[ _-]?positioner.*\.ods$", re.IGNORECASE),
}

_ODS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=FI_TIMEOUT_S) as resp:
        return resp.read()


def insyn_url(pub_from: date, pub_to: date) -> str:
    """Samma fält, i samma ordning, som FI:s sökformulär skickar med knappen Exportera."""
    return INSYN_URL + "?" + urlencode([
        ("SearchFunctionType", "Insyn"),
        ("Utgivare", ""),
        ("PersonILedandeStällningNamn", ""),
        ("Transaktionsdatum.From", ""),
        ("Transaktionsdatum.To", ""),
        ("Publiceringsdatum.From", pub_from.isoformat()),
        ("Publiceringsdatum.To", pub_to.isoformat()),
        ("button", "export"),
        ("Page", "1"),
    ])


# --- Tolkning ----------------------------------------------------------------


def ods_rows(data: bytes) -> list[list[str]]:
    """Alla icke-tomma rader i första bladet i en ODS-fil, som text."""
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))
    table = next(root.iter(f"{{{_ODS['table']}}}table"))
    rows = []
    for r in table.iter(f"{{{_ODS['table']}}}table-row"):
        cells: list[str] = []
        for c in r.findall(f"{{{_ODS['table']}}}table-cell"):
            repeat = min(int(c.get(f"{{{_ODS['table']}}}number-columns-repeated", "1")), 50)
            text = " ".join((p.text or "") for p in c.iter(f"{{{_ODS['text']}}}p")).strip()
            cells += [text] * repeat
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            rows.append(cells)
    return rows


def _after_header(rows: list[list[str]], first_col: str) -> list[list[str]]:
    for i, r in enumerate(rows):
        if r and r[0].strip().casefold().startswith(first_col.casefold()):
            return rows[i + 1:]
    raise ValueError(f"hittar inte rubrikraden ({first_col!r})")


def parse_pct(value: str) -> float | None:
    """'4,72' -> 4.72. '<0,5' (positionen har sjunkit under rapportgränsen) -> None."""
    v = value.strip()
    if not v or v.startswith("<"):
        return None
    return float(v.replace(",", ".").replace(" ", ""))


def parse_aggregate(data: bytes) -> list[dict]:
    out = []
    for r in _after_header(ods_rows(data), "Namn på emittent"):
        r = r + [""] * (4 - len(r))
        if not r[0]:
            continue
        out.append({"issuer": r[0].strip(), "lei": r[1].strip(), "pct": parse_pct(r[2]), "date": r[3].strip()})
    return out


def parse_positions(data: bytes) -> list[dict]:
    """Aktuella eller historiska positioner ≥ 0,5 %. pct = None betyder '<0,5'."""
    out = []
    for r in _after_header(ods_rows(data), "Innehavare av positionen"):
        r = r + [""] * (6 - len(r))
        if not r[0]:
            continue
        out.append({
            "holder": r[0].strip(), "issuer": r[1].strip(), "isin": r[2].strip(),
            "pct": parse_pct(r[3]), "date": r[4].strip(), "comment": r[5].strip(),
        })
    return out


_INSYN_FIELDS = {
    "Publiceringsdatum": "published", "Emittent": "issuer", "LEI-kod": "lei",
    "Anmälningsskyldig": "reporter", "Person i ledande ställning": "person", "Befattning": "position",
    "Närstående": "closely_associated", "Korrigering": "correction", "Beskrivning av korrigering": "correction_note",
    "Är förstagångsrapportering": "initial_report", "Är kopplad till aktieprogram": "share_program",
    "Karaktär": "nature", "Instrumenttyp": "instrument_type", "Instrumentnamn": "instrument_name",
    "ISIN": "isin", "Transaktionsdatum": "transaction_date", "Volym": "volume", "Volymsenhet": "volume_unit",
    "Pris": "price", "Valuta": "currency", "Handelsplats": "venue", "Status": "status",
}


def _num(v: str) -> float | None:
    v = v.strip().replace(" ", "").replace(" ", "")
    return float(v.replace(",", ".")) if v else None


def parse_insyn(data: bytes) -> list[dict]:
    """FI:s insynsexport: UTF-16, semikolonseparerad, en rubrikrad."""
    text = data.decode("utf-16")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(";")]
    missing = {"Emittent", "ISIN", "Karaktär", "Volym", "Pris"} - set(header)
    if missing:
        raise ValueError(f"oväntad insynsexport, saknar fält {sorted(missing)}")
    out = []
    for line in lines[1:]:
        cells = line.split(";")
        raw = dict(zip(header, cells))
        row = {key: (raw.get(src) or "").strip() for src, key in _INSYN_FIELDS.items()}
        row["volume"], row["price"] = _num(row["volume"]), _num(row["price"])
        row["share_program"] = row["share_program"] == "Ja"
        row["initial_report"] = row["initial_report"] == "Ja"
        row["published_date"] = row["published"][:10]
        row["transaction_date"] = row["transaction_date"][:10]
        out.append(row)
    return out


# --- Körning -----------------------------------------------------------------


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def _week_dir(raw_dir: Path, day: date) -> Path:
    return raw_dir / iso_week(datetime.combine(day, datetime.min.time(), STOCKHOLM)) / "fi"


def manual_file(kind: str, manual_dir: Path = MANUAL_DIR) -> Path | None:
    """Den senast ändrade nedladdade filen av sorten ("aktuella", "aggregerade", "historiska")."""
    if not manual_dir.is_dir():
        return None
    files = [p for p in manual_dir.iterdir() if p.is_file() and MANUAL_FILES[kind].match(p.name)]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def file_date(rows: list[dict]) -> str:
    """Filens eget datum: det senaste positionsdatumet (FI:s filer har inget annat datum)."""
    dates = [r["date"][:10] for r in rows if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"][:10])]
    if not dates:
        raise ValueError("hittar inget positionsdatum i filen")
    return max(dates)


def import_blankning(raw_dir: Path, now: datetime, manual_dir: Path = MANUAL_DIR) -> str:
    """Ögonblicksbild ur de nedladdade filerna "Aktuella positioner" och "Aggregerade positioner"."""
    cur_path, agg_path = manual_file("aktuella", manual_dir), manual_file("aggregerade", manual_dir)
    if cur_path is None and agg_path is None:
        return f"inga nedladdade filer i {manual_dir} – hoppar över"
    if cur_path is None or agg_path is None:
        missing = "Aktuella positioner" if cur_path is None else "Aggregerade positioner"
        raise ValueError(f"filen \"{missing}\" saknas i {manual_dir} (båda behövs)")
    current = parse_positions(cur_path.read_bytes())
    aggregate = parse_aggregate(agg_path.read_bytes())
    as_of = date.fromisoformat(max(file_date(current), file_date(aggregate)))
    path = _week_dir(raw_dir, as_of) / f"blankning-{as_of}.json"
    _write_atomic(path, _dump({
        "as_of": as_of.isoformat(), "fetched_at": now.isoformat(timespec="seconds"),
        "sources": {"aggregate": f"manuell nedladdning: {agg_path.name}", "current": f"manuell nedladdning: {cur_path.name}",
                    "page": BLANKNING_PAGE},
        "aggregate": aggregate, "current": current,
    }))
    last_written.append(path)
    return f"{as_of}: {len(aggregate)} emittenter i aggregatet, {len(current)} positioner ≥ 0,5 % (manuellt nedladdade filer)"


def backup_written(staging: Path) -> tuple[bool, str]:
    """Kopierar blankningsfilerna som senaste main() skrev under staging (data/morning/raw/) till
    data/raw/ och pushar dem till det privata repot, där veckans analys och signaler läser dem."""
    from backup_week import commit_files

    if not last_written:
        return True, "inga nya blankningsfiler"
    files = [(p, p.relative_to(staging).as_posix()) for p in last_written]
    return commit_files(ROOT / "data" / "raw", files, "Blankning ur manuellt nedladdade FI-filer")


def import_history(raw_dir: Path, since: date, now: datetime, manual_dir: Path = MANUAL_DIR) -> str:
    """Historikfilen "Historiska positioner", om den laddats ner."""
    hist = manual_file("historiska", manual_dir)
    if hist is None:
        return f"ingen nedladdad historikfil i {manual_dir} – hoppar över"
    all_rows = parse_positions(hist.read_bytes())
    as_of = file_date(all_rows)
    rows = [r for r in all_rows if r["date"] >= since.isoformat()]
    path = raw_dir / "fi-historik" / f"blankning-historik-{as_of}.json"
    _write_atomic(path, _dump({
        "fetched_at": now.isoformat(timespec="seconds"), "as_of": as_of,
        "source": f"manuell nedladdning: {hist.name} ({BLANKNING_PAGE})", "since": since.isoformat(),
        "positions": rows,
    }))
    last_written.append(path)
    return f"{as_of}: {len(rows)} historiska positionsrader sedan {since} (manuellt nedladdad fil)"


def fetch_insyn(raw_dir: Path, pub_from: date, pub_to: date, now: datetime, get: Callable[[str], bytes]) -> str:
    """Ett anrop för hela intervallet; en fil per publiceringsdag (även tomma dagar,
    så att det syns att dagen är hämtad)."""
    url = insyn_url(pub_from, pub_to)
    rows = parse_insyn(get(url))
    day, written = pub_from, 0
    while day <= pub_to:
        day_rows = [r for r in rows if r["published_date"] == day.isoformat()]
        _write_atomic(_week_dir(raw_dir, day) / f"insyn-{day}.json", _dump({
            "publication_date": day.isoformat(), "fetched_at": now.isoformat(timespec="seconds"),
            "source": url, "rows": day_rows,
        }))
        written += len(day_rows)
        day += timedelta(days=1)
    return f"{written} transaktioner {pub_from}–{pub_to}"


# Felen från senaste main(): del ("blankning", "insyn", "historik") -> felmeddelande. Läses av
# morning.py, som visar felet i morgonrapporten.
last_errors: dict[str, str] = {}
# Blankningsfiler som senaste main() skrev (ögonblicksbild, historik), för säkerhetskopian i morning.py.
last_written: list[Path] = []


def main(argv: list[str] | None = None, get: Callable[[str], bytes] = http_get, now: datetime | None = None) -> int:
    # Utskrifterna innehåller t.ex. '≥', som Windows-konsolens cp1252 inte kan skriva.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    p.add_argument("--insyn-from", type=date.fromisoformat, help="efterhämta insyn från detta publiceringsdatum")
    p.add_argument("--insyn-to", type=date.fromisoformat, help="… till och med detta datum")
    p.add_argument("--utan-blankning", action="store_true",
                   help="läs inga nedladdade blankningsfiler (Actions: där finns inga)")
    p.add_argument("--las-in", action="store_true",
                   help="läs bara in de nedladdade blankningsfilerna (till data/morning/raw/) och säkerhetskopiera "
                        "dem till data/raw/; ingen hämtning")
    p.add_argument("--manual-dir", type=Path, default=MANUAL_DIR, help="mappen med nedladdade FI-filer")
    p.add_argument("--history-since", type=date.fromisoformat, default=date(2025, 1, 1))
    args = p.parse_args(argv)

    now = (now or datetime.now(STOCKHOLM)).astimezone(STOCKHOLM)
    as_of = now.date() - timedelta(days=1)

    jobs: dict[str, Callable[[], str]] = {}
    staging = ROOT / "data" / "morning" / "raw"
    if args.las_in:
        jobs["blankning"] = lambda: import_blankning(staging, now, args.manual_dir)
        jobs["historik"] = lambda: import_history(staging, args.history_since, now, args.manual_dir)
    elif args.insyn_from or args.insyn_to:
        if not (args.insyn_from and args.insyn_to) or args.insyn_from > args.insyn_to:
            p.error("--insyn-from och --insyn-to krävs båda, i rätt ordning")
        jobs["insyn"] = lambda: fetch_insyn(args.raw_dir, args.insyn_from, args.insyn_to, now, get)
    else:
        if not args.utan_blankning:
            jobs["blankning"] = lambda: import_blankning(args.raw_dir, now, args.manual_dir)
            jobs["historik"] = lambda: import_history(args.raw_dir, args.history_since, now, args.manual_dir)
        jobs["insyn"] = lambda: fetch_insyn(args.raw_dir, as_of, as_of, now, get)

    failed = []
    last_errors.clear()
    last_written.clear()
    for name, job in jobs.items():
        try:
            print(f"[{name}] {job()}")
        except Exception as err:  # noqa: BLE001 – varje del ska loggas, inte stoppa de andra
            failed.append(name)
            msg = f"{type(err).__name__}: {err}"[:300]
            last_errors[name] = msg
            print(f"[{name}] FEL: {msg}", file=sys.stderr)
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::error title=FI-fel: {name}::{msg}")
    if args.las_in and last_written:
        ok, msg = backup_written(staging)
        print(f"Säkerhetskopia: {msg}")
        if not ok:
            failed.append("säkerhetskopia")
            last_errors["säkerhetskopia"] = msg
    # Ett fel i någon del räknas (2/11 2026: blankningen gav 404 i flera dagar medan körningen var
    # grön eftersom insynen gick bra). 1 = allt misslyckades, 2 = någon del misslyckades.
    if not failed:
        return 0
    return 1 if len(failed) == len(jobs) else 2


if __name__ == "__main__":
    sys.exit(main())
