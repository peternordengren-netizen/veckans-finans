"""Kurs mot vinst i år: kursutveckling, vinst per aktie rullande 12 månader och P/E vid årets
början och vid veckans slut, för alla bolag i instruments.csv.

Skriver data/valuation/<vecka>.json (schema/valuation.schema.json); fliken "Kurs mot vinst".

Källor (Yahoo via yfinance):
- Vinst per aktie: "Reported EPS" i Ticker.get_earnings_dates (en rad per rapport). Kan vara
  justerad vinst för vissa bolag, men samma definition används vid båda tidpunkterna.
  Rullande 12 månader = summan av de fyra senaste rapporterna publicerade t.o.m. tidpunkten,
  bara om de hänger ihop (45–135 dagar mellan) och den senaste är högst MAX_REPORT_AGE_DAYS gammal.
- Kurs: stängningskurs (prisavkastning, jämförbar med OMX Stockholm PI), ett batchanrop.
  Årets början = sista stängningen före 1 januari; nu = sista stängningen t.o.m. veckans söndag.

Status per bolag (P/E visas bara vid "ok"):
- "kontrollera bolagshändelse": en enskild dag i år över ±BIG_MOVE_PCT % som inte förklaras av
  en verifierad post i data/corporate_actions.json – då visas varken kurs i år eller P/E
- "känd bolagshändelse": som ovan, men dagen är utredd och händelsen känd fast den inte går att
  justera (investigated med label i corporate_actions.json); etiketten visas, t.ex.
  "noterad i Stockholm 9 jan", och varken kurs i år eller P/E. Med missing_price gäller den
  också när kursen vid årets början saknas, t.ex. "A-aktie sedan 16 feb (tidigare depåbevis)"
- "noterad under året": ingen kurs vid årets början, och första handelsdagen i år är verifierad
  (listings i corporate_actions.json); etiketten visar dagen, t.ex. "noterad under året (12 jun)"
- "fastighetsbolag" (sektor fastigheter): vinsten styrs av värdeförändringar
- "investmentbolag" (kolumnen typ i instruments.csv): substansvärdet är rätt mått
- "annan valuta": vinsten redovisas i annan valuta än kursen (valutaeffekt)
- "otillräcklig vinsthistorik": rullande 12 månader saknas vid någon tidpunkt
- "förlust": vinsten är noll eller negativ vid någon tidpunkt
- "ok": P/E räknas; P/E över PE_MAX visas som "över 100", vinstförändring över ±EPS_CHANGE_MAX %
  visas som värden

Tolkning (bara "ok"): P/E upp mer än VERDICT_PCT % = "kursen före vinsten", ned mer än
VERDICT_PCT % = "vinsten före kursen", annars "i takt".

Användning:
    python scripts/valuation.py 2026-W42
Exit-kod: 0 = filen skriven, 1 = ingen kursdata eller valideringen underkände.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from prices import INDEX_NAME, INDEX_TICKER  # noqa: E402
from retry import with_retries  # noqa: E402
from signals import Bar, BarFetcher, yahoo_bars  # noqa: E402
from validate import DATA_DIR, Instrument, iso_week_period, listed, load_instruments, validate_valuation  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
CORPORATE_ACTIONS = DATA_DIR / "corporate_actions.json"
THRESHOLDS = {
    "big_move_pct": 40.0,        # enskild dag som kräver en verifierad bolagshändelse
    "verdict_pct": 10.0,         # P/E-förändring för "kursen före vinsten"/"vinsten före kursen"
    "pe_max": 100.0,             # P/E över detta visas som "över 100"
    "eps_change_max_pct": 200.0, # vinstförändring över ±detta visas som värden, inte procent
    "max_report_age_days": 135,
    "min_gap_days": 45,
    "max_gap_days": 135,
}
EpsFetcher = Callable[[str], list[tuple[date, float]]]


class NoEpsData(Exception):
    """Yahoo svarade utan vinsthistorik. Kan vara tillfälligt och görs därför om; ger alla
    försök samma svar behandlas bolaget som utan historik (inget fel)."""


def yahoo_eps(yahoo_ticker: str) -> list[tuple[date, float]]:
    """(rapportdag, rapporterad vinst per aktie) ur yfinance, nyast först."""
    import yfinance as yf

    df = yf.Ticker(yahoo_ticker).get_earnings_dates(limit=16)
    if df is None or df.empty:
        raise NoEpsData(yahoo_ticker)
    out = [(idx.date(), float(v)) for idx, v in df["Reported EPS"].items() if v == v]
    return sorted(out, reverse=True)


def ttm(series: list[tuple[date, float]], asof: date) -> tuple[float | None, list[str]]:
    """Rullande 12 mån = summan av de fyra senaste rapporterna t.o.m. asof, om de hänger ihop."""
    s = sorted((x for x in series if x[0] <= asof), reverse=True)[:4]
    dates = [d.isoformat() for d, _ in s]
    if len(s) < 4 or (asof - s[0][0]).days > THRESHOLDS["max_report_age_days"]:
        return None, dates
    for a, b in zip(s, s[1:]):
        if not THRESHOLDS["min_gap_days"] <= (a[0] - b[0]).days <= THRESHOLDS["max_gap_days"]:
            return None, dates
    return round(sum(v for _, v in s), 4), dates


def load_actions(path: Path = CORPORATE_ACTIONS) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for a in json.loads(path.read_text(encoding="utf-8")).get("actions", []):
        out.setdefault(a["ticker"], []).append(a)
    return out


def load_listings(path: Path = CORPORATE_ACTIONS) -> dict[str, str]:
    """Verifierade första handelsdagar för bolag noterade under året: {ticker: datum}."""
    if not path.exists():
        return {}
    return {x["ticker"]: x["first_trading_day"]
            for x in json.loads(path.read_text(encoding="utf-8")).get("listings", [])}


MONTHS_SHORT = ["jan", "feb", "mars", "apr", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]


def listing_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"noterad under året ({d.day} {MONTHS_SHORT[d.month - 1]})"


def load_known(path: Path = CORPORATE_ACTIONS) -> dict[str, list[dict]]:
    """Utredda händelser som är kända men inte går att justera: {ticker: [{label, yahoo_dates}]}."""
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for e in json.loads(path.read_text(encoding="utf-8")).get("investigated", []):
        if e.get("label") and (e.get("yahoo_dates") or e.get("missing_price")):
            out.setdefault(e["ticker"], []).append({"label": e["label"], "yahoo_dates": e.get("yahoo_dates", []),
                                                    "missing_price": bool(e.get("missing_price"))})
    return out


def big_moves(bars: list[Bar], start: date, end: date) -> list[dict]:
    """Dagar i år med kursrörelse över ±big_move_pct % (stängning mot föregående stängning)."""
    out = []
    for prev, cur in zip(bars, bars[1:]):
        if start <= cur.day <= end and prev.close:
            pct = (cur.close / prev.close - 1) * 100
            if abs(pct) > THRESHOLDS["big_move_pct"]:
                out.append({"date": cur.day.isoformat(), "pct": round(pct, 1)})
    return out


def adjusted_closes(bars: list[Bar], actions: list[dict]) -> list[tuple[date, float]]:
    """Stängningskurser där kurserna före varje verifierad ex-dag multipliceras med faktorn."""
    out = []
    for b in bars:
        factor = 1.0
        for a in actions:
            if b.day < date.fromisoformat(a["ex_date"]):
                factor *= a["factor"]
        out.append((b.day, b.close * factor))
    return out


def _last_close(closes: list[tuple[date, float]], before: date | None = None, upto: date | None = None) -> float | None:
    pts = [c for d, c in closes if (before is None or d < before) and (upto is None or d <= upto)]
    return pts[-1] if pts else None


def verdict(pe_change: float | None) -> str | None:
    if pe_change is None:
        return None
    if pe_change > THRESHOLDS["verdict_pct"]:
        return "kursen före vinsten"
    if pe_change < -THRESHOLDS["verdict_pct"]:
        return "vinsten före kursen"
    return "i takt"


def company(inst: Instrument, bars: list[Bar], eps: list[tuple[date, float]], actions: list[dict],
            year_start: date, asof: date, index_ytd: float | None, fin_ccy: str | None,
            known: list[dict] | None = None, listed_on: str | None = None) -> dict:
    """Värden och status för ett bolag."""
    moves = big_moves(bars, year_start, asof)
    explained = {a["ex_date"] for a in actions}
    unexplained = [m for m in moves if m["date"] not in explained]
    closes = adjusted_closes(bars, [a for a in actions if any(m["date"] == a["ex_date"] for m in moves)])
    p0, p1 = _last_close(closes, before=year_start), _last_close(closes, upto=asof)
    e0, d0 = ttm(eps, year_start - timedelta(days=1))
    e1, d1 = ttm(eps, asof)
    c = {
        "ticker": inst.ticker, "name": inst.namn, "sector": inst.sektor, "type": getattr(inst, "typ", None),
        "financial_currency": fin_ccy, "price_start": p0, "price_now": p1, "ytd_pct": None, "rel_index_pp": None,
        "eps_ttm_start": e0, "eps_ttm_now": e1, "eps_reports_start": d0, "eps_reports_now": d1,
        "eps_change_pct": None, "pe_start": None, "pe_now": None, "pe_change_pct": None, "pe_over_max": False,
        "verdict": None, "big_moves": moves, "adjusted_ex_dates": sorted(explained & {m["date"] for m in moves}),
        "status": "ok", "event_label": None,
    }
    if p0 is None or p1 is None:
        missing_label = next((k["label"] for k in (known or []) if k.get("missing_price")), None)
        if p0 is None and listed_on and year_start.isoformat() <= listed_on <= asof.isoformat():
            c["status"] = "noterad under året"
            c["event_label"] = listing_label(listed_on)
        elif p0 is None and missing_label:
            # Känd händelse som förklarar att kursen vid årets början saknas (t.ex. byte från depåbevis).
            c["status"] = "känd bolagshändelse"
            c["event_label"] = missing_label
        else:
            c["status"] = "kurs saknas"
        return c
    if unexplained:
        known_dates = {d: k["label"] for k in (known or []) for d in k["yahoo_dates"]}
        if all(m["date"] in known_dates for m in unexplained):
            c["status"] = "känd bolagshändelse"
            c["event_label"] = "; ".join(dict.fromkeys(known_dates[m["date"]] for m in unexplained))
        else:
            c["status"] = "kontrollera bolagshändelse"
        return c
    c["ytd_pct"] = round((p1 / p0 - 1) * 100, 2)
    if index_ytd is not None:
        c["rel_index_pp"] = round(c["ytd_pct"] - index_ytd, 2)
    if inst.sektor == "fastigheter":
        c["status"] = "fastighetsbolag"
    elif c["type"] == "investmentbolag":
        c["status"] = "investmentbolag"
    elif (fin_ccy or "SEK") != "SEK":
        c["status"] = "annan valuta"
    elif e0 is None or e1 is None:
        c["status"] = "otillräcklig vinsthistorik"
    elif e0 <= 0 or e1 <= 0:
        c["status"] = "förlust"
    if e0 is not None and e1 is not None and e0 > 0 and e1 > 0:
        change = (e1 / e0 - 1) * 100
        if abs(change) <= THRESHOLDS["eps_change_max_pct"]:
            c["eps_change_pct"] = round(change, 1)
    if c["status"] != "ok":
        return c
    pe0, pe1 = p0 / e0, p1 / e1
    c["pe_start"], c["pe_now"] = round(pe0, 1), round(pe1, 1)
    c["pe_over_max"] = pe0 > THRESHOLDS["pe_max"] or pe1 > THRESHOLDS["pe_max"]
    if not c["pe_over_max"]:
        # Räknas på de avrundade P/E-talen som visas (och som valideringen räknar om).
        c["pe_change_pct"] = round((c["pe_now"] / c["pe_start"] - 1) * 100, 1)
        c["verdict"] = verdict(c["pe_change_pct"])
    return c


def build(week: str, instruments: dict[str, Instrument], fetch_bars: BarFetcher, fetch_eps: EpsFetcher,
          currencies: dict[str, str | None], now: datetime, actions: dict[str, list[dict]] | None = None,
          known: dict[str, list[dict]] | None = None, listings: dict[str, str] | None = None) -> dict:
    _, end = iso_week_period(week)
    asof = min(end, now.date())
    instruments = listed(instruments, asof)  # avnoterade bolag hoppas över (instruments.csv)
    year_start = date(asof.year, 1, 1)
    actions = load_actions() if actions is None else actions
    known = load_known() if known is None else known
    listings = load_listings() if listings is None else listings
    bars = fetch_bars([*[i.yahoo_ticker for i in instruments.values()], INDEX_TICKER],
                      year_start - timedelta(days=20), asof)
    idx = [(b.day, b.close) for b in bars.get(INDEX_TICKER, [])]
    i0, i1 = _last_close(idx, before=year_start), _last_close(idx, upto=asof)
    index_ytd = round((i1 / i0 - 1) * 100, 2) if i0 and i1 else None
    companies, errors = [], []
    for inst in instruments.values():
        try:
            eps = with_retries(lambda: fetch_eps(inst.yahoo_ticker), f"{inst.yahoo_ticker} (vinsthistorik)")
        except NoEpsData:
            eps = []
        except Exception as exc:  # noqa: BLE001 – ett bolag får aldrig stoppa resten
            errors.append(f"{inst.yahoo_ticker}: {type(exc).__name__}")
            eps = []
        companies.append(company(inst, bars.get(inst.yahoo_ticker, []), eps, actions.get(inst.ticker, []),
                                 year_start, asof, index_ytd, currencies.get(inst.ticker), known.get(inst.ticker),
                                 listings.get(inst.ticker)))
    return {
        "schema_version": 1, "week": week, "as_of": asof.isoformat(), "year_start": year_start.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"), "thresholds": THRESHOLDS,
        "index": {"ticker": INDEX_TICKER, "name": INDEX_NAME, "ytd_pct": index_ytd},
        "companies": companies, "errors": errors,
    }


def currencies_from_consensus() -> dict[str, str | None]:
    """Redovisningsvaluta per bolag ur senaste konsensusfilen (scripts/consensus.py)."""
    files = sorted((DATA_DIR / "consensus").glob("*.json"))
    if not files:
        return {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return {c["ticker"]: c.get("financial_currency") for c in data["companies"]}


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("week")
    args = p.parse_args(argv)
    if not re.fullmatch(r"\d{4}-W\d{2}", args.week):
        print(f"ogiltig vecka: {args.week!r}", file=sys.stderr)
        return 1
    instruments = load_instruments()
    data = build(args.week, instruments, yahoo_bars, yahoo_eps, currencies_from_consensus(),
                 now or datetime.now(STOCKHOLM))
    if all(c["price_now"] is None for c in data["companies"]):
        print("Ingen kursdata – ingen fil skrivs.", file=sys.stderr)
        return 1
    res = validate_valuation(data, args.week, instruments)
    if res.errors:
        print("Filen klarar inte valideringen – skrivs inte:", *res.errors[:20], sep="\n  ", file=sys.stderr)
        return 1
    out = DATA_DIR / "valuation" / f"{args.week}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from collections import Counter

    status = Counter(c["status"] for c in data["companies"])
    verdicts = Counter(c["verdict"] for c in data["companies"] if c["verdict"])
    print(f"Kurs mot vinst {args.week} (t.o.m. {data['as_of']}): index i år {data['index']['ytd_pct']} %; "
          f"status {dict(status)}; tolkning {dict(verdicts)}; {len(data['errors'])} fel -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
