"""Eftermiddagsuppdateringen: dagens kursreaktion för bolagen som rapporterat, utan Claude.

Skriver data/afternoon/<datum>.json (schema/afternoon.schema.json). Körs av
scripts/run_update.ps1 (vardagar 17:45 och skrivbordsgenvägen "Uppdatera nu") efter att
rapportutfallen för dagens uppdateringsfönster tagits fram.

- Bolagen: rapporterna i dagens körningsfönster (morgonen och dagens uppdateringar, se
  scripts/report_list.py) som publicerats från förra handelsdagens stängning (17:30) och framåt
  – deras första handelsdag efter rapporten är i dag. En rapport efter dagens stängning märks
  "after_close" och får ingen kursreaktion förrän nästa handelsdag.
- Kursen: ett batchanrop till Yahoo (scripts/signals.yahoo_bars) för bolagen och index. Dagens
  förändring är senaste kursen i dag mot förra handelsdagens stängning (justerad stängning, som
  i veckans kursfil); före stängning är det kursen vid körningen. Mot index i procentenheter,
  bara för aktier handlade i kronor (.ST), som i morgonrapporten.

Användning:
    python scripts/afternoon.py                  # i dag
    python scripts/afternoon.py --handelsdag     # bara kontrollen: exit 0 = handelsdag, 3 = inte
    python scripts/afternoon.py --date 2026-10-22  # en tidigare dag (stängningskurser)
    python scripts/afternoon.py --forra-om-saknas  # förra handelsdagen om dess fil saknas eller är ofullständig
    python scripts/afternoon.py --klar           # bara kontrollen: exit 0 = dagens fil är klar, 4 = inte
Saknas kurser (Yahoo eller nätverket) räknas det som fel: filen skrivs med "retry" och felen, exit 2,
och nästa inloggning eller upplåsning (eller morgonflödet) försöker igen. Klar = filen finns, "retry"
är tom och den togs fram efter stängning.
Exit-kod: 0 = filen skriven (eller ingen handelsdag), 1 = valideringen underkände, 2 = kurser saknas,
3 och 4 = se ovan.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from morning import is_business_day, previous_business_day  # noqa: E402
from prices import INDEX_NAME, INDEX_TICKER  # noqa: E402
from report_list import RESULTS_DIR, list_scope, runs_on  # noqa: E402
from signals import BarFetcher, week_change, yahoo_bars  # noqa: E402
from validate import DATA_DIR, Instrument, load_instruments, load_sector_kpis, validate_afternoon  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
AFTERNOON_DIR = DATA_DIR / "afternoon"
MARKET_CLOSE = time(17, 30)
HISTORY_DAYS = 10  # räcker för förra handelsdagens stängning även efter en långhelg


def reporters(today: date, instruments: dict[str, Instrument], results_dir: Path = RESULTS_DIR,
              list_fn=list_scope) -> tuple[list[str], list[dict]]:
    """Dagens körningar och deras rapporter med reaktion i dag (eller efter dagens stängning)."""
    scopes = runs_on(today, results_dir)
    since = datetime.combine(previous_business_day(today), MARKET_CLOSE, STOCKHOLM)
    close = datetime.combine(today, MARKET_CLOSE, STOCKHOLM)
    sector_kpis = load_sector_kpis()
    out, seen = [], set()
    for scope in scopes:
        for r in list_fn(scope, instruments, sector_kpis, results_dir=results_dir):
            published = datetime.fromisoformat(r["published"])
            if published < since or r["ticker"] in seen:
                continue
            seen.add(r["ticker"])
            out.append({"ticker": r["ticker"], "name": r["name"], "scope": scope, "published": r["published"],
                        "title": r["title"], "url": r["url"], "after_close": published >= close,
                        # Länkar ur instruments.csv; ingenting hämtas från sajterna.
                        "links": instruments[r["ticker"]].analyst_links()})
    return scopes, out


def build(today: date, now: datetime, instruments: dict[str, Instrument], fetch: BarFetcher,
          results_dir: Path = RESULTS_DIR, list_fn=list_scope) -> dict:
    scopes, companies = reporters(today, instruments, results_dir, list_fn)
    errors: list[str] = []
    yahoo = {c["ticker"]: instruments[c["ticker"]].yahoo_ticker for c in companies}
    retry: list[str] = []
    bars = {}
    if companies:
        try:
            bars = fetch([*yahoo.values(), INDEX_TICKER], today - timedelta(days=HISTORY_DAYS), today)
        except Exception as e:  # noqa: BLE001 – ett fel i kurshämtningen ska inte stoppa sidan
            errors.append(f"kursdata: {e}")
            retry.append("nätverk")
    has_today = any(b.day == today for bs in bars.values() for b in bs)
    index_change = week_change(bars.get(INDEX_TICKER, []), today, today) if has_today else None
    for c in companies:
        b = [x for x in bars.get(yahoo[c["ticker"]], []) if x.day <= today]
        change = week_change(b, today, today) if has_today and not c["after_close"] else None
        c["close"] = round(b[-1].close, 4) if change is not None else None
        c["change_pct"] = change
        c["relative_pp"] = (round(change - index_change, 2) if change is not None and index_change is not None
                            and yahoo[c["ticker"]].endswith(".ST") else None)
    companies.sort(key=lambda c: (c["after_close"], c["published"], c["name"]))
    # Kurser som saknas är ett fel: filen skrivs ändå, men nästa inloggning/upplåsning (eller
    # morgonflödet) försöker igen.
    needs = [c for c in companies if not c["after_close"]]
    missing = [c["name"] for c in needs if c["change_pct"] is None]
    if needs and index_change is None:
        missing.append(INDEX_NAME)
    if missing:
        errors.append(f"kurser saknas för {today}: {', '.join(missing)}")
        retry.append("kurser")
    return {
        "date": today.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "scopes": scopes,
        "price_day": today.isoformat() if has_today else None,
        "index": {"ticker": INDEX_TICKER, "name": INDEX_NAME, "change_pct": index_change},
        "companies": companies,
        "errors": errors,
        "retry": sorted(set(retry)),
    }


def is_complete(day: date, path: Path | None = None) -> bool:
    """Dagens fil finns, saknar inga kurser (retry tom) och togs fram efter stängning (eller en senare
    dag), så att ingen ny körning behövs."""
    path = path or AFTERNOON_DIR / f"{day}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(data["generated_at"])
    except (ValueError, KeyError, TypeError):
        return False
    after_close = generated >= datetime.combine(day, MARKET_CLOSE, STOCKHOLM)
    return not data.get("retry") and after_close


def main(argv: list[str] | None = None, fetch: BarFetcher = yahoo_bars, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--handelsdag", action="store_true", help="kontrollera bara om i dag är en handelsdag")
    p.add_argument("--date", type=date.fromisoformat, help="en tidigare handelsdag (stängningskurser)")
    p.add_argument("--forra-om-saknas", action="store_true",
                   help="förra handelsdagen, bara om dess fil saknas eller är ofullständig (morgonflödet)")
    p.add_argument("--klar", action="store_true",
                   help="bara kontrollen: exit 0 = dagens (med --forra-om-saknas förra dagens) fil är klar, 4 = inte")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    today = args.date or now.date()
    if args.forra_om_saknas:
        today = previous_business_day(now.date())
    if args.klar:
        return 0 if is_complete(today) else 4
    if args.forra_om_saknas and is_complete(today):
        print(f"Kursreaktion {today} finns redan.")
        return 0
    if args.handelsdag:
        return 0 if is_business_day(today) else 3
    if not is_business_day(today):
        print(f"{today} är ingen handelsdag – ingen eftermiddagsuppdatering.")
        return 0
    instruments = load_instruments()
    data = build(today, now, instruments, fetch)
    # Ett bolag som inte klarar valideringen hoppas över ("skipped", med orsaken) i stället för att stoppa filen.
    from validate import prune_invalid

    res, skipped = prune_invalid(data, lambda d: validate_afternoon(d, today.isoformat(), instruments),
                                 lambda d: [d["companies"]])
    for s in skipped:
        print(f"Eftermiddag: {s['name']} hoppas över (klarar inte valideringen): {s['reason']}", file=sys.stderr)
    if res.errors:
        print("Eftermiddagsuppdateringen klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ",
              file=sys.stderr)
        return 1
    out = AFTERNOON_DIR / f"{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    with_price = sum(c["change_pct"] is not None for c in data["companies"])
    print(f"Eftermiddag {today}: {len(data['companies'])} rapporterande bolag, {with_price} med kursreaktion, "
          f"körningar {', '.join(data['scopes']) or 'inga'}, {len(data['errors'])} fel -> {out}")
    if data["retry"]:
        for e in data["errors"]:
            print(f"FEL: {e}")
        print(f"Ofullständig ({', '.join(data['retry'])}) – nästa inloggning eller upplåsning försöker igen.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
