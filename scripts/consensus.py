"""Sparar Yahoos konsensus (vinst per aktie, omsättning, antal analytiker, valuta) för alla bolag
i instruments.csv, en fil per dag: data/consensus/<datum>.json (schema/consensus.schema.json).

Varför en ögonblicksbild per dag: Yahoos prognos för "innevarande kvartal" (0q) byter till
nästa kvartal när bolaget rapporterat. För att kunna jämföra utfall mot förväntan efteråt
måste förväntan sparas före rapporten. Morgonkörningen (scripts/morning.py) kör skriptet
varje handelsdag.

Per bolag (0q = kvartalet efter most_recent_quarter, det som rapporteras härnäst):
- eps / revenue: avg, low, high, analysts, year_ago och currency (prognosens valuta)
- next_report_date: Yahoos nästa rapportdatum (kan vara uppskattat)
- financial_currency: valutan bolaget redovisar i; analyst_opinions: antal rekommendationer
- after_report: bolaget hade redan publicerat en rapport samma dag innan bilden togs. Då kan
  Yahoo redan ha bytt till nästa kvartal eller uppdaterat prognosen – posten visas som
  "konsensus efter rapport" och används aldrig för jämförelse (scripts/results.py).

Ett anrop per bolag och datatyp (estimat för vinst och omsättning, kalender, info).
Fail-soft: ett bolag som inte går att hämta hamnar i "errors".

Användning:
    python scripts/consensus.py              # i dag
    python scripts/consensus.py --backup     # och säkerhetskopiera till det privata repot
    python scripts/consensus.py --markera 2026-10-19 --backup   # märk om en befintlig bild (ingen hämtning)
Exit-kod: 0 = filen skriven, 1 = ingen konsensus kunde hämtas eller valideringen underkände.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from retry import with_retries  # noqa: E402
from validate import DATA_DIR, RAW_DIR, Instrument, load_instruments, validate_consensus  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
CONSENSUS_DIR = DATA_DIR / "consensus"


def _num(v) -> float | None:
    """Tal eller None (NaN, None och tomma värden blir None)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _int(v) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _estimate(df, year_ago_col: str) -> dict | None:
    """Raden '0q' ur yfinance earnings_estimate/revenue_estimate."""
    if df is None or getattr(df, "empty", True) or "0q" not in df.index:
        return None
    row = df.loc["0q"]
    avg = _num(row.get("avg"))
    if avg is None:
        return None
    return {
        "avg": avg,
        "low": _num(row.get("low")),
        "high": _num(row.get("high")),
        "analysts": _int(row.get("numberOfAnalysts")),
        "year_ago": _num(row.get(year_ago_col)),
        "currency": row.get("currency") if isinstance(row.get("currency"), str) else None,
    }


def yahoo_consensus(yahoo_ticker: str) -> dict:
    """Konsensus för ett bolag ur yfinance."""
    import yfinance as yf

    t = yf.Ticker(yahoo_ticker)
    info = t.info or {}
    cal = t.calendar or {}
    dates = [d for d in (cal.get("Earnings Date") or []) if isinstance(d, date)]
    mrq = info.get("mostRecentQuarter")
    return {
        "eps": _estimate(t.earnings_estimate, "yearAgoEps"),
        "revenue": _estimate(t.revenue_estimate, "yearAgoRevenue"),
        "next_report_date": min(dates).isoformat() if dates else None,
        "most_recent_quarter": datetime.fromtimestamp(mrq, timezone.utc).date().isoformat() if mrq else None,
        "financial_currency": info.get("financialCurrency"),
        "analyst_opinions": _int(info.get("numberOfAnalystOpinions")),
    }


def build(day: date, now: datetime, instruments: dict[str, Instrument],
          get: Callable[[str], dict] = yahoo_consensus) -> dict:
    companies, errors = [], []
    for inst in instruments.values():
        try:
            c = with_retries(lambda: get(inst.yahoo_ticker), f"{inst.yahoo_ticker} (konsensus)")
        except Exception as exc:  # noqa: BLE001 – ett bolag får aldrig stoppa resten
            errors.append(f"{inst.yahoo_ticker}: {type(exc).__name__}")
            continue
        if c["eps"] is None and c["revenue"] is None:
            continue  # ingen konsensus alls – bolaget utelämnas
        # after_report sätts av mark_after_report() när bilden är tagen.
        companies.append({"ticker": inst.ticker, "name": inst.namn, "yahoo_ticker": inst.yahoo_ticker, **c,
                          "after_report": False})
    return {
        "schema_version": 1,
        "date": day.isoformat(),
        "fetched_at": now.isoformat(timespec="seconds"),
        "source": "Yahoo Finance via yfinance (earnings_estimate, revenue_estimate, calendar, info)",
        "companies": companies,
        "errors": errors,
    }


