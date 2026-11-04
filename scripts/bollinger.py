"""Bollingerkontroll under handelsdagen: bolag vid eller över det övre Bollingerbandet, och inom 1 % under det.

Körs av scripts/run_bollinger.ps1 (schemalagd vardagar 09:20 och 13:20, utan Claude), som visar en Windows-notis
och bygger om sidan ("Bollinger" överst på startsidan). Ett informationsverktyg: listan beskriver var kursen ligger
mot bandet och är ingen uppmaning.

- Kurserna: ett batchanrop till Yahoo (scripts/signals.yahoo_bars) för alla bolag i instruments.csv. Dagens senaste
  kurs är fördröjd ca 15 minuter (Yahoo). Ett bolag utan kurs i dag (ingen handel än) räknas inte.
- Övre bandet (upper_band): medelvärdet av de 20 senaste stängningskurserna plus 2 standardavvikelser (populationens,
  som i Bollingers definition). Under dagen är den aktuella kursen den tjugonde, efter de 19 senaste stängningarna;
  i fliken Swing (scripts/swing.py) gårdagens stängning efter de 19 före. Under dagen används faktiska kurser
  (dagens kurs är inte justerad); i Swing justerade stängningar som övriga mått.
- "Vid eller över": kursen ≥ övre bandet. "Inom 1 %": kursen under bandet men högst NEAR_PCT % under.

Skriver data/bollinger/<datum>-<hhmm>.json (git-ignorerad).

Användning:
    python scripts/bollinger.py                # kontrollen nu
    python scripts/bollinger.py --handelsdag   # bara kontrollen av börsdag: exit 0 = handelsdag, 3 = inte
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from morning import is_business_day  # noqa: E402
from signals import BarFetcher, yahoo_bars  # noqa: E402
from validate import DATA_DIR, Instrument, load_instruments  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
BOLLINGER_DIR = DATA_DIR / "bollinger"
PERIOD = 20
WIDTH = 2.0
NEAR_PCT = 1.0
FETCH_DAYS = 45  # 20 handelsdagar och marginal för helger och helgdagar
DELAY_NOTE = "Kurserna från Yahoo är fördröjda ca 15 minuter."


def upper_band(values: list[float], n: int = PERIOD, k: float = WIDTH) -> float | None:
    """Övre Bollingerbandet för de n sista värdena (medel + k populationsstandardavvikelser), None om färre än n."""
    if len(values) < n:
        return None
    last = values[-n:]
    return statistics.fmean(last) + k * statistics.pstdev(last)


def status(price: float, upper: float, near_pct: float = NEAR_PCT) -> str | None:
    """"over" (vid eller över bandet), "near" (högst near_pct % under) eller None."""
    if price >= upper:
        return "over"
    if price >= upper * (1 - near_pct / 100):
        return "near"
    return None


def check(today: date, now: datetime, instruments: dict[str, Instrument], fetch: BarFetcher) -> dict:
    tickers = {i.yahoo_ticker: t for t, i in instruments.items()}
    bars = fetch(list(tickers), today - timedelta(days=FETCH_DAYS), today)
    over, near, missing = [], [], []
    for yt, t in tickers.items():
        b = bars.get(yt, [])
        if not b or b[-1].day != today:
            missing.append(t)
            continue
        price = b[-1].close
        upper = upper_band([x.close for x in b[:-1]][-(PERIOD - 1):] + [price])
        if upper is None:
            missing.append(t)
            continue
        st = status(price, upper)
        if st:
            row = {"ticker": t, "name": instruments[t].namn, "price": round(price, 4), "upper": round(upper, 4),
                   "vs_upper_pct": round((price / upper - 1) * 100, 2)}
            (over if st == "over" else near).append(row)
    for rows in (over, near):
        rows.sort(key=lambda r: -r["vs_upper_pct"])
    return {
        "date": today.isoformat(),
        "checked_at": now.isoformat(timespec="seconds"),
        "delay_note": DELAY_NOTE,
        "thresholds": {"period": PERIOD, "width": WIDTH, "near_pct": NEAR_PCT},
        "checked": len(tickers) - len(missing),
        "missing": len(missing),
        "over": over,
        "near": near,
    }


def validate(data: dict) -> list[str]:
    """Statusen räknas om ur pris och band; listorna får inte överlappa."""
    errors = []
    for key in ("over", "near"):
        for r in data[key]:
            if status(r["price"], r["upper"], data["thresholds"]["near_pct"]) != key:
                errors.append(f"{r['name']}: {r['price']} mot bandet {r['upper']} hör inte till {key!r}")
    both = {r["ticker"] for r in data["over"]} & {r["ticker"] for r in data["near"]}
    if both:
        errors.append(f"i båda listorna: {sorted(both)}")
    return errors


def main(argv: list[str] | None = None, fetch: BarFetcher = yahoo_bars, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--handelsdag", action="store_true", help="kontrollera bara om i dag är en handelsdag")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    today = now.date()
    if args.handelsdag:
        return 0 if is_business_day(today) else 3
    if not is_business_day(today):
        print(f"{today} är ingen handelsdag – ingen Bollingerkontroll.")
        return 3
    data = check(today, now, load_instruments(), fetch)
    errors = validate(data)
    if errors:
        print("Bollingerkontrollen klarar inte valideringen – skrivs inte:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    if data["checked"] == 0:
        print("Inga kurser för i dag från Yahoo – ingen Bollingerkontroll skrivs.", file=sys.stderr)
        return 2
    out = BOLLINGER_DIR / f"{today}-{now:%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Bollinger {now:%Y-%m-%d %H:%M}: {data['checked']} bolag, vid eller över övre bandet "
          f"{len(data['over'])}, inom 1 % {len(data['near'])} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
