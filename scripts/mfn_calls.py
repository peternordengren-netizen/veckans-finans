"""Antal anrop till MFN:s RSS per dygn, ur körningsloggarna i rådatans index ("runs" i <vecka>/index.json).

Varje körning av scripts/fetch.py loggar sina källor; en körning med "mfn" bland källorna är ett anrop.
- Actions (GitHub, var fjärde timme sedan 2026-11-09): data/raw/<vecka>/index.json
- lokalt (morgon- och eftermiddagskörningen, omförsök): data/morning/raw/<vecka>/index.json
Dygnet räknas i svensk tid.

Användning:
    python scripts/mfn_calls.py                 # de senaste 7 dygnen
    python scripts/mfn_calls.py --dag 2026-11-09
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STOCKHOLM = ZoneInfo("Europe/Stockholm")
DIRS = {"Actions": ROOT / "data" / "raw", "lokalt": ROOT / "data" / "morning" / "raw"}


def count(dirs: dict[str, Path] = DIRS) -> dict[date, Counter]:
    """dag -> {"Actions": n, "lokalt": n}"""
    out: dict[date, Counter] = {}
    for label, base in dirs.items():
        for p in base.glob("*-W*/index.json"):
            try:
                runs = json.loads(p.read_text(encoding="utf-8")).get("runs", [])
            except (OSError, ValueError):
                continue
            for r in runs:
                if "mfn" in (r.get("sources") or {}):
                    day = datetime.fromisoformat(r["time"]).astimezone(STOCKHOLM).date()
                    out.setdefault(day, Counter())[label] += 1
    return out


def line(day: date, c: Counter) -> str:
    return f"MFN-anrop {day}: {sum(c.values())} (Actions {c['Actions']}, lokalt {c['lokalt']})"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--dag", type=date.fromisoformat)
    args = p.parse_args(argv)
    counts = count()
    days = [args.dag] if args.dag else [date.today() - timedelta(days=i) for i in range(6, -1, -1)]
    for d in days:
        print(line(d, counts.get(d, Counter())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
