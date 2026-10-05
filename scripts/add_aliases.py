"""Lägger till alias i data/instruments.csv genom att ta bort bolagsändelser från namnet.

"Avanza Bank Holding" ger aliasen "Avanza Bank" och "Avanza". Ändelser tas
bort stegvis från slutet (se SUFFIXES). Ett alias läggs bara till om det är
unikt i hela listan – det får inte vara namn/alias på en annan rad och inte
heller genereras från en annan rad –, har minst MIN_LENGTH tecken och inte
är ett vanligt ord (STOPWORDS).
Krockar och för korta alias skrivs ut så att de kan avgöras för hand.

Skriptet är idempotent: körs det igen läggs inget till som redan finns.

Användning:
    python scripts/add_aliases.py            # skriver data/instruments.csv
    python scripts/add_aliases.py --dry-run  # visar bara vad som skulle hända
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "instruments.csv"

# Jämförs gemener, ord för ord från slutet.
SUFFIXES = {
    "holding", "hold.", "group", "ab", "(publ)", "publ", "bank",
    "ltd", "ltd.", "plc", "oyj", "abp", "corp", "corp.", "corporation", "inc", "inc.",
}
MIN_LENGTH = 3

# Vanliga ord som aldrig blir alias (de ger falska träffar i löptext), t.ex.
# "Momentum Group" -> inte "Momentum". Jämförs gemener mot hela aliaset.
STOPWORDS = {
    # engelska
    "momentum", "humble", "green", "global", "nordic", "international", "capital", "invest", "investment",
    "property", "properties", "energy", "medical", "health", "care", "tech", "technology", "solutions",
    "systems", "services", "partners", "industries", "smart", "prime", "core", "select", "link", "nova",
    # svenska
    "fastigheter", "industri", "bygg", "hälsa", "vård", "energi", "kapital", "sverige", "norden",
}


def stripped_variants(name: str) -> list[str]:
    """Namnet med en, två, … ändelser borttagna (det ursprungliga namnet ingår inte)."""
    words = name.split()
    out = []
    while len(words) > 1 and words[-1].casefold() in SUFFIXES:
        words = words[:-1]
        out.append(" ".join(words))
    return out


@dataclass
class AliasResult:
    added: list[tuple[str, str]] = field(default_factory=list)  # (ticker, alias)
    collisions: dict[str, list[str]] = field(default_factory=dict)  # alias -> tickers
    too_short: list[tuple[str, str]] = field(default_factory=list)  # (ticker, alias)
    stopwords: list[tuple[str, str]] = field(default_factory=list)  # (ticker, alias)


def add_aliases(rows: list[dict]) -> AliasResult:
    """Lägger till unika alias i rows (ändras på plats) och returnerar vad som hände."""
    res = AliasResult()

    def names(row: dict) -> list[str]:
        return [row["namn"], *[a for a in (row.get("alias") or "").split("|") if a]]

    existing: dict[str, set[str]] = {}  # gemener -> tickers som redan har namnet/aliaset
    for row in rows:
        for n in names(row):
            existing.setdefault(n.casefold(), set()).add(row["ticker"])

    candidates: dict[str, set[str]] = {}  # gemener -> tickers som skulle få aliaset
    display: dict[tuple[str, str], str] = {}
    for row in rows:
        for v in stripped_variants(row["namn"]):
            key = v.casefold()
            if row["ticker"] in existing.get(key, set()):
                continue  # finns redan på raden
            candidates.setdefault(key, set()).add(row["ticker"])
            display[(row["ticker"], key)] = v

    for row in rows:
        new = []
        for (ticker, key), alias in display.items():
            if ticker != row["ticker"]:
                continue
            owners = candidates[key] | existing.get(key, set())
            if owners != {ticker}:
                res.collisions[alias] = sorted(owners)
            elif len(alias) < MIN_LENGTH:
                res.too_short.append((ticker, alias))
            elif key in STOPWORDS:
                res.stopwords.append((ticker, alias))
            else:
                new.append(alias)
                res.added.append((ticker, alias))
        if new:
            row["alias"] = "|".join([a for a in (row.get("alias") or "").split("|") if a] + new)
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    res = add_aliases(rows)

    print(f"Nya alias: {len(res.added)}")
    for ticker, alias in res.added:
        print(f"  {ticker:12} + {alias}")
    print(f"Krockar (inte tillagda, avgör för hand): {len(res.collisions)}")
    for alias, tickers in sorted(res.collisions.items()):
        print(f"  {alias!r}: {', '.join(tickers)}")
    print(f"För korta (< {MIN_LENGTH} tecken, inte tillagda): {len(res.too_short)}")
    for ticker, alias in res.too_short:
        print(f"  {ticker:12} {alias!r}")
    print(f"Vanliga ord (stopplistan, inte tillagda): {len(res.stopwords)}")
    for ticker, alias in res.stopwords:
        print(f"  {ticker:12} {alias!r}")

    if not args.dry_run and res.added:
        with args.csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"Skrev {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
