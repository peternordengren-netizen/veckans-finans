"""Hämtar Finansinspektionens blanknings- och insynsregister till <raw-dir>/<vecka>/fi/.

Körs en gång per dygn (tidigt på morgonen) i GitHub Actions i det privata
repot veckans-finans-raw, se deploy/raw-repo/.github/workflows/fi.yml. FI
begränsar antalet anrop per användare, så jobbet gör bara tre anrop:

    blankning, aggregat     summa av alla korta positioner ≥ 0,1 % per emittent
    blankning, aktuella     enskilda positioner ≥ 0,5 % (innehavare, ISIN)
    insyn, export           gårdagens publiceringar i insynsregistret

All data dateras till "as_of" = gårdagen (svensk tid). Ögonblicksbilden som tas
lördag morgon beskriver alltså fredagens läge och hamnar i fredagens vecka.

Utdata:
    <vecka>/fi/blankning-<as_of>.json   aggregat + aktuella positioner
    <vecka>/fi/insyn-<as_of>.json       insynstransaktioner publicerade as_of
    fi-historik/blankning-historik-<datum>.json   (bara med --history, en gång)

Adresserna kommer från knapparna på FI:s egna sidor (kontrollerat 2026-10-12);
filerna är ODS respektive UTF-16-CSV och läses med standardbiblioteket.

Användning:
    python scripts/fi_fetch.py [--raw-dir data/raw]
    python scripts/fi_fetch.py --insyn-from 2026-10-05 --insyn-to 2026-10-10   # efterhämtning, ett anrop
    python scripts/fi_fetch.py --history --history-since 2025-01-01            # historikfilen, en gång
Exit-kod: 0 = allt lyckades, 2 = någon del misslyckades (den andra sparades), 1 = allt misslyckades.
Fel skrivs som ::error:: i Actions, och körningen blir röd i båda felfallen.
"""

from __future__ import annotations

import argparse
import io
import json
import os
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

FI_TIMEOUT_S = 90  # historikfilen är ~2 MB och FI svarar ibland långsamt
BLANKNING_BASE = "https://www.fi.se/BlankningsRegister/"
AGGREGATE_URL = BLANKNING_BASE + "GetBlankningsregisterAggregat"
CURRENT_URL = BLANKNING_BASE + "GetAktuellFile"
HISTORY_URL = BLANKNING_BASE + "GetHistFile"
INSYN_URL = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"

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


def fetch_blankning(raw_dir: Path, as_of: date, now: datetime, get: Callable[[str], bytes]) -> str:
    aggregate = parse_aggregate(get(AGGREGATE_URL))
    current = parse_positions(get(CURRENT_URL))
    path = _week_dir(raw_dir, as_of) / f"blankning-{as_of}.json"
    _write_atomic(path, _dump({
        "as_of": as_of.isoformat(), "fetched_at": now.isoformat(timespec="seconds"),
        "sources": {"aggregate": AGGREGATE_URL, "current": CURRENT_URL},
        "aggregate": aggregate, "current": current,
    }))
    return f"{len(aggregate)} emittenter i aggregatet, {len(current)} positioner ≥ 0,5 %"


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


def fetch_history(raw_dir: Path, since: date, now: datetime, get: Callable[[str], bytes]) -> str:
    rows = [r for r in parse_positions(get(HISTORY_URL)) if r["date"] >= since.isoformat()]
    path = raw_dir / "fi-historik" / f"blankning-historik-{now.date()}.json"
    _write_atomic(path, _dump({
        "fetched_at": now.isoformat(timespec="seconds"), "source": HISTORY_URL, "since": since.isoformat(),
        "positions": rows,
    }))
    return f"{len(rows)} historiska positionsrader sedan {since}"


# Felen från senaste main(): del ("blankning", "insyn", "historik") -> felmeddelande. Läses av
# morning.py, som visar felet i morgonrapporten.
last_errors: dict[str, str] = {}


def main(argv: list[str] | None = None, get: Callable[[str], bytes] = http_get, now: datetime | None = None) -> int:
    # Utskrifterna innehåller t.ex. '≥', som Windows-konsolens cp1252 inte kan skriva.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    p.add_argument("--insyn-from", type=date.fromisoformat, help="efterhämta insyn från detta publiceringsdatum")
    p.add_argument("--insyn-to", type=date.fromisoformat, help="… till och med detta datum")
    p.add_argument("--history", action="store_true", help="hämta historikfilen (blankning ≥ 0,5 %%) en gång")
    p.add_argument("--history-since", type=date.fromisoformat, default=date(2025, 1, 1))
    args = p.parse_args(argv)

    now = (now or datetime.now(STOCKHOLM)).astimezone(STOCKHOLM)
    as_of = now.date() - timedelta(days=1)

    jobs: dict[str, Callable[[], str]] = {}
    if args.history:
        jobs["historik"] = lambda: fetch_history(args.raw_dir, args.history_since, now, get)
    elif args.insyn_from or args.insyn_to:
        if not (args.insyn_from and args.insyn_to) or args.insyn_from > args.insyn_to:
            p.error("--insyn-from och --insyn-to krävs båda, i rätt ordning")
        jobs["insyn"] = lambda: fetch_insyn(args.raw_dir, args.insyn_from, args.insyn_to, now, get)
    else:
        jobs["blankning"] = lambda: fetch_blankning(args.raw_dir, as_of, now, get)
        jobs["insyn"] = lambda: fetch_insyn(args.raw_dir, as_of, as_of, now, get)

    failed = []
    last_errors.clear()
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
    # Ett fel i någon del räknas (2/11 2026: blankningen gav 404 i flera dagar medan körningen var
    # grön eftersom insynen gick bra). 1 = allt misslyckades, 2 = någon del misslyckades.
    if not failed:
        return 0
    return 1 if len(failed) == len(jobs) else 2


if __name__ == "__main__":
    sys.exit(main())
