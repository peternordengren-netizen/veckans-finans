"""Rapportkalender för en vecka: rapportdatum och stämmor för bolagen i instruments.csv.

Skriver data/calendar/<vecka>.json (schema/calendar.schema.json).

Källor:
- Yahoo (yfinance, Ticker.calendar, ett anrop per bolag) är grunden för rapportdatum.
- MFN/Cision-posterna i data/raw/ (inbjudningar till rapportpresentationer och kallelser
  till stämmor) läses för de senaste LOOKBACK_WEEKS veckorna. Datumet tas ur texten.

Status per rapport:
- "bekräftat": Yahoo och MFN anger samma datum
- "avvikelse": Yahoo och MFN anger olika datum – båda visas
- "yahoo": bara Yahoo har ett datum
- "mfn": bara MFN har ett datum (även alla stämmor – Yahoo har inga stämmodatum)

MFN-datum som redan passerats (före körningsdagen) räknas inte: de gäller en tidigare
rapport. Fail-soft: om Yahoo inte svarar för ett bolag hamnar det i "errors" och
MFN-datumen används ensamma.

Användning:
    python scripts/report_calendar.py 2026-W43
Exit-kod: 0 = filen skriven, 1 = fel i indata eller valideringen underkände filen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from retry import with_retries  # noqa: E402
from validate import (  # noqa: E402
    DATA_DIR, RAW_DIR, Instrument, iso_week_period, load_instruments, validate_calendar,
)

STOCKHOLM = ZoneInfo("Europe/Stockholm")
LOOKBACK_WEEKS = 6  # kallelser till stämma kommer 2–6 veckor före, inbjudningar 1–3 veckor före
MAX_DAYS_AHEAD = 100  # datum längre fram än så efter publiceringen är inte händelsens datum
CONTEXT_CHARS = 150  # hur långt från ett datum ett nyckelord får stå för att räknas

# Vilka poster som räknas, utifrån rubriken.
_ANNOUNCE = r"inbjud|invitation|bjuder in|invites|telefonkonferens|conference call|webcast|presentation av|publicerar datum"
_REPORT = r"rapport|report|kvartal|quarter|\bq[1-4]\b|bokslut|year-end|resultat|results|delår|interim"
REPORT_TITLE_RE = re.compile(rf"(?=.*(?:{_ANNOUNCE}))(?=.*(?:{_REPORT}))", re.IGNORECASE | re.DOTALL)
AGM_TITLE_RE = re.compile(
    r"(?=.*(?:kallelse|notice|välkommen till))(?=.*(?:stämma|general meeting))", re.IGNORECASE | re.DOTALL
)
# Kapitalmarknadsdagar och liknande är inga rapporter även om de "presenterar resultat".
EXCLUDE_TITLE_RE = re.compile(r"kapitalmarknadsdag|capital markets day|investerardag|investor day", re.IGNORECASE)
REPORT_CONTEXT_RE = re.compile(_REPORT + r"|publicer|offentliggör|publish", re.IGNORECASE)
AGM_CONTEXT_RE = re.compile(r"stämma|general meeting|meeting", re.IGNORECASE)

MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6, "juli": 7,
    "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7,
    "august": 8, "october": 10,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
# "22 oktober 2026", "den 22 oktober", "22 October 2026", "October 22, 2026", "2026-10-22".
DATE_RES = (
    (re.compile(rf"\b(?P<d>\d{{1,2}})\.?\s+(?P<m>{_MONTH})\b(?:\s+(?P<y>20\d\d))?", re.IGNORECASE), "dmy"),
    (re.compile(rf"\b(?P<m>{_MONTH})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b,?(?:\s+(?P<y>20\d\d))?", re.IGNORECASE), "mdy"),
    (re.compile(r"\b(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)\b"), "iso"),
)


@dataclass(frozen=True)
class MfnEvent:
    """En rapport eller stämma som MFN/Cision-posten anger datum för."""

    ticker: str
    kind: str  # "rapport" | "stämma"
    date: date
    url: str
    title: str
    published: datetime


_RANGE_AFTER_RE = re.compile(rf"\s*[-–—]\s*(?:\d|{_MONTH})", re.IGNORECASE)
_RANGE_BEFORE_RE = re.compile(r"[-–—]\s*$")


def find_dates(text: str, published: date) -> list[tuple[int, date]]:
    """Alla datum i texten som (position, datum). Saknas år antas närmaste år framåt.

    Datum som ingår i ett intervall ("1 januari – 30 september") hoppas över: de anger
    rapportperioden, inte när rapporten kommer.
    """
    text = re.sub(r"\s+", " ", text)
    found = []
    for rx, kind in DATE_RES:
        for m in rx.finditer(text):
            if _RANGE_AFTER_RE.match(text, m.end()) or _RANGE_BEFORE_RE.search(text[max(0, m.start() - 3):m.start()]):
                continue
            try:
                month = int(m.group("m")) if kind == "iso" else MONTHS[m.group("m").lower()]
                year = int(m.group("y")) if m.group("y") else published.year
                d = date(year, month, int(m.group("d")))
                if not m.group("y") and d < published:
                    d = date(year + 1, month, int(m.group("d")))
            except ValueError:
                continue
            found.append((m.start(), d))
    return sorted(found)


def event_date(title: str, body: str, published: date, context: re.Pattern) -> date | None:
    """Händelsens datum ur en inbjudan eller kallelse.

    Bara datum efter publiceringsdagen och högst MAX_DAYS_AHEAD dagar fram räknas (så
    att pressmeddelandets eget datum aldrig väljs). Ett datum i rubriken går först,
    sedan det första datum i texten som har ett nyckelord (context) i närheten, sist
    det första datumet över huvud taget.
    """
    def usable(found: Iterable[tuple[int, date]]) -> list[tuple[int, date]]:
        last = published + timedelta(days=MAX_DAYS_AHEAD)
        return [(p, d) for p, d in found if published < d <= last]

    in_title = usable(find_dates(title, published))
    if in_title:
        return in_title[0][1]
    flat = re.sub(r"\s+", " ", body)
    candidates = usable(find_dates(flat, published))
    for pos, d in candidates:
        if context.search(_sentence(flat, pos)):
            return d
    return candidates[0][1] if candidates else None


# Meningsslut, men inte förkortningen "kl." (som står efter nästan varje rapportdatum).
_SENTENCE_END_RE = re.compile(r"(?<!\bkl)[.!?](?=\s)", re.IGNORECASE)


def _sentence(text: str, pos: int) -> str:
    """Meningen runt position pos, högst CONTEXT_CHARS tecken åt varje håll."""
    ends_before = [m.end() for m in _SENTENCE_END_RE.finditer(text, max(0, pos - CONTEXT_CHARS), pos)]
    start = ends_before[-1] if ends_before else max(0, pos - CONTEXT_CHARS)
    after = _SENTENCE_END_RE.search(text, pos, pos + CONTEXT_CHARS)
    return text[start: after.end() if after else pos + CONTEXT_CHARS]


def classify(title: str) -> str | None:
    """'rapport', 'stämma' eller None utifrån rubriken."""
    if EXCLUDE_TITLE_RE.search(title):
        return None
    if AGM_TITLE_RE.search(title):
        return "stämma"
    if REPORT_TITLE_RE.search(title):
        return "rapport"
    return None


def name_patterns(instruments: dict[str, Instrument]) -> list[tuple[re.Pattern, str]]:
    """Namn och alias (minst tre tecken) som hela ord, även i genitiv ("Bonavas", "SEB:s"), längsta först."""
    pairs = sorted(
        ((n, inst.ticker) for inst in instruments.values() for n in (inst.namn, *inst.alias) if len(n) >= 3),
        key=lambda p: -len(p[0]),
    )
    return [(re.compile(rf"(?<!\w){re.escape(n)}(?::?s)?(?!\w)", re.IGNORECASE), t) for n, t in pairs]


from fetch import slug_matches, slugify  # noqa: E402  (samma slug-regel som hämtningens scope-filter)


def match_company(item: dict, instruments: dict[str, Instrument], patterns: list[tuple[re.Pattern, str]]) -> str | None:
    """Ticker för postens avsändare.

    Har posten en bolagsslug (MFN och Cision) måste den stämma med bolagets mfn_slug, eller
    med namnet/ett alias som slug, eventuellt följt av ett bolagssuffix ("-ab", "-group" …).
    Annars skulle t.ex. "Catena Media" (inte i listan) matchas som Catena via rubriken.
    Bara poster utan slug matchas på namn eller alias i rubriken.
    """
    slug = item.get("company")
    if slug:
        for inst in instruments.values():
            if inst.mfn_slug and inst.mfn_slug == slug and item.get("source") == "mfn":
                return inst.ticker
        for inst in instruments.values():
            for n in (inst.namn, *inst.alias):
                if slug_matches(slug, slugify(n)):
                    return inst.ticker
        return None
    for rx, ticker in patterns:
        if rx.search(item.get("title", "")):
            return ticker
    return None


def _previous_weeks(week: str, n: int) -> list[str]:
    start, _ = iso_week_period(week)
    out = []
    for i in range(n, -1, -1):
        y, w, _ = (start - timedelta(weeks=i)).isocalendar()
        out.append(f"{y}-W{w:02d}")
    return out


def mfn_events(raw_dir: Path, week: str, instruments: dict[str, Instrument], today: date) -> dict[tuple[str, str], MfnEvent]:
    """Senaste MFN/Cision-händelsen per (ticker, typ) ur de senaste veckornas rådata."""
    patterns = name_patterns(instruments)
    events: dict[tuple[str, str], MfnEvent] = {}
    for wk in _previous_weeks(week, LOOKBACK_WEEKS):
        index_path = raw_dir / wk / "index.json"
        if not index_path.exists():
            continue
        for item in json.loads(index_path.read_text(encoding="utf-8")).get("items", []):
            if item.get("source") not in ("mfn", "cision"):
                continue
            kind = classify(item.get("title", ""))
            if kind is None:
                continue
            ticker = match_company(item, instruments, patterns)
            if ticker is None:
                continue
            published = datetime.fromisoformat(item["published"])
            text_path = raw_dir / wk / item["path"]
            body = text_path.read_text(encoding="utf-8").split("\n---\n", 1)[-1] if text_path.exists() else ""
            context = AGM_CONTEXT_RE if kind == "stämma" else REPORT_CONTEXT_RE
            d = event_date(item["title"], body, published.date(), context)
            if d is None or d < today:
                continue
            ev = MfnEvent(ticker, kind, d, item["url"], item["title"], published)
            old = events.get((ticker, kind))
            if old is None or ev.published > old.published:
                events[(ticker, kind)] = ev
    return events


def yahoo_report_dates(yahoo_ticker: str) -> list[date]:
    """Kommande rapportdatum enligt Yahoo (yfinance Ticker.calendar)."""
    import yfinance as yf

    dates = (yf.Ticker(yahoo_ticker).calendar or {}).get("Earnings Date") or []
    return sorted(d for d in dates if isinstance(d, date))


def build(
    week: str,
    raw_dir: Path,
    instruments: dict[str, Instrument],
    today: date,
    now: datetime,
    get_dates: Callable[[str], list[date]] = yahoo_report_dates,
) -> dict:
    """Bygger kalenderfilen för veckan."""
    start, end = iso_week_period(week)
    in_week = lambda d: d is not None and start <= d <= end  # noqa: E731
    mfn = mfn_events(raw_dir, week, instruments, today)
    errors: list[str] = []
    events: list[dict] = []

    for inst in instruments.values():
        try:
            ydates = [d for d in with_retries(lambda: get_dates(inst.yahoo_ticker), f"{inst.yahoo_ticker} (rapportdatum)")
                      if d >= today]
        except Exception as exc:  # noqa: BLE001 – en ticker får aldrig stoppa resten
            errors.append(f"{inst.yahoo_ticker}: {type(exc).__name__}")
            ydates = []
        ev = mfn.get((inst.ticker, "rapport"))
        y_in_week = next((d for d in ydates if in_week(d)), None)
        m = ev.date if ev else None
        if not (y_in_week or in_week(m)):
            continue
        # Yahoo-datum att jämföra med: det i veckan, annars närmast MFN-datumet.
        y = y_in_week or (min(ydates, key=lambda d: abs((d - m).days)) if ydates and m else None)
        if y and m:
            status = "bekräftat" if y == m else "avvikelse"
        else:
            status = "yahoo" if y else "mfn"
        events.append(_event(inst, "rapport", y_in_week or m, status, y, ev))

    for (ticker, kind), ev in mfn.items():
        if kind == "stämma" and in_week(ev.date):
            events.append(_event(instruments[ticker], "stämma", ev.date, "mfn", None, ev))

    events.sort(key=lambda e: (e["date"], e["kind"], e["name"].casefold()))
    return {
        "schema_version": 1,
        "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": now.isoformat(timespec="seconds"),
        "events": events,
        "errors": errors,
    }


def _event(inst: Instrument, kind: str, d: date, status: str, y: date | None, ev: MfnEvent | None) -> dict:
    return {
        "date": d.isoformat(),
        "kind": kind,
        "ticker": inst.ticker,
        "yahoo_ticker": inst.yahoo_ticker,
        "name": inst.namn,
        "sector": inst.sektor,
        "status": status,
        "yahoo_date": y.isoformat() if y else None,
        "mfn_date": ev.date.isoformat() if ev else None,
        "mfn_url": ev.url if ev else None,
        "mfn_title": ev.title if ev else None,
    }


def main(argv: list[str] | None = None, get_dates: Callable[[str], list[date]] = yahoo_report_dates,
         now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("week")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}-W\d{2}", args.week):
        print(f"ogiltig vecka: {args.week!r}", file=sys.stderr)
        return 1
    now = now or datetime.now(STOCKHOLM)
    instruments = load_instruments()
    data = build(args.week, RAW_DIR, instruments, now.date(), now, get_dates)
    res = validate_calendar(data, args.week, instruments, RAW_DIR)
    for w in res.warnings:
        print(f"varning: {w}")
    if res.errors:
        for e in res.errors:
            print(f"fel: {e}", file=sys.stderr)
        return 1
    out = args.out or DATA_DIR / "calendar" / f"{args.week}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    counts = {s: sum(e["status"] == s for e in data["events"] if e["kind"] == "rapport")
              for s in ("bekräftat", "avvikelse", "yahoo", "mfn")}
    agms = sum(e["kind"] == "stämma" for e in data["events"])
    print(f"Kalender {args.week}: {sum(counts.values())} rapporter ({', '.join(f'{k} {v}' for k, v in counts.items())}), "
          f"{agms} stämmor, {len(data['errors'])} Yahoo-fel -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
