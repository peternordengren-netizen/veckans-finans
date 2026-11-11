"""Signalöversikt per bolag för en vecka: kurs mot index, omsättning, 52-veckorsläge,
nyhetston och insyn för alla bolag i instruments.csv.

Skriver data/signals/<vecka>.json (schema/signals.schema.json).

Kursdata hämtas med ett enda batchanrop till Yahoo (yfinance.download) för hela listan
plus jämförelseindexet. Ton kommer ur veckofilen (direkt berörda bolag), insyn ur analysfilen
(scripts/registers.py). Båda är valfria.

Blankning ingår inte sedan 2026-11-11 (FI tillåter inte automatisk hämtning av blankningsregistret). Äldre
filer har kvar short_positions_pp, short_aggregate_pp, short_as_of och blankningströsklarna; valideringen
räknar deras signal "blankning" som förut (signals_for), nya filer har den aldrig.

Signaler (trösklarna i THRESHOLDS, sparas i filen så att valideringen kan räkna om):
- "insyn": insynstransaktioner över registers.py:s tröskel under veckan
- "blankning" (bara äldre filer): den sammanlagda blankningen ändrad minst short_aggregate_pp procentenheter
- "avviker": nyhetens ton och kursen mot index går åt olika håll (samma regel som
  sidans "avviker mot index", src/lib/tone.ts)
- "pe-upp": P/E har stigit minst pe_up_pct % i år (ur data/valuation/<vecka>.json, scripts/valuation.py;
  bara bolag där P/E räknas där)
- "stor-rörelse": veckans kurs skiljer sig mer än big_move_pp procentenheter från index,
  åt något håll (bara aktier i SEK, där jämförelsen mot index finns)
- "omsättning": veckans snittomsättning (antal aktier per dag) minst volume_ratio gånger
  snittet för de VOLUME_BASE_DAYS handelsdagarna före veckan. Veckosnittet i stället för
  veckans största dag, så att en enstaka toppdag (t.ex. derivatförfall) inte räcker –
  med största dag flaggades 74 av 266 bolag i W42, med veckosnittet 20.
- "52v-högsta" / "52v-lägsta": veckans sista stängning högst near_52w_pct procent från
  52-veckorshögsta respektive -lägsta (dagshögsta/-lägsta, inte justerade)

Kursförändringen räknas som i prices.py, på justerad stängning. Jämförelsen mot index
görs bara för aktier i SEK, vilket här antas för alla .ST-tickers.

Användning:
    python scripts/signals.py 2026-W42
Exit-kod: 0 = filen skriven, 1 = ingen kursdata eller valideringen underkände filen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from prices import INDEX_NAME, INDEX_TICKER  # noqa: E402
from retry import retry_missing  # noqa: E402
from validate import DATA_DIR, Instrument, iso_week_period, listed, load_instruments, validate_signals  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
# Blankningströsklarna (short_aggregate_pp, short_only_aggregate) finns bara i äldre filer (till 2026-11-11).
THRESHOLDS = {"volume_ratio": 2.0, "near_52w_pct": 2.0, "big_move_pp": 5.0, "pe_up_pct": 30.0}
VOLUME_BASE_DAYS = 20
HISTORY_DAYS = 400  # 52 veckor plus marginal för omsättningssnittet före veckan
SEK_SUFFIX = ".ST"


@dataclass(frozen=True)
class Bar:
    """En handelsdag."""

    day: date
    close: float
    adj_close: float
    high: float
    low: float
    volume: float
    # Öppningskursen (för candlemönster i Swing, scripts/pullback.py); hämtas i samma batchanrop.
    # None i äldre sparade hämtningar och i tester som inte behöver den.
    open: float | None = None


BarFetcher = Callable[[list[str], date, date], dict[str, list[Bar]]]


def yahoo_bars(tickers: list[str], start: date, end_inclusive: date) -> dict[str, list[Bar]]:
    """Dagsdata för alla tickers i ett batchanrop (yfinance.download). Tickers som saknas i svaret
    hämtas om upp till två gånger (scripts/retry.py); de som fortfarande saknas utelämnas."""
    return retry_missing(lambda ks: _download_bars(ks, start, end_inclusive), tickers, "kursdata (batch)")


def _download_bars(tickers: list[str], start: date, end_inclusive: date) -> dict[str, list[Bar]]:
    import yfinance as yf

    df = yf.download(tickers, start=start.isoformat(), end=(end_inclusive + timedelta(days=1)).isoformat(),
                     interval="1d", group_by="ticker", auto_adjust=False, actions=False, threads=True,
                     progress=False)
    # Rader där Yahoo har high och low men ingen stängning (2026-11-10: måndagens rad för 262 av 266 bolag, Close
    # NaN): stängningen hämtas ur timdatan, ett batchanrop för de bolagen (_hourly_closes).
    gaps: dict[str, list[date]] = {}
    for t in tickers:
        try:
            sub = df[t]
        except KeyError:
            continue
        miss = sub[sub["Close"].isna() & sub["High"].notna() & sub["Low"].notna()]
        if len(miss):
            gaps[t] = [idx.date() for idx in miss.index]
    filled = _hourly_closes(gaps) if gaps else {}
    if gaps:
        n = sum(len(v) for v in filled.values())
        print(f"Yahoo saknar stängningskurs i dagsdatan för {len(gaps)} bolag "
              f"({', '.join(sorted({d.isoformat() for v in gaps.values() for d in v}))}); "
              f"{n} hämtade ur timdatan (sista timmens stängning)", file=sys.stderr)
    out: dict[str, list[Bar]] = {}
    for t in tickers:
        try:
            sub = df[t]
        except KeyError:
            continue
        bars = []
        for idx, r in sub.iterrows():
            close = float(r["Close"]) if r["Close"] == r["Close"] else filled.get(t, {}).get(idx.date())
            if close is None or r["High"] != r["High"] or r["Low"] != r["Low"]:
                continue
            # Ur timdatan: ingen justerad stängning (senaste dagarna, faktorn är 1 utan utdelning efteråt).
            adj = float(r["Adj Close"]) if r["Adj Close"] == r["Adj Close"] else close
            bars.append(Bar(idx.date(), close, adj, float(r["High"]), float(r["Low"]),
                            float(r["Volume"]) if r["Volume"] == r["Volume"] else 0.0,
                            float(r["Open"]) if r["Open"] == r["Open"] else None))
        if bars:
            out[t] = bars
    return out


def _hourly_closes(gaps: dict[str, list[date]]) -> dict[str, dict[date, float]]:
    """Stängningen för dagar där dagsdatan saknar den: sista timstapelns stängning samma dag (Stockholmstid),
    som innehåller stängningsauktionen 17:25–17:30. Ett batchanrop (yfinance, intervall 60m) för alla bolagen.
    Kontrollerat 2026-11-10: Betsson 98,20 och Hexagon 107,20 för 2026-11-09, samma som dagsstängningen kvällen före."""
    import yfinance as yf

    days = sorted({d for v in gaps.values() for d in v})
    try:
        df = yf.download(list(gaps), start=days[0].isoformat(), end=(days[-1] + timedelta(days=1)).isoformat(),
                         interval="60m", group_by="ticker", auto_adjust=False, actions=False, threads=True,
                         progress=False)
    except Exception as exc:  # noqa: BLE001 - reserven får aldrig stoppa hämtningen
        print(f"Timdatan kunde inte hämtas: {type(exc).__name__}", file=sys.stderr)
        return {}
    return hourly_closes_from(df, gaps)


def hourly_closes_from(df, gaps: dict[str, list[date]]) -> dict[str, dict[date, float]]:
    """{ticker: {dag: sista timstapelns stängning}} för dagarna i gaps, ur en timtabell grupperad per ticker."""
    out: dict[str, dict[date, float]] = {}
    for t, wanted in gaps.items():
        try:
            sub = df[t].dropna(subset=["Close"])
        except KeyError:
            continue
        for idx, r in sub.iterrows():
            ts = idx.tz_convert("Europe/Stockholm") if getattr(idx, "tzinfo", None) else idx
            if ts.date() in wanted:
                out.setdefault(t, {})[ts.date()] = float(r["Close"])  # stigande tid: sista stapeln vinner
    return out


def week_change(bars: list[Bar], start: date, end: date) -> float | None:
    """Veckans förändring i procent på justerad stängning, som prices.week_change."""
    before = [b for b in bars if b.day < start]
    during = [b for b in bars if start <= b.day <= end]
    if not before or not during:
        return None
    return round((during[-1].adj_close / before[-1].adj_close - 1) * 100, 2)


# Dagsdatans faktiska datum: den senaste dagen som minst så stor andel av bolagen (med kurser) har en kursrad
# för. Yahoo har ofta inte gårdagens rader för svenska aktier kl. 08:10, men för ett fåtal (utländska) bolag och
# index – det senaste datumet hos något bolag är därför inte dagsdatans datum (2026-11-05: 3 av 266).
MIN_DAY_COVERAGE = 0.9


def data_day(bars: dict[str, list[Bar]], tickers: list[str], before: date) -> tuple[date | None, int, int]:
    """(dagsdatans datum, antal bolag med kursrad den dagen, antal bolag med kurser) för raderna före `before`.
    Datumet är den senaste dagen som minst MIN_DAY_COVERAGE av bolagen med kurser har en rad för; None utan kurser."""
    have = [{b.day for b in bars.get(t, []) if b.day < before} for t in tickers]
    have = [s for s in have if s]
    if not have:
        return None, 0, 0
    for d in sorted(set().union(*have), reverse=True):
        n = sum(1 for s in have if d in s)
        if n >= MIN_DAY_COVERAGE * len(have):
            return d, n, len(have)
    return None, 0, len(have)


def price_stats(bars: list[Bar], start: date, end: date) -> dict:
    """Kursnyckeltal för veckan: förändring, omsättning mot snitt och 52-veckorsläge."""
    during = [b for b in bars if start <= b.day <= end]
    if not during:
        return {}
    last = during[-1]
    base = [b.volume for b in bars if b.day < start][-VOLUME_BASE_DAYS:]
    avg = sum(base) / len(base) if len(base) >= VOLUME_BASE_DAYS // 2 else 0
    year = [b for b in bars if last.day - timedelta(days=365) < b.day <= last.day]
    high, low = max(b.high for b in year), min(b.low for b in year)
    return {
        "change_pct": week_change(bars, start, end),
        "close": round(last.close, 4),
        "volume_ratio": round(sum(b.volume for b in during) / len(during) / avg, 2) if avg > 0 else None,
        "high_52w": round(high, 4),
        "low_52w": round(low, 4),
        "from_high_pct": round((last.close / high - 1) * 100, 2) if high > 0 else None,
        "from_low_pct": round((last.close / low - 1) * 100, 2) if low > 0 else None,
    }


def tone_of(labels: list[str]) -> str | None:
    """Sammanvägd ton för bolagets direkta nyheter: positiv/negativ, blandad om båda, annars neutral."""
    if not labels:
        return None
    has_pos, has_neg = "positiv" in labels, "negativ" in labels
    if has_pos and has_neg:
        return "blandad"
    return "positiv" if has_pos else "negativ" if has_neg else "neutral"


def news_by_ticker(week_data: dict | None) -> dict[str, list[str]]:
    """Tonen för varje nyhet där bolaget är direkt berört, per ticker."""
    out: dict[str, list[str]] = {}
    for s in (week_data or {}).get("sectors", []):
        for it in s["items"]:
            label = (it.get("sentiment") or {}).get("label", "neutral")
            for c in it["companies"]:
                if c.get("match") == "matchad" and c.get("impact") == "direkt" and c.get("ticker"):
                    out.setdefault(c["ticker"], []).append(label)
    return out


def diverges(tone: str | None, relative_pp: float | None) -> bool:
    """Samma regel som src/lib/tone.ts divergesFromPrice."""
    if relative_pp is None:
        return False
    return (tone == "positiv" and relative_pp < 0) or (tone == "negativ" and relative_pp > 0)


def signals_for(c: dict, thresholds: dict) -> list[str]:
    """Bolagets signaler ur de uträknade värdena (används även av valideringen)."""
    out = []
    if c["insider_net_sek"] is not None:
        out.append("insyn")
    # Blankning, bara i äldre filer (med short_aggregate_pp i trösklarna): den sammanlagda blankningen har
    # ändrats minst short_aggregate_pp. Filer utan short_only_aggregate räknade också varje ändrad enskild
    # position över 0,5 %. Nya filer (från 2026-11-11) har ingen blankning.
    if "short_aggregate_pp" in thresholds:
        agg, pos = c.get("short_aggregate_pp"), c.get("short_positions_pp")
        aggregate = agg is not None and abs(agg) >= thresholds["short_aggregate_pp"]
        positions = pos is not None and not thresholds.get("short_only_aggregate", False)
        if aggregate or positions:
            out.append("blankning")
    if diverges(c["tone"], c["relative_pp"]):
        out.append("avviker")
    if c["relative_pp"] is not None and abs(c["relative_pp"]) > thresholds["big_move_pp"]:
        out.append("stor-rörelse")
    if c["volume_ratio"] is not None and c["volume_ratio"] >= thresholds["volume_ratio"]:
        out.append("omsättning")
    if c.get("pe_change_pct") is not None and c["pe_change_pct"] >= thresholds["pe_up_pct"]:
        out.append("pe-upp")
    if c["from_high_pct"] is not None and c["from_high_pct"] >= -thresholds["near_52w_pct"]:
        out.append("52v-högsta")
    if c["from_low_pct"] is not None and c["from_low_pct"] <= thresholds["near_52w_pct"]:
        out.append("52v-lägsta")
    return out


def build(week: str, instruments: dict[str, Instrument], week_data: dict | None, analysis: dict | None,
          fetch: BarFetcher, today: date, now: datetime, valuation: dict | None = None) -> dict:
    start, end = iso_week_period(week)
    end = min(end, today)
    instruments = listed(instruments, start)  # avnoterade före veckan hoppas över (instruments.csv)
    tickers = [i.yahoo_ticker for i in instruments.values()]
    bars = fetch([*tickers, INDEX_TICKER], start - timedelta(days=HISTORY_DAYS), end)
    index_change = week_change(bars.get(INDEX_TICKER, []), start, end)
    news = news_by_ticker(week_data)

    insider = {c["ticker"]: c for c in ((analysis or {}).get("insider") or {}).get("companies", [])}

    pe_change = {c["ticker"]: c["pe_change_pct"] for c in (valuation or {}).get("companies", [])}
    companies, errors = [], []
    for inst in instruments.values():
        stats = price_stats(bars.get(inst.yahoo_ticker, []), start, end)
        if not stats:
            errors.append(f"{inst.yahoo_ticker}: ingen kursdata för veckan")
        change = stats.get("change_pct")
        relative = (round(change - index_change, 2)
                    if change is not None and index_change is not None and inst.yahoo_ticker.endswith(SEK_SUFFIX)
                    else None)
        ins = insider.get(inst.ticker)
        c = {
            "ticker": inst.ticker,
            "name": inst.namn,
            "yahoo_ticker": inst.yahoo_ticker,
            "sector": inst.sektor,
            "change_pct": change,
            "relative_pp": relative,
            "close": stats.get("close"),
            "volume_ratio": stats.get("volume_ratio"),
            "high_52w": stats.get("high_52w"),
            "low_52w": stats.get("low_52w"),
            "from_high_pct": stats.get("from_high_pct"),
            "from_low_pct": stats.get("from_low_pct"),
            "tone": tone_of(news.get(inst.ticker, [])),
            "news": len(news.get(inst.ticker, [])),
            "insider_net_sek": ins["net_value_sek"] if ins else None,
            "insider_buyers": len(ins["buyers"]) if ins else 0,
            "insider_sellers": len(ins["sellers"]) if ins else 0,
            "pe_change_pct": pe_change.get(inst.ticker),
        }
        c["signals"] = signals_for(c, THRESHOLDS)
        companies.append(c)

    out = {
        "schema_version": 1,
        "week": week,
        "period": {"start": start.isoformat(), "end": iso_week_period(week)[1].isoformat()},
        "generated_at": now.isoformat(timespec="seconds"),
        "thresholds": THRESHOLDS,
        "index": {"ticker": INDEX_TICKER, "name": INDEX_NAME, "change_pct": index_change} if index_change is not None else None,
        "companies": companies,
        "errors": errors,
    }
    if (week_data or {}).get("mock"):
        out["mock"] = True
    return out


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main(argv: list[str] | None = None, fetch: BarFetcher | None = None, now: datetime | None = None) -> int:
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
    data = build(args.week, instruments, _read(DATA_DIR / "weeks" / f"{args.week}.json"),
                 _read(DATA_DIR / "analysis" / f"{args.week}.json"), fetch or yahoo_bars, now.date(), now,
                 _read(DATA_DIR / "valuation" / f"{args.week}.json"))
    with_prices = sum(c["change_pct"] is not None for c in data["companies"])
    if with_prices == 0:
        print("Ingen kursdata kunde hämtas – ingen signalfil skrivs.", file=sys.stderr)
        return 1
    res = validate_signals(data, args.week, instruments)
    if res.errors:
        print("Signalfilen klarar inte valideringen – skrivs inte:", *res.errors, sep="\n  ", file=sys.stderr)
        return 1
    out = args.out or DATA_DIR / "signals" / f"{args.week}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    flagged = [c for c in data["companies"] if c["signals"]]
    counts = {s: sum(s in c["signals"] for c in data["companies"])
              for s in ("insyn", "blankning", "avviker", "stor-rörelse", "omsättning", "pe-upp", "52v-högsta", "52v-lägsta")}
    print(f"Signaler {args.week}: {with_prices} bolag med kurs, {len(flagged)} med signal "
          f"({', '.join(f'{k} {v}' for k, v in counts.items())}), {len(data['errors'])} utan kurs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
