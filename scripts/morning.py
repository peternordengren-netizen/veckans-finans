"""Morgonrapport för en handelsdag, utan Claude: dagens rapporter, nattens pressmeddelanden,
insyn sedan förra handelsdagen och gårdagens kurssignaler. Blankning ingår inte sedan 2026-11-11 (FI tillåter
inte automatisk hämtning); äldre rapporter har kvar fältet "short".

Skriver data/morning/<datum>.json (schema/morning.schema.json). Körs vardagar 08:10 av
scripts/run_morning.ps1, så att rapporter som släpps 07:00–08:00 kommer med före börsöppning.

Källor och hämtning (en hämtning per källa och körning):
- MFN/Cision/Riksbanken (fetch.py) och FI (fi_fetch.py) hämtas lokalt till
  data/morning/raw/ (MFN med 500 poster i stället för 200, så att även en tung rapportdag
  täcks tillbaka till förra morgonrapporten), aldrig till data/raw/ (klonen av det privata repot, som bara Actions
  och säkerhetskopian skriver i). GitHub Actions schemalagda jobb kan komma timmar för sent
  – FI-jobbet "06:37" har committats 10:45–11:15 – så rapporten kan inte vänta på dem.
- data/raw/ (Actions) och data/morning/raw/ läses båda; samma post räknas en gång.
- Rapportkalendern för innevarande vecka skrivs om (scripts/report_calendar.py), så att
  veckosidans "Kommande vecka" också hålls aktuell.
- Kursdata: ett batchanrop till Yahoo (scripts/signals.py) för gårdagens handelsdag. Samma dagsdata
  används för fliken Swing (scripts/swing.py -> data/swing/<datum>.json), utan extra hämtning.
  Datumet är dagsdatans eget (signals.data_day: den senaste dagen som minst 90 % av bolagen har en kursrad
  för), inte det senaste datumet hos något bolag. Är det inte förra handelsdagen blir rapporten "retry"
  ("kurser") med ett fel, och run_morning.ps1 hämtar kurserna igen var tionde minut till 09:10 (--bara-kurser).

Innehåll:
- reports_today: kalenderns rapporter och stämmor i dag
- news: icke-rutinmässiga pressmeddelanden för bolag i listan sedan förra handelsdagen 17:30
- insider: insyn (samma filter som registers.py) publicerad sedan förra rapporten
- price: bolag med hög omsättning eller nära 52-veckorshögsta/-lägsta under gårdagens
  handelsdag (trösklar som signals.THRESHOLDS, men för en enda dag)

Användning:
    python scripts/morning.py                 # i dag
    python scripts/morning.py --date 2026-10-20 --no-fetch
Exit-kod: 0 = filen skriven eller ingen handelsdag, 1 = fel.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import report_calendar  # noqa: E402
from registers import insyn_summary  # noqa: E402
from signals import THRESHOLDS, BarFetcher, data_day, price_stats, week_change, yahoo_bars  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from validate import DATA_DIR, RAW_DIR, Instrument, listed, load_instruments, validate_morning  # noqa: E402
from workdays import non_business_days  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
MORNING_DIR = DATA_DIR / "morning"
MORNING_RAW = MORNING_DIR / "raw"
MORNING_MFN_LIMIT = 500
NEWS_FROM = time(17, 30)  # nattens nyheter räknas från förra handelsdagens stängning
KEEP_RAW_WEEKS = 3  # data/morning/raw/ rensas från äldre veckor


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in non_business_days(d.year)


def previous_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _dedupe_url(url: str) -> str:
    return url.split("?")[0].rstrip("/")


# --- Nyheter -----------------------------------------------------------------


def overnight_news(raw_dirs: list[Path], instruments: dict[str, Instrument], since: datetime, until: datetime,
                   reporting_today: set[str]) -> list[dict]:
    """Pressmeddelanden för bolag i listan publicerade i [since, until], per bolag. Poster från bolagets egen
    webbplats (source "bolag", scripts/add_raw_item.py) har tickern i data och antingen en egen sammanfattning
    (visas märkt "manuellt tillagd") eller, när källans villkor tillåter återgivning, ett ordagrant citat."""
    patterns = report_calendar.name_patterns(instruments)
    weeks = {_iso_week(since.date()), _iso_week(until.date())}
    seen: set[str] = set()
    per: dict[str, dict] = {}
    for raw in raw_dirs:
        for wk in sorted(weeks):
            index_path = raw / wk / "index.json"
            if not index_path.exists():
                continue
            for it in json.loads(index_path.read_text(encoding="utf-8")).get("items", []):
                if it.get("source") not in ("mfn", "cision", "bolag") or it.get("routine"):
                    continue
                published = datetime.fromisoformat(it["published"])
                if not since <= published <= until:
                    continue
                key = _dedupe_url(it["url"])
                also = {_dedupe_url(a["url"]) for a in it.get("also_in", [])}
                if key in seen or also & seen:
                    continue
                data = it.get("data") or {}
                if it["source"] == "bolag":
                    ticker = data.get("ticker") if data.get("ticker") in instruments else None
                else:
                    ticker = report_calendar.match_company(it, instruments, patterns)
                if ticker is None:
                    continue
                seen.add(key)
                seen.update(also)
                c = per.setdefault(ticker, {"ticker": ticker, "name": instruments[ticker].namn,
                                            "report_today": ticker in reporting_today, "items": []})
                entry = {"published": published.isoformat(), "title": it["title"], "url": it["url"],
                         "source": it.get("source_name") or it["source"]}
                if it["source"] == "bolag":
                    entry["manual"] = True
                    if data.get("summary"):
                        entry["summary"] = data["summary"]
                    elif data.get("quote"):
                        entry["quote"] = data["quote"]
                c["items"].append(entry)
    out = list(per.values())
    for c in out:
        c["items"].sort(key=lambda i: i["published"])
    # Bolag med rapport i dag först, sedan efter första nyhetens tid.
    out.sort(key=lambda c: (not c["report_today"], c["items"][0]["published"], c["name"]))
    return out


def latest_policy_decision(raw_dirs: list[Path], today: date) -> dict | None:
    """Riksbankens senaste räntebeslut i rådatan (scripts/fetch.py, data.policy_decision), fattat senast i dag.
    Sidan visar det bredvid gällande ränta så länge det inte börjat gälla."""
    best = None
    for raw in raw_dirs:
        for p in sorted(raw.glob("*-W*/index.json"))[-8:]:
            try:
                items = json.loads(p.read_text(encoding="utf-8")).get("items", [])
            except (OSError, ValueError):
                continue
            for it in items:
                d = (it.get("data") or {}).get("policy_decision")
                if d and d["decided_on"] <= today.isoformat() and (best is None or d["decided_on"] > best["decided_on"]):
                    best = d
    return best


# --- FI ----------------------------------------------------------------------


def _fi_files(raw_dirs: list[Path], pattern: str) -> dict[str, Path]:
    """FI-filer per datum ur alla rådatamappar (senast hämtad vinner inte – alla har samma as_of-innehåll)."""
    files: dict[str, Path] = {}
    for raw in raw_dirs:
        for p in raw.glob(f"*/fi/{pattern}-*.json"):
            files.setdefault(p.stem.removeprefix(f"{pattern}-"), p)
    return files


def insider_since(raw_dirs: list[Path], days: list[str], instruments: dict[str, Instrument]) -> dict:
    """Insyn publicerad de angivna dagarna, med samma filter som veckoanalysen."""
    paths = _fi_files(raw_dirs, "insyn")
    files = {d: json.loads(paths[d].read_text(encoding="utf-8")) for d in days if d in paths}
    isin_to_ticker = {isin: t for t, i in instruments.items() for isin in i.isin}
    s = insyn_summary(RAW_DIR, "", date.min, date.min, isin_to_ticker, instruments, days=days, files=files)
    return {"threshold_sek": s["threshold_sek"], "days": days, "days_missing": s["days_missing"],
            "companies": s["companies"]}


# --- Kurser ------------------------------------------------------------------


def price_signals(c: dict, thresholds: dict) -> list[str]:
    """Kurssignaler för en dag (används även av valideringen)."""
    out = []
    if c["volume_ratio"] is not None and c["volume_ratio"] >= thresholds["volume_ratio"]:
        out.append("omsättning")
    if c["from_high_pct"] is not None and c["from_high_pct"] >= -thresholds["near_52w_pct"]:
        out.append("52v-högsta")
    if c["from_low_pct"] is not None and c["from_low_pct"] <= thresholds["near_52w_pct"]:
        out.append("52v-lägsta")
    return out


def yesterday_prices(instruments: dict[str, Instrument], today: date, fetch: BarFetcher) -> dict:
    """Bolag med kurssignal den senaste handelsdagen som dagsdatan faktiskt täcker (signals.data_day: minst 90 %
    av bolagen har en kursrad den dagen). "day" är alltså datans datum, aldrig ett antaget; "expected_day" är
    förra handelsdagen och "coverage" hur många bolag som har en rad för day respektive expected_day."""
    expected = previous_business_day(today)
    instruments = listed(instruments, expected)  # avnoterade bolag hoppas över (instruments.csv)
    tickers = [i.yahoo_ticker for i in instruments.values()]
    bars = fetch([*tickers, INDEX_TICKER], today - timedelta(days=400), today - timedelta(days=1))
    day, with_day, of = data_day(bars, tickers, today)
    with_expected = sum(1 for t in tickers if any(b.day == expected for b in bars.get(t, [])))
    coverage = {"with_day": with_day, "with_expected": with_expected, "of": of}
    if day is None:
        return {"day": None, "expected_day": expected.isoformat(), "coverage": coverage,
                "index_change_pct": None, "thresholds": _price_thresholds(), "companies": []}
    index_change = week_change(bars.get(INDEX_TICKER, []), day, day)
    companies = []
    for inst in instruments.values():
        stats = price_stats(bars.get(inst.yahoo_ticker, []), day, day)
        if not stats:
            continue
        change = stats["change_pct"]
        c = {
            "ticker": inst.ticker, "name": inst.namn, "change_pct": change,
            "relative_pp": round(change - index_change, 2)
            if change is not None and index_change is not None and inst.yahoo_ticker.endswith(".ST") else None,
            "volume_ratio": stats["volume_ratio"], "from_high_pct": stats["from_high_pct"],
            "from_low_pct": stats["from_low_pct"],
        }
        c["signals"] = price_signals(c, THRESHOLDS)
        if c["signals"]:
            companies.append(c)
    companies.sort(key=lambda c: (-(c["volume_ratio"] or 0), c["name"]))
    return {"day": day.isoformat(), "expected_day": expected.isoformat(), "coverage": coverage,
            "index_change_pct": index_change, "thresholds": _price_thresholds(), "companies": companies}


def _price_thresholds() -> dict:
    return {"volume_ratio": THRESHOLDS["volume_ratio"], "near_52w_pct": THRESHOLDS["near_52w_pct"]}


# --- Sammanställning ---------------------------------------------------------


def build(today: date, now: datetime, instruments: dict[str, Instrument], raw_dirs: list[Path],
          calendar: dict | None, fetch: BarFetcher) -> dict:
    prev = previous_business_day(today)
    since = datetime.combine(prev, NEWS_FROM, STOCKHOLM)
    reports_today = [
        {k: e[k] for k in ("ticker", "name", "kind", "status", "yahoo_date", "mfn_date", "mfn_url")}
        for e in (calendar or {}).get("events", []) if e["date"] == today.isoformat()
    ]
    reporting = {e["ticker"] for e in reports_today if e["kind"] == "rapport"}
    # Insyn: publiceringsdagar sedan förra rapporten (förra handelsdagen t.o.m. i går).
    days = [(prev + timedelta(days=i)).isoformat() for i in range((today - prev).days)]
    return {
        "schema_version": 1,
        "date": today.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "news_since": since.isoformat(),
        "reports_today": reports_today,
        "news": overnight_news(raw_dirs, instruments, since, now, reporting),
        "insider": insider_since(raw_dirs, days, instruments),
        "price": yesterday_prices(instruments, today, fetch),
        "policy_decision": latest_policy_decision(raw_dirs, today),
    }


def _prune_morning_raw(today: date, morning_raw: Path | None = None, complete=None) -> list[str]:
    """Rensar data/morning/raw/ från veckor äldre än KEEP_RAW_WEEKS. Först kopieras veckans poster som bara finns
    lokalt till rådatarepot (merge_morning_raw.complete); misslyckas det står veckan kvar och felet returneras.
    (Vecka 43 rensades 2026-11-09 med tre rapporttexter som bara fanns här.)"""
    morning_raw = morning_raw or MORNING_RAW
    if complete is None:
        from merge_morning_raw import complete
    keep = {_iso_week(today - timedelta(weeks=i)) for i in range(KEEP_RAW_WEEKS)}
    errors = []
    for p in sorted(morning_raw.glob("*-W*")):
        if p.is_dir() and re.fullmatch(r"\d{4}-W\d{2}", p.name) and p.name not in keep:
            ok, msg, _ = complete(p.name, morning_raw=morning_raw)
            print(f"[rensning] {msg}")
            if not ok:
                errors.append(f"rensning: {p.name} står kvar i data/morning/raw, kopian till rådatarepot misslyckades: {msg}")
                continue
            shutil.rmtree(p)
    return errors


FETCH_FAILED = "fetch.py: alla källor fallerade"


def fetch_local(today: date) -> list[str]:
    """Hämtar MFN/Cision/Riksbanken och FI till data/morning/raw/. Returnerar fel som text."""
    import fetch
    import fi_fetch

    errors = []
    MORNING_RAW.mkdir(parents=True, exist_ok=True)
    # 500 poster från MFN (ett anrop), så att hämtningen når tillbaka till förra morgonrapporten
    # även på tunga rapportdagar; standardens 200 räcker där bara några timmar.
    if fetch.main(["--raw-dir", str(MORNING_RAW), "--mfn-limit", str(MORNING_MFN_LIMIT)]) != 0:
        errors.append(FETCH_FAILED)
    if fi_fetch.main(["--raw-dir", str(MORNING_RAW)]) != 0:
        # Varje del som misslyckades räknas, också när en annan gick bra (blankningen gav 404 från
        # 29/10 2026 medan insynen fungerade, och morgonrapporten visade då "0 positioner").
        errors += [f"FI {name}: {msg}" for name, msg in fi_fetch.last_errors.items()] or \
            ["fi_fetch.py: fel vid hämtning från FI"]
    errors += _prune_morning_raw(today)
    return errors


def main(argv: list[str] | None = None, fetch_bars: BarFetcher = yahoo_bars,
         get_dates: Callable[[str], list[date]] = report_calendar.yahoo_report_dates,
         now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=date.fromisoformat, help="rapportens datum (standard: i dag)")
    p.add_argument("--no-fetch", action="store_true", help="hämta inget lokalt, använd befintlig rådata")
    p.add_argument("--bara-kurser", action="store_true",
                   help="hämta bara dagsdatan igen och skriv om kursdelen och Swing (exit 2 = gårdagens kurser saknas)")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    today = args.date or now.date()
    if not is_business_day(today):
        print(f"{today} är ingen handelsdag – ingen morgonrapport.")
        return 0
    if args.bara_kurser:
        return refresh_prices(today, now, fetch_bars)

    errors = [] if args.no_fetch else fetch_local(today)
    instruments = load_instruments()
    raw_dirs = [RAW_DIR, MORNING_RAW]

    # Rapportkalendern för veckan skrivs om, med både Actions- och den lokala rådatan.
    week = _iso_week(today)
    calendar = report_calendar.build(week, RAW_DIR, instruments, today, now, get_dates)
    cal_res = report_calendar.validate_calendar(calendar, week, instruments, RAW_DIR)
    if cal_res.errors:
        errors += [f"kalender: {e}" for e in cal_res.errors]
        calendar = None
    else:
        out_cal = DATA_DIR / "calendar" / f"{week}.json"
        out_cal.parent.mkdir(parents=True, exist_ok=True)
        out_cal.write_text(json.dumps(calendar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Dagsdatan hämtas en gång och återanvänds av Swing (samma tickers och datum som kurssignalerna).
    cache: dict = {}

    def fetch_once(tickers: list[str], start: date, end: date) -> dict:
        key = (tuple(tickers), start, end)
        if key not in cache:
            cache[key] = fetch_bars(tickers, start, end)
        return cache[key]

    data = build(today, now, instruments, raw_dirs, calendar, fetch_once)
    data["errors"] = errors + calendar_errors(calendar)
    data["retry"] = retry_reasons(data, errors, today)
    add_price_error(data, today)
    res = validate_morning(data, instruments)
    if res.errors:
        print("Morgonrapporten klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    out = MORNING_DIR / f"{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"Morgonrapport {today}: {len(data['reports_today'])} rapporter/stämmor i dag, "
          f"{sum(len(c['items']) for c in data['news'])} nyheter för {len(data['news'])} bolag, "
          f"insyn {len(data['insider']['companies'])} bolag, "
          f"kurssignaler {len(data['price']['companies'])} bolag, {len(data['errors'])} fel -> {out}")
    write_swing(today, now, instruments, fetch_once)
    if data["retry"]:
        for e in data["errors"]:
            print(f"FEL: {e}")
        print(f"Ofullständig ({', '.join(data['retry'])}) – nästa inloggning eller upplåsning försöker igen.")
        return 2
    return 0


PRICE_ERROR_PREFIX = "kurser saknas för "


def add_price_error(data: dict, today: date) -> None:
    """Felet när dagsdatan inte är förra handelsdagens (ersätter ett tidigare sådant fel)."""
    data["errors"] = [e for e in data["errors"] if not e.startswith(PRICE_ERROR_PREFIX)]
    if "kurser" in data["retry"]:
        p = data["price"]
        cov = p.get("coverage") or {}
        data["errors"].append(
            f"{PRICE_ERROR_PREFIX}{previous_business_day(today)} ({cov.get('with_expected', 0)} av {cov.get('of', 0)} "
            f"bolag hos Yahoo); dagsdatan gäller {p['day'] or 'ingen dag'}")


def refresh_prices(today: date, now: datetime, fetch_bars: BarFetcher = yahoo_bars) -> int:
    """--bara-kurser: hämtar bara dagsdatan igen (ett batchanrop) och skriver om kursdelen i dagens morgonrapport
    och fliken Swing. För omförsöken var tionde minut när gårdagens kurser saknades (scripts/run_morning.ps1).
    Exit 0 = gårdagens kurser finns nu, 2 = saknas fortfarande, 1 = ingen morgonrapport eller valideringsfel."""
    out = MORNING_DIR / f"{today}.json"
    if not out.exists():
        print(f"Ingen morgonrapport för {today} – kör morning.py först.", file=sys.stderr)
        return 1
    data = json.loads(out.read_text(encoding="utf-8"))
    instruments = load_instruments()
    cache: dict = {}

    def fetch_once(tickers: list[str], start: date, end: date) -> dict:
        key = (tuple(tickers), start, end)
        if key not in cache:
            cache[key] = fetch_bars(tickers, start, end)
        return cache[key]

    data["price"] = yesterday_prices(instruments, today, fetch_once)
    data["retry"] = [r for r in data.get("retry", []) if r != "kurser"]
    if data["price"]["day"] != previous_business_day(today).isoformat():
        data["retry"].append("kurser")
    add_price_error(data, today)
    res = validate_morning(data, instruments)
    if res.errors:
        print("Morgonrapporten klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    cov = data["price"]["coverage"]
    print(f"Kurser {today}: dagsdatan gäller {data['price']['day']} ({cov['with_expected']} av {cov['of']} bolag har "
          f"{previous_business_day(today)}), kurssignaler {len(data['price']['companies'])} bolag -> {out}")
    write_swing(today, now, instruments, fetch_once)
    return 2 if "kurser" in data["retry"] else 0


def retry_reasons(data: dict, fetch_errors: list[str], today: date) -> list[str]:
    """Varför morgonrapporten ska göras om vid nästa inloggning/upplåsning: "nätverk" när ingen källa
    gick att hämta, "kurser" när Yahoo saknar förra handelsdagens kurser."""
    out = []
    if FETCH_FAILED in fetch_errors:
        out.append("nätverk")
    if data["price"]["day"] != previous_business_day(today).isoformat():
        out.append("kurser")
    return out


def write_swing(today: date, now: datetime, instruments: dict[str, Instrument], fetch: BarFetcher) -> None:
    """Fliken Swing (scripts/swing.py) med morgonrapportens dagsdata. Fel här stoppar inte morgonrapporten."""
    import swing

    try:
        tickers = [i.yahoo_ticker for i in swing.priced(instruments, today).values()]
        bars = fetch([*tickers, INDEX_TICKER], today - timedelta(days=swing.HISTORY_DAYS), today - timedelta(days=1))
        data = swing.build(today, now, instruments, bars, swing.latest_reports(today))
        out = swing.write(data, instruments)
        if out:
            print(swing.summary(data, out))
    except Exception as e:  # noqa: BLE001
        print(f"Swing kunde inte skrivas: {e}", file=sys.stderr)


def calendar_errors(calendar: dict | None) -> list[str]:
    return [f"Yahoo: {e}" for e in (calendar or {}).get("errors", [])]


if __name__ == "__main__":
    sys.exit(main())