PRELIM_LOOKBACK_DAYS = 45


def mark_after_report(data: dict, instruments: dict[str, Instrument], raw_dirs: dict | None = None) -> int:
    """Sätter after_report på bolag vars siffror redan var kända när bilden togs (fetched_at):
    - rapport publicerad från dagens början fram till bilden, eller
    - preliminära siffror de senaste PRELIM_LOOKBACK_DAYS dagarna, utan att den riktiga
      rapporten kommit efter dem (marknaden känner då redan utfallet).
    Returnerar antalet märkta bolag."""
    from report_list import list_window
    from validate import load_sector_kpis

    fetched = datetime.fromisoformat(data["fetched_at"])
    day_start = datetime.combine(date.fromisoformat(data["date"]), datetime.min.time(), fetched.tzinfo)
    kpis, dirs = load_sector_kpis(), raw_dirs or {}
    reported = {r["ticker"] for r in list_window(day_start, fetched, instruments, kpis, **dirs)}
    lookback = fetched - timedelta(days=PRELIM_LOOKBACK_DAYS)
    prelims = {r["ticker"]: r["published"] for r in list_window(lookback, fetched, instruments, kpis, **dirs,
                                                                 prelim=True)}
    reports = {r["ticker"]: r["published"] for r in list_window(lookback, fetched, instruments, kpis, **dirs)}
    reported |= {t for t, when in prelims.items() if not (t in reports and reports[t] > when)}
    for c in data["companies"]:
        c["after_report"] = c["ticker"] in reported
    return sum(c["after_report"] for c in data["companies"])


def write(data: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)


def main(argv: list[str] | None = None, get: Callable[[str], dict] = yahoo_consensus,
         now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--backup", action="store_true", help="säkerhetskopiera till data/raw/consensus/")
    p.add_argument("--markera", metavar="DATUM", help="märk om en befintlig bild (after_report), utan ny hämtning")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    instruments = load_instruments()
    if args.markera:
        path = CONSENSUS_DIR / f"{args.markera}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = build(now.date(), now, instruments, get)
    marked = mark_after_report(data, instruments)
    if not data["companies"]:
        print("Ingen konsensus kunde hämtas – ingen fil skrivs.", file=sys.stderr)
        return 1
    # Ett bolag som inte klarar valideringen hoppas över ("skipped", med orsaken) i stället för att stoppa filen
    # (2026-11-16 skrevs ingen konsensusfil alls).
    from validate import prune_invalid

    res, skipped = prune_invalid(data, lambda d: validate_consensus(d, instruments), lambda d: [d["companies"]])
    for s in skipped:
        print(f"Konsensus: {s['name']} hoppas över (klarar inte valideringen): {s['reason']}", file=sys.stderr)
    if res.errors:
        print("Konsensusfilen klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    out = CONSENSUS_DIR / f"{data['date']}.json"
    write(data, out)
    eps = sum(c["eps"] is not None for c in data["companies"])
    eps3 = sum(((c["eps"] or {}).get("analysts") or 0) >= 3 for c in data["companies"])
    other = sum((c["financial_currency"] or "SEK") != "SEK" for c in data["companies"])
    print(f"Konsensus {data['date']} (hämtad {data['fetched_at'][11:16]}): {len(data['companies'])} bolag, "
          f"{marked} hade redan rapporterat (konsensus efter rapport), vinst per aktie för {eps} "
          f"({eps3} med minst 3 analytiker), {other} redovisar i annan valuta än SEK, "
          f"{len(data['errors'])} fel -> {out}")
    if args.backup:
        from backup_week import commit_files

        ok, msg = commit_files(RAW_DIR, [(out, f"consensus/{out.name}")], f"Konsensus {data['date']}")
        print(f"Säkerhetskopia: {msg}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
