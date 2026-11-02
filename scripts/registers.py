"""Räknar fram veckans blanknings- och insynsanalys för bolagen i instruments.csv.

Deterministiskt – ingen modell tolkar siffrorna. Läser FI-data som
scripts/fi_fetch.py sparat i rådatan och skriver data/analysis/<vecka>.json
(git-ignorerad, säkerhetskopieras till det privata repot av /sammanfatta-veckan).

Blankning, positioner ≥ 0,5 % (per innehavare, med ISIN):
    Läget vid veckans slut (söndag) jämförs med läget vid förra veckans slut.
    Läget vid en tidpunkt = senaste kända värde per (innehavare, ISIN) med datum
    ≤ tidpunkten, ur historikfilen (fi-historik/, hämtad en gång) och alla
    dagliga ögonblicksbilder. "<0,5" och positioner som försvunnit ur en
    ögonblicksbild räknas som 0. Summeras per bolag över alla aktieslag (isin-kolumnen).

Blankning, aggregat (alla positioner ≥ 0,1 %, bara emittentnamn och LEI):
    Bara från egna ögonblicksbilder: veckans sista mot förra veckans sista. Saknas
    någon av dem anges available = false med en förklaring. Emittenten kopplas till
    listan via namn -> ISIN i samma ögonblicksbilds aktuella positioner, annars via
    normaliserat namn mot namn/alias.

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
import re
import sys
import unicodedata
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


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(publ\)|\baktiebolag(et)?\b|\bab\b|\bplc\b|\boyj\b|\babp\b|\bltd\b|\bgroup\b|\bholding\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshots(raw_dir: Path) -> list[dict]:
    """Alla blankningsögonblicksbilder, äldst först."""
    snaps = [_load(p) for p in raw_dir.glob("*/fi/blankning-*.json")]
    return sorted(snaps, key=lambda s: s["as_of"])


def _latest_in(snaps: list[dict], start: date, end: date) -> dict | None:
    inside = [s for s in snaps if start.isoformat() <= s["as_of"] <= end.isoformat()]
    return inside[-1] if inside else None


def _r2(x: float) -> float:
    return round(x + 0.0, 2)


# --- Blankning ≥ 0,5 % -------------------------------------------------------


def _position_records(raw_dir: Path, snaps: list[dict]) -> tuple[list[tuple], str | None]:
    """(innehavare, ISIN, datum, pct) ur historikfilen och ögonblicksbilderna.
    Returnerar också senaste datum som underlaget täcker (för täckningskontroll)."""
    records: list[tuple] = []
    covered: list[str] = []
    hist_files = sorted((raw_dir / "fi-historik").glob("blankning-historik-*.json"))
    if hist_files:
        h = _load(hist_files[-1])
        records += [(r["holder"], r["isin"], r["date"], r["pct"]) for r in h["positions"]]
        # Manuellt nedladdad historik har filens eget datum (as_of); äldre filer hämtningsdagen.
        covered.append(h.get("as_of") or h["fetched_at"][:10])
    prev: dict | None = None
    for s in snaps:
        records += [(r["holder"], r["isin"], r["date"], r["pct"]) for r in s["current"]]
        if prev is not None:
            now_keys = {(r["holder"], r["isin"]) for r in s["current"]}
            for r in prev["current"]:
                if (r["holder"], r["isin"]) not in now_keys:
                    records.append((r["holder"], r["isin"], s["as_of"], None))  # under 0,5 %
        prev = s
        covered.append(s["as_of"])
    return records, (max(covered) if covered else None)


def _latest_at(records: list[tuple], when: date) -> dict[tuple[str, str], tuple[str, float | None]]:
    """Senaste (datum, pct) per (innehavare, ISIN) med datum ≤ when."""
    latest: dict[tuple[str, str], tuple[str, float | None]] = {}
    for holder, isin, d, pct in records:
        if d <= when.isoformat() and ((holder, isin) not in latest or d >= latest[(holder, isin)][0]):
            latest[(holder, isin)] = (d, pct)
    return latest


def _state_at(records: list[tuple], when: date) -> dict[tuple[str, str], float]:
    return {k: v[1] for k, v in _latest_at(records, when).items() if v[1]}


def positions_changes(raw_dir: Path, snaps: list[dict], start: date, end: date,
                      isin_to_ticker: dict[str, str], instruments: dict[str, Instrument]) -> dict:
    prev_end = start - timedelta(days=1)
    records, covered_to = _position_records(raw_dir, snaps)
    if covered_to is None or covered_to < end.isoformat():
        return {"available": False, "note": f"underlaget täcker bara t.o.m. {covered_to or '–'}",
                "start_as_of": prev_end.isoformat(), "end_as_of": end.isoformat(), "changes": []}
    before, after = _state_at(records, prev_end), _state_at(records, end)
    # Positionsdatum = datumet på det värde som gäller vid veckans slut (FI:s
    # "Datum för positionen", eller ögonblicksbilden där positionen försvann).
    end_dates = {k: v[0] for k, v in _latest_at(records, end).items()}
    per: dict[str, dict] = {}
    for key in set(before) | set(after):
        holder, isin = key
        ticker = isin_to_ticker.get(isin)
        if ticker is None:
            continue
        b, a = before.get(key, 0.0), after.get(key, 0.0)
        c = per.setdefault(ticker, {"ticker": ticker, "name": instruments[ticker].namn,
                                    "start_pct": 0.0, "end_pct": 0.0, "holders": []})
        c["start_pct"] += b
        c["end_pct"] += a
        if abs(a - b) > 1e-9:
            c["holders"].append({"holder": holder, "start_pct": _r2(b), "end_pct": _r2(a), "change_pp": _r2(a - b),
                                 "date": end_dates[key]})
    changes = []
    for c in per.values():
        c["start_pct"], c["end_pct"] = _r2(c["start_pct"]), _r2(c["end_pct"])
        c["change_pp"] = _r2(c["end_pct"] - c["start_pct"])
        if c["holders"]:
            c["holders"].sort(key=lambda h: -abs(h["change_pp"]))
            c["last_change_date"] = max(h["date"] for h in c["holders"])
            changes.append(c)
    changes.sort(key=lambda c: (-abs(c["change_pp"]), c["name"]))
    return {"available": True, "note": None, "start_as_of": prev_end.isoformat(), "end_as_of": end.isoformat(),
            "changes": changes}


# --- Blankning aggregat ------------------------------------------------------


def _aggregate_by_ticker(snap: dict, instruments: dict[str, Instrument],
                         isin_to_ticker: dict[str, str]) -> dict[str, float]:
    name_to_isin = {r["issuer"]: r["isin"] for r in snap["current"]}
    by_name = {_norm_name(n): t for t, i in instruments.items() for n in (i.namn, *i.alias)}
    out: dict[str, float] = {}
    for r in snap["aggregate"]:
        ticker = isin_to_ticker.get(name_to_isin.get(r["issuer"], "")) or by_name.get(_norm_name(r["issuer"]))
        if ticker and r["pct"] is not None:
            out[ticker] = out.get(ticker, 0.0) + r["pct"]
    return out


def aggregate_changes(snaps: list[dict], start: date, end: date, instruments: dict[str, Instrument],
                      isin_to_ticker: dict[str, str]) -> dict:
    this = _latest_in(snaps, start, end)
    prev = _latest_in(snaps, start - timedelta(days=7), start - timedelta(days=1))
    if this is None or prev is None:
        missing = "veckans" if this is None else "förra veckans"
        first = snaps[0]["as_of"] if snaps else "–"
        return {"available": False,
                "note": f"egen ögonblicksbild saknas för {missing} slut (första ögonblicksbild: {first})",
                "start_as_of": prev["as_of"] if prev else None, "end_as_of": this["as_of"] if this else None,
                "changes": []}
    b = _aggregate_by_ticker(prev, instruments, isin_to_ticker)
    a = _aggregate_by_ticker(this, instruments, isin_to_ticker)
    changes = []
    for t in set(a) | set(b):
        s, e = _r2(b.get(t, 0.0)), _r2(a.get(t, 0.0))
        if abs(e - s) > 1e-9:
            changes.append({"ticker": t, "name": instruments[t].namn, "start_pct": s, "end_pct": e,
                            "change_pp": _r2(e - s)})
    changes.sort(key=lambda c: (-abs(c["change_pp"]), c["name"]))
    return {"available": True, "note": None, "start_as_of": prev["as_of"], "end_as_of": this["as_of"],
            "changes": changes}


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
    snaps = _snapshots(raw_dir)
    return {
        "schema_version": 1,
        "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": now.isoformat(timespec="seconds"),
        "top_n": TOP_N,
        "short_positions": positions_changes(raw_dir, snaps, start, end, isin_to_ticker, instruments),
        "short_aggregate": aggregate_changes(snaps, start, end, instruments, isin_to_ticker),
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
    sp, ag, ins = analysis["short_positions"], analysis["short_aggregate"], analysis["insider"]
    print(f"Skrev {out}")
    print(f"  blankning ≥ 0,5 %: {len(sp['changes'])} bolag med förändring" if sp["available"] else f"  blankning ≥ 0,5 %: {sp['note']}")
    print(f"  blankning aggregat: {len(ag['changes'])} bolag med förändring" if ag["available"] else f"  blankning aggregat: {ag['note']}")
    print(f"  insyn: {len(ins['companies'])} bolag, dagar som saknas: {ins['days_missing'] or 'inga'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
