"""Hämtar veckans kursförändring för matchade bolag och skriver data/prices/<vecka>.json.

Läser yahoo_ticker för alla bolag med "match": "matchad" i data/weeks/<vecka>.json
och hämtar dagskurser via yfinance (ett anrop per ticker; valutan kommer ur
samma svar).

Veckans förändring = senaste stängning t.o.m. veckans söndag (eller i dag, om
veckan pågår) jämfört med senaste stängning före veckans måndag. Förändringen
räknas på justerad stängning (utdelningar och splittar påverkar inte), medan
"close" är den faktiska stängningskursen.

Fail-soft – sidan fungerar utan kursfilen:
- en ticker som inte går att hämta hamnar i "errors" och övriga skrivs ändå
- går ingen ticker att hämta (eller saknas yfinance) skrivs ingen fil, och en
  eventuell befintlig fil lämnas orörd

Användning:
    python scripts/prices.py 2026-W40
    python scripts/prices.py 2026-W40 --mock      # läser data/mock/weeks, skriver till --out
    python scripts/prices.py 2026-W40 --out /tmp/p.json
Exit-kod: 0 = filen skriven (ev. med errors), 1 = ingen ticker kunde hämtas
eller indata saknas.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate import iso_week_period, validate_prices  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
DATA = ROOT / "data"
LOOKBACK_DAYS = 10  # tillräckligt för att hitta föregående stängning även över långhelger


@dataclass
class History:
    """Dagliga stängningar för en ticker, äldst först."""

    rows: list[tuple[date, float, float]]  # (dag, stängning, justerad stängning)
    currency: str | None


Fetcher = Callable[[str, date, date], History]


def yahoo_fetch(ticker: str, start: date, end_inclusive: date) -> History:
    """Hämtar dagskurser från Yahoo Finance via yfinance. Importeras först här
    så att resten av projektet (och testerna) inte kräver yfinance."""
    import yfinance as yf

    # Låt fel bubbla upp (i stället för tom DataFrame) så att orsaken hamnar i "errors".
    yf.config.debug.hide_exceptions = False
    t = yf.Ticker(ticker)
    df = t.history(start=start.isoformat(), end=(end_inclusive + timedelta(days=1)).isoformat(),
                   interval="1d", auto_adjust=False, actions=False)
    rows = []
    for idx, row in df.iterrows():
        close = row.get("Close")
        adj = row.get("Adj Close", close)
        if close is None or close != close:  # NaN
            continue
        rows.append((idx.date(), float(close), float(adj if adj == adj else close)))
    currency = (t.get_history_metadata() or {}).get("currency")
    return History(rows=rows, currency=currency)


def week_change(hist: History, start: date, end: date) -> dict:
    """Räknar veckans förändring. Kastar ValueError om data saknas."""
    before = [r for r in hist.rows if r[0] < start]
    during = [r for r in hist.rows if start <= r[0] <= end]
    if not before:
        raise ValueError("ingen stängning före veckan")
    if not during:
        raise ValueError("ingen stängning under veckan")
    if not hist.currency or len(hist.currency) != 3:
        raise ValueError(f"okänd valuta {hist.currency!r}")
    prev, last = before[-1], during[-1]
    change = (last[2] / prev[2] - 1) * 100
    return {"change_pct": round(change, 2), "close": round(last[1], 4), "currency": hist.currency.upper()}


def matched_tickers(week_data: dict) -> list[str]:
    tickers = {
        c["yahoo_ticker"]
        for s in week_data["sectors"]
        for it in s["items"]
        for c in it["companies"]
        if c.get("match") == "matchad" and c.get("yahoo_ticker")
    }
    return sorted(tickers)


def build_prices(week_data: dict, fetch: Fetcher, today: date, now: datetime) -> dict:
    week = week_data["week"]
    start, end = iso_week_period(week)
    end = min(end, today)
    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for ticker in matched_tickers(week_data):
        try:
            quotes[ticker] = week_change(fetch(ticker, start - timedelta(days=LOOKBACK_DAYS), end), start, end)
        except Exception as err:  # noqa: BLE001 – varje tickerfel ska bara loggas
            errors[ticker] = f"{type(err).__name__}: {err}"[:200]
            print(f"[{ticker}] FEL: {errors[ticker]}", file=sys.stderr)
    out = {
        "schema_version": 1,
        "week": week,
        "fetched_at": now.isoformat(timespec="seconds"),
        "quotes": quotes,
    }
    if week_data.get("mock"):
        out["mock"] = True
    if errors:
        out["errors"] = errors
    return out


def main(argv: list[str] | None = None, fetch: Fetcher | None = None,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("week", help="ISO-vecka, t.ex. 2026-W40")
    parser.add_argument("--mock", action="store_true", help="läs veckofilen från data/mock/weeks/")
    parser.add_argument("--out", type=Path, help="utfil (standard: data/prices/<vecka>.json)")
    args = parser.parse_args(argv)

    week_path = DATA / ("mock/weeks" if args.mock else "weeks") / f"{args.week}.json"
    if args.mock and args.out is None:
        # Den kurerade mockkursfilen ska inte skrivas över av misstag.
        parser.error("--mock kräver --out")
    out_path = args.out or DATA / "prices" / f"{args.week}.json"

    if not week_path.exists():
        print(f"Veckofilen saknas: {week_path}", file=sys.stderr)
        return 1
    week_data = json.loads(week_path.read_text(encoding="utf-8"))

    if fetch is None:
        try:
            import yfinance  # noqa: F401
        except ImportError:
            print("yfinance saknas – ingen kursfil skrivs (sidan visas utan kurser).", file=sys.stderr)
            return 1
        fetch = yahoo_fetch

    now = now or datetime.now(STOCKHOLM)
    prices = build_prices(week_data, fetch, now.date(), now)
    n_ok, n_err = len(prices["quotes"]), len(prices.get("errors", {}))
    if n_ok == 0:
        print(f"Ingen av {n_err} tickers kunde hämtas – ingen kursfil skrivs.", file=sys.stderr)
        return 1

    res = validate_prices(prices, args.week)
    if res.errors:
        print("Kursfilen klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(prices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Skrev {out_path}: {n_ok} kurser, {n_err} fel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
