"""Lägger in ett pressmeddelande från ett bevakat bolags egen webbplats i rådatan, för bolag som inte publicerar
(allt) via MFN eller Cision (t.ex. ABB: abb.com/global/en/news).

Posten får source "bolag", källans namn (t.ex. "ABB"), taggen "manuellt tillagd" och data["ticker"] (tickern i
instruments.csv). Två lägen, beroende på källans villkor:

- **Egen sammanfattning** (standard, och det enda tillåtna när villkoren inte tillåter återgivning, som ABB:s
  "Terms of Use" 2026-11-05): bara rubrik, tid, länk och en kort sammanfattning med egna ord (--summary).
  Ingen text ur pressmeddelandet sparas. text_kind "sammanfattning", data["summary"].
- **Ordagrann text** (--text-file, --quote och --atergivning-tillaten): bara när källans villkor uttryckligen
  tillåter återgivning. Texten sparas ordagrant (text_kind "fulltext") och citatet, som kontrolleras mot texten,
  läggs i data["quote"].

Morgonrapporten visar sammanfattningen märkt "manuellt tillagd" (eller citatet) under rubriken.

Skrivs till data/morning/raw/<vecka>/ (morgonrapporten) och till data/raw/<vecka>/ (veckosammanfattningen), som
committas och pushas till det privata repot. Använd bara för pressmeddelanden du läst på bolagets egen sida.

Användning:
    python scripts/add_raw_item.py --ticker ABB --source-name ABB --url <adress> \\
        --published 2026-11-05T07:25:00+01:00 --title "<rubrik>" --summary "<egen sammanfattning>"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from backup_week import git  # noqa: E402
from fetch import STOCKHOLM, Item, RunResult, iso_week, store  # noqa: E402
from validate import load_instruments  # noqa: E402

MORNING_RAW = ROOT / "data" / "morning" / "raw"
RAW_DIR = ROOT / "data" / "raw"
MANUAL_TAG = "manuellt tillagd"
SUMMARY_MAX_WORDS = 60


def make_item(ticker: str, source_name: str, url: str, published: datetime, title: str, *,
              summary: str | None = None, text: str | None = None, quote: str | None = None,
              reproduction_allowed: bool = False, language: str = "en") -> Item:
    """En post från bolagets webbplats: egen sammanfattning (summary) eller, bara om källans villkor tillåter
    återgivning (reproduction_allowed), ordagrann text med ett citat ur den."""
    if (summary is None) == (text is None):
        raise ValueError("ange antingen en egen sammanfattning eller (om villkoren tillåter) den ordagranna texten")
    data: dict = {"ticker": ticker}
    if summary is not None:
        summary = " ".join(summary.split())
        if not summary:
            raise ValueError("sammanfattningen är tom")
        if len(summary.split()) > SUMMARY_MAX_WORDS:
            raise ValueError(f"sammanfattningen är längre än {SUMMARY_MAX_WORDS} ord")
        if quote is not None:
            raise ValueError("ett citat kräver den ordagranna texten")
        body, kind = summary, "sammanfattning"
        data["summary"] = summary
    else:
        if not reproduction_allowed:
            raise ValueError("ordagrann text bara om källans villkor tillåter återgivning – ange annars en egen sammanfattning")
        if not quote or " ".join(quote.split()) not in " ".join(text.split()):
            raise ValueError("citatet finns inte ordagrant i texten")
        body, kind = text.strip(), "fulltext"
        data["quote"] = quote
    slug = ticker.lower().replace(" ", "-")
    return Item(
        id=f"bolag-{slug}-{published.astimezone(STOCKHOLM):%Y%m%d%H%M}",
        source="bolag",
        source_name=source_name,
        url=url,
        published=published,
        title=title,
        company=slug,
        language=language if kind == "fulltext" else "sv",
        tags=["bolagets webbplats", MANUAL_TAG],
        text=body,
        text_kind=kind,
        data=data,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--ticker", required=True)
    p.add_argument("--source-name", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--published", required=True, type=datetime.fromisoformat)
    p.add_argument("--title", required=True)
    p.add_argument("--summary", help=f"kort sammanfattning med egna ord (högst {SUMMARY_MAX_WORDS} ord)")
    p.add_argument("--text-file", type=Path, help="ordagrann text – bara med --atergivning-tillaten")
    p.add_argument("--quote", help="ordagrant citat ur texten – bara med --text-file")
    p.add_argument("--atergivning-tillaten", action="store_true",
                   help="källans villkor tillåter uttryckligen återgivning av pressmeddelandet")
    p.add_argument("--language", default="en", help="den ordagranna textens språk")
    p.add_argument("--no-push", action="store_true", help="skriv bara till data/morning/raw/ (för test)")
    args = p.parse_args(argv)
    if args.ticker not in load_instruments():
        print(f"{args.ticker} finns inte i instruments.csv", file=sys.stderr)
        return 1
    if not args.url.startswith("https://"):
        print("adressen ska börja med https://", file=sys.stderr)
        return 1
    try:
        item = make_item(args.ticker, args.source_name, args.url, args.published, args.title,
                         summary=args.summary,
                         text=args.text_file.read_text(encoding="utf-8") if args.text_file else None,
                         quote=args.quote, reproduction_allowed=args.atergivning_tillaten, language=args.language)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    now = datetime.now(STOCKHOLM)
    result = RunResult(items=[item], log={"bolag": {"status": "ok", "items": 1}})
    print(f"data/morning/raw: {store(result, MORNING_RAW, now)}")
    if args.no_push:
        return 0
    # data/raw/ är det privata repots klon: hämta först, skriv, och committa bara veckans index och textfilen.
    if git(RAW_DIR, "status", "--porcelain").stdout.strip():
        print("data/raw/ har oincheckade ändringar – skrivs inte dit.", file=sys.stderr)
        return 1
    if git(RAW_DIR, "pull", "--rebase").returncode != 0:
        print("git pull --rebase misslyckades i data/raw/", file=sys.stderr)
        return 1
    print(f"data/raw: {store(result, RAW_DIR, now)}")
    week = iso_week(item.published)
    git(RAW_DIR, "add", "--", f"{week}/index.json", f"{week}/bolag")
    if git(RAW_DIR, "commit", "-q", "-m", f"Pressmeddelande från bolagets webbplats: {item.source_name}, {item.title}").returncode != 0:
        print("inget nytt att committa i data/raw/")
        return 0
    push = git(RAW_DIR, "push", "-q")
    print("Pushad till det privata repot." if push.returncode == 0 else f"push misslyckades: {push.stderr.strip()}")
    return 0 if push.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
