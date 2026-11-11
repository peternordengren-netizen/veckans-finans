"""Finansinspektionens insynsregister till <raw-dir>/<vecka>/fi/.

Insyn hämtas från FI (ett anrop): gårdagens publiceringar i insynsregistret, daterade
"as_of" = gårdagen (svensk tid).

Blankning hämtas inte längre (borttaget 2026-11-11): FI avvisar skriptade nedladdningar av
blankningsregistret sedan 2026-10-29, och den manuella vägen (filer i data/fi_manual/) är också
borttagen. Äldre blankningsfiler i rådatarepot (<vecka>/fi/blankning-*.json) ligger kvar och
läses inte av något nytt.

Utdata:
    <vecka>/fi/insyn-<as_of>.json       insynstransaktioner publicerade as_of

Användning:
    python scripts/fi_fetch.py [--raw-dir data/raw]                            # insyn
    python scripts/fi_fetch.py --insyn-from 2026-10-05 --insyn-to 2026-10-10   # efterhämtning, ett anrop
--utan-blankning accepteras (Actions-jobbet i rådatarepot skickar den) men gör ingenting.
Exit-kod: 0 = allt lyckades, 1 = hämtningen misslyckades. Fel skrivs som ::error:: i Actions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch import ROOT, STOCKHOLM, USER_AGENT, _write_atomic, iso_week  # noqa: E402

import urllib.request  # noqa: E402

FI_TIMEOUT_S = 90
INSYN_URL = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
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


# Felen från senaste main(): del ("insyn") -> felmeddelande. Läses av morning.py, som visar felet i
# morgonrapporten.
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
    p.add_argument("--utan-blankning", action="store_true",
                   help="gör ingenting (blankning hämtas inte längre); kvar för Actions-jobbet i rådatarepot")
    args = p.parse_args(argv)

    now = (now or datetime.now(STOCKHOLM)).astimezone(STOCKHOLM)
    as_of = now.date() - timedelta(days=1)

    jobs: dict[str, Callable[[], str]] = {}
    if args.insyn_from or args.insyn_to:
        if not (args.insyn_from and args.insyn_to) or args.insyn_from > args.insyn_to:
            p.error("--insyn-from och --insyn-to krävs båda, i rätt ordning")
        jobs["insyn"] = lambda: fetch_insyn(args.raw_dir, args.insyn_from, args.insyn_to, now, get)
    else:
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
    # 1 = allt misslyckades, 2 = någon del misslyckades (i dag finns bara en del, insyn).
    if not failed:
        return 0
    return 1 if len(failed) == len(jobs) else 2


if __name__ == "__main__":
    sys.exit(main())
