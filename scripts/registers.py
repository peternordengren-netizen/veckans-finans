"""Räknar fram veckans insynsanalys för bolagen i instruments.csv.

Deterministiskt – ingen modell tolkar siffrorna. Läser FI-data som
scripts/fi_fetch.py sparat i rådatan och skriver data/analysis/<vecka>.json
(git-ignorerad, säkerhetskopieras till det privata repot av /sammanfatta-veckan).

Blankning räknas inte längre (borttaget 2026-11-11): FI tillåter inte automatisk hämtning av
blankningsregistret. Äldre analysfiler har kvar short_positions och short_aggregate, som sidan visar som de var.

Insyn:
    Transaktioner publicerade under veckan, karaktär Förvärv eller Avyttring, aktier,
    i SEK, inte kopplade till aktieprogram, status Aktuell och värde (volym × pris)
    över INSYN_MIN_SEK. Bolag där minst CLUSTER_MIN_PERSONS olika insynspersoner köpt
    (respektive sålt) markeras. Bolag som enligt veckans nyheter är målbolag för ett
    uppköpsbud eller en avnotering (bid_target i veckofilen) får under_offer = true, så
    att försäljningar inte läses som fristående säljbeslut. Transaktioner med
    handelsplats "Utanför handelsplats" får off_exchange = true; de räknas med i
    summorna men visas som "utanför börsen". late_report = true om publiceringen
    skedde mer än LATE_REPORT_BUSINESS_DAYS arbetsdagar efter transaktionen
    (scripts/workdays.py).

Användning:
    python scripts/registers.py 2026-W41 [--raw-dir data/raw] [--out data/analysis/2026-W41.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate import Instrument, bid_targets, iso_week_period, load_instruments, validate_analysis  # noqa: E402

from fetch import STOCKHOLM  # noqa: E402
from workdays import business_days_between  # noqa: E402

INSYN_MIN_SEK = 100_000
CLUSTER_MIN_PERSONS = 2
INSYN_NATURES = {"Förvärv": "köp", "Avyttring": "försäljning"}
TOP_N = 10
OFF_EXCHANGE_VENUE = "Utanför handelsplats"  # FI:s värde i fältet Handelsplats
# Insynspersoner ska anmäla inom tre arbetsdagar efter transaktionen (MAR art. 19).
LATE_REPORT_BUSINESS_DAYS = 3




# --- Hjälp -------------------------------------------------------------------


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- Insyn -------------------------------------------------------------------


def insyn_summary(raw_dir: Path, week: str, start: date, end: date, isin_to_ticker: dict[str, str],
                  instruments: dict[str, Instrument], under_offer: set[str] = frozenset(),
                  days: list[str] | None = None, files: dict[str, dict] | None = None) -> dict:
    """Insyn per bolag för veckans publiceringsdagar. days/files används av morgonrapporten
    (scripts/morning.py) för valfria dagar och redan inlästa filer; standard är veckans sju
    dagar ur <raw_dir>/<vecka>/fi/."""
    if files is None:
        files = {p.stem.removeprefix("insyn-"): _load(p) for p in (raw_dir / week / "fi").glob("insyn-*.json")}
    if days is None:
        days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    excluded = {"other_nature": 0, "not_share": 0, "share_program": 0, "not_current": 0, "non_sek": 0,
                "below_threshold": 0, "not_in_list": 0}
    per: dict[str, dict] = {}
    for day in days:
        for r in files.get(day, {}).get("rows", []):
            if r["nature"] not in INSYN_NATURES:
                excluded["other_nature"] += 1
            elif r["instrument_type"] != "Aktie":
                excluded["not_share"] += 1
            elif r["share_program"]:
                excluded["share_program"] += 1
            elif r["status"] != "Aktuell":
                excluded["not_current"] += 1
            elif r["currency"] != "SEK":
                excluded["non_sek"] += 1
            elif (ticker := isin_to_ticker.get(r["isin"])) is None:
                excluded["not_in_list"] += 1
            elif (value := (r["volume"] or 0) * (r["price"] or 0)) <= INSYN_MIN_SEK:
                excluded["below_threshold"] += 1
            else:
                c = per.setdefault(ticker, {"ticker": ticker, "name": instruments[ticker].namn, "transactions": []})
                c["transactions"].append({
                    "person": r["person"], "position": r["position"], "closely_associated": r["closely_associated"],
                    "side": INSYN_NATURES[r["nature"]], "transaction_date": r["transaction_date"],
                    "published_date": r["published_date"], "volume": r["volume"], "price": r["price"],
                    "value_sek": round(value),
                    # Räknas med i summorna men markeras – t.ex. blockaffärer i samband med fusion.
                    "off_exchange": r["venue"].strip().casefold() == OFF_EXCHANGE_VENUE.casefold(),
                    "business_days_to_publication": (bd := business_days_between(
                        date.fromisoformat(r["transaction_date"]), date.fromisoformat(r["published_date"]))),
                    "late_report": bd > LATE_REPORT_BUSINESS_DAYS,
                })
    companies = []
    for c in per.values():
        tx = sorted(c["transactions"], key=lambda t: (-t["value_sek"], t["person"]))
        buyers = sorted({t["person"] for t in tx if t["side"] == "köp"})
        sellers = sorted({t["person"] for t in tx if t["side"] == "försäljning"})
        buy = sum(t["value_sek"] for t in tx if t["side"] == "köp")
        sell = sum(t["value_sek"] for t in tx if t["side"] == "försäljning")
        companies.append({
            "ticker": c["ticker"], "name": c["name"], "buy_value_sek": buy, "sell_value_sek": sell,
            "net_value_sek": buy - sell, "buyers": buyers, "sellers": sellers,
            "cluster_buy": len(buyers) >= CLUSTER_MIN_PERSONS, "cluster_sell": len(sellers) >= CLUSTER_MIN_PERSONS,
            "under_offer": c["ticker"] in under_offer,
            "transactions": tx,
        })
    companies.sort(key=lambda c: (-(c["buy_value_sek"] + c["sell_value_sek"]), c["name"]))
    return {
        "threshold_sek": INSYN_MIN_SEK, "cluster_min_persons": CLUSTER_MIN_PERSONS,
        "days_covered": [d for d in days if d in files], "days_missing": [d for d in days if d not in files],
        "excluded": excluded, "companies": companies,
    }


# --- Sammanställning ---------------------------------------------------------


def build(week: str, raw_dir: Path, instruments: dict[str, Instrument], now: datetime,
          under_offer: set[str] = frozenset()) -> dict:
    """under_offer: tickers som är målbolag för uppköpsbud/avnotering enligt veckans
    nyheter (bid_target i data/weeks/<vecka>.json)."""
    start, end = iso_week_period(week)
    isin_to_ticker = {isin: t for t, i in instruments.items() for isin in i.isin}
    return {
        "schema_version": 1,
        "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": now.isoformat(timespec="seconds"),
        "top_n": TOP_N,
        "insider": insyn_summary(raw_dir, week, start, end, isin_to_ticker, instruments, under_offer),
    }


def _utf8_console() -> None:
    """Utskrifterna innehåller t.ex. '≥', som Windows-konsolens cp1252 inte kan skriva."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    _utf8_console()
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("week")
    p.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    p.add_argument("--instruments", type=Path, default=ROOT / "data" / "instruments.csv")
    p.add_argument("--out", type=Path)
    p.add_argument("--week-file", type=Path, help="veckofilen med bid_target (standard: data/weeks/<vecka>.json)")
    args = p.parse_args(argv)
    out = args.out or ROOT / "data" / "analysis" / f"{args.week}.json"
    week_file = args.week_file or ROOT / "data" / "weeks" / f"{args.week}.json"
    if week_file.exists():
        targets = bid_targets(json.loads(week_file.read_text(encoding="utf-8")))
    else:
        targets = set()
        print(f"Veckofilen saknas ({week_file}) – inga bolag markeras som under pågående uppköpsbud.", file=sys.stderr)

    analysis = build(args.week, args.raw_dir, load_instruments(args.instruments), now or datetime.now(STOCKHOLM), targets)
    res = validate_analysis(analysis, args.week)
    if res.errors:
        print("Analysen klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ins = analysis["insider"]
    print(f"Skrev {out}")
    print(f"  insyn: {len(ins['companies'])} bolag, dagar som saknas: {ins['days_missing'] or 'inga'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
