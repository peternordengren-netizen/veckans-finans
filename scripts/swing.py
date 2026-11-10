"""Swing: lägesmått och beskrivande etiketter per bolag ur dagsdata, uppdaterat varje morgon.

Skriver data/swing/<datum>.json (schema/swing.schema.json). Ett informationsverktyg: måtten och etiketterna
beskriver vad som hänt fram till förra handelsdagens stängning och är inga uppmaningar. Historiken per etikett
(scripts/swing_history.py, data/swing/history.json) visar hur ofta etiketten följts av att aktien gått bättre
än index, jämfört med alla dagar.

Körs av scripts/morning.py med samma dagsdata som morgonrapporten (ingen extra hämtning), eller fristående.

Lägesmått per bolag (senaste handelsdagen, justerad stängning; högsta/lägsta justeras likadant):
- mot 20- och 50-dagars glidande medelvärde, i procent
- RSI(2) och RSI(14) (Wilders utjämning)
- ATR(14) i procent av kursen
- omsättning mot snittet för de 20 handelsdagarna före
- handelsdagar sedan senaste rapporten (ur rapportutfallen, data/results/) och utfallet mot förväntan
- sammanlagd blankning ur senaste ögonblicksbilden av FI:s register, där den finns

Etiketter (LABELS, trösklar i THRESHOLDS):
- "utbrott-omsattning" / "utbrott": stängning över 20-dagarshögsta (högsta under de 20 dagarna före),
  med respektive utan omsättning minst volume_ratio gånger snittet
- "rsi2-under-10" / "rsi2-over-90"
- "trend-upp": kurs över 20-dagars MA och 20-dagars över 50-dagars; "trend-ned" omvänt
Stöd och motstånd per bolag ("sr", scripts/levels.py): närmaste stöd under och motstånd över gårdagens stängning
(zoner av tidigare vändpunkter de senaste 120 dagarna, och 20 EMA, 50 och 200 MA), avstånd i procent och ATR,
triggernivå och stopp under stödet. Fliken har ett enda R, strategins (se nedan); motståndet i R räknas med det.
- "golden-cross" / "death-cross": 50 dagars MA har korsat upp över (ned under) 200 dagars MA de senaste 10
  handelsdagarna; ma_cross_days = handelsdagar sedan korsningen (0 = gårdagen)
- "bollinger-ovre": stängningen vid eller över övre Bollingerbandet (20 dagars stängningar, 2 standardavvikelser;
  bb_upper_pct = stängningen mot bandet i procent)
- "efter-rapport-over" / "efter-rapport-under" / "efter-rapport": rapport inom report_days handelsdagar
  (reaktionsdagen räknas som dag 0), med utfallet mot förväntan för vinst per aktie där det finns
- "rekyl-i-trend" / "rekyl-vandsignal": strategin "Pullback i trend" (scripts/pullback.py), med
  stopp-, trigger-, topp-, 2R- och 3R-nivåer per bolag ("pullback") och strategin i "strategies". För
  kandidaterna också gårdagens stängning mot Bollingerbanden ("pullback.bollinger": %B, övre bandet, avståndet
  dit i R och om stängningen är vid eller över), som bollinger.py uppdaterar under dagen

- "bbrekyl" / "bbrekyl-vandsignal": strategin "Rekyl efter Bollingerutbrott" (scripts/bbrekyl.py), med fönstret
  ("bbrekyl.state"), nivåerna, utbrottsdagen (och om den var en rapportdag, med utfallet mot förväntan), Bollinger-
  läget och rangen per bolag ("bbrekyl"), som för Pullback

Bolag som saknas i tabellen står i "missing" (Yahoo saknar kursdata, eller för kort historik) och "delisted"
(avnoterade enligt instruments.csv, med sista handelsdag och källa; hoppas över helt), så att sidan kan visa dem.

Användning:
    python scripts/swing.py                 # i dag (förra handelsdagens stängning)
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

import bbrekyl  # noqa: E402
import levels  # noqa: E402
import pullback  # noqa: E402
import ranking  # noqa: E402
from bollinger import bands, position, upper_band  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from signals import Bar, BarFetcher, data_day, yahoo_bars  # noqa: E402
from workdays import non_business_days  # noqa: E402


def previous_business_day(d: date) -> date:
    """Förra handelsdagen (som morning.previous_business_day; morning importeras inte här)."""
    d -= timedelta(days=1)
    while d.weekday() >= 5 or d in non_business_days(d.year):
        d -= timedelta(days=1)
    return d
from validate import DATA_DIR, RAW_DIR, Instrument, listed, load_instruments, validate_swing  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
SWING_DIR = DATA_DIR / "swing"
MORNING_RAW = DATA_DIR / "morning" / "raw"
RESULTS_DIR = DATA_DIR / "results"
HISTORY_DAYS = 400  # samma hämtning som morgonrapporten
MIN_BARS = 51
MARKET_CLOSE = time(17, 30)
THRESHOLDS = {"volume_ratio": 2.0, "rsi2_low": 10.0, "rsi2_high": 90.0, "report_days": 5}
CROSS_DAYS = 10  # golden/death cross: korsningen högst så många handelsdagar bakåt (gårdagen = dag 0)
LABELS = {
    "utbrott-omsattning": "Utbrott, hög omsättning",
    "utbrott": "Utbrott",
    "rsi2-under-10": "RSI(2) under 10",
    "rsi2-over-90": "RSI(2) över 90",
    "trend-upp": "Trend upp",
    "trend-ned": "Trend ned",
    "efter-rapport-over": "Efter rapport, över förväntan",
    "efter-rapport-under": "Efter rapport, under förväntan",
    "efter-rapport": "Efter rapport",
    "bollinger-ovre": "Vid övre Bollingerband",
    "golden-cross": "Golden cross",
    "death-cross": "Death cross",
    **pullback.LABELS,
    **bbrekyl.LABELS,
}
# Strategier i rullistan: namn och etiketterna som räknas som träff (scripts/pullback.py).
STRATEGIES = {
    "pullback": {"name": "Pullback i trend", "labels": list(pullback.LABELS)},
    "bbrekyl": {"name": "Rekyl efter Bollingerutbrott", "labels": list(bbrekyl.LABELS)},
}


# --- Lägesmått -------------------------------------------------------------------


def _wilder(values: list[float], n: int) -> list[float | None]:
    """Wilders glidande medelvärde (första värdet = enkelt medel av de n första)."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    avg = sum(values[:n]) / n
    out[n - 1] = avg
    for i in range(n, len(values)):
        avg = (avg * (n - 1) + values[i]) / n
        out[i] = avg
    return out


def rsi(closes: list[float], n: int) -> list[float | None]:
    """RSI(n) per dag; None tills n förändringar finns."""
    ups = [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    downs = [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]
    au, ad = _wilder(ups, n), _wilder(downs, n)
    out: list[float | None] = [None]
    for u, d in zip(au, ad):
        out.append(None if u is None else 100.0 if d == 0 else 100 - 100 / (1 + u / d))
    return out


def series(bars: list[Bar]) -> list[dict | None]:
    """Lägesmått för varje dag i serien (None innan MIN_BARS dagar finns)."""
    f = [b.adj_close / b.close if b.close else 1.0 for b in bars]
    c = [b.adj_close for b in bars]
    h = [b.high * k for b, k in zip(bars, f)]
    lo = [b.low * k for b, k in zip(bars, f)]
    v = [b.volume for b in bars]
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(bars))]
    atr, rsi2, rsi14 = _wilder(tr, 14), rsi(c, 2), rsi(c, 14)
    cross = ma_crosses(c)
    out: list[dict | None] = []
    for i in range(len(bars)):
        if i < MIN_BARS - 1:
            out.append(None)
            continue
        ma20, ma50 = sum(c[i - 19:i + 1]) / 20, sum(c[i - 49:i + 1]) / 50
        base_v = v[i - 20:i]
        avg_v = sum(base_v) / len(base_v)
        out.append({
            "day": bars[i].day,
            "close": c[i],
            "ma20_pct": round((c[i] / ma20 - 1) * 100, 2),
            "ma50_pct": round((c[i] / ma50 - 1) * 100, 2),
            "ma20_over_ma50": ma20 > ma50,
            "rsi2": round(rsi2[i], 1) if rsi2[i] is not None else None,
            "rsi14": round(rsi14[i], 1) if rsi14[i] is not None else None,
            "atr_pct": round(atr[i] / c[i] * 100, 2) if atr[i] is not None and c[i] else None,
            "volume_ratio": round(v[i] / avg_v, 2) if avg_v > 0 else None,
            "above_high20": c[i] > max(h[i - 20:i]),
            # Gårdagens stängning mot övre Bollingerbandet (20 dagar, 2 standardavvikelser, scripts/bollinger.py).
            "bb_upper_pct": round((c[i] / upper_band(c[:i + 1]) - 1) * 100, 2),
            # Senaste korsningen mellan 50 och 200 dagars MA inom CROSS_DAYS dagar: "golden" (upp), "death" (ned).
            "ma_cross": cross[i][0] if cross[i] else None,
            "ma_cross_days": cross[i][1] if cross[i] else None,
        })
    return out


def ma_crosses(c: list[float], days: int = CROSS_DAYS) -> list[tuple[str, int] | None]:
    """Per dag i: ("golden" | "death", dagar sedan) för den senaste korsningen mellan 50 och 200 dagars MA
    under dagarna i−days+1 … i, eller None. Korsning på dag j: 50 MA över 200 MA dag j men inte dag j−1
    (golden), eller omvänt (death). Kräver 201 dagar (200 MA både dag j och dag j−1)."""
    pre = [0.0]
    for x in c:
        pre.append(pre[-1] + x)
    above: list[bool | None] = [None] * len(c)
    for j in range(199, len(c)):
        above[j] = (pre[j + 1] - pre[j - 49]) / 50 > (pre[j + 1] - pre[j - 199]) / 200
    out: list[tuple[str, int] | None] = [None] * len(c)
    last: tuple[str, int] | None = None  # (slag, dag) för senaste korsningen
    for j in range(len(c)):
        if j >= 200 and above[j] is not None and above[j - 1] is not None and above[j] != above[j - 1]:
            last = ("golden" if above[j] else "death", j)
        if last and j - last[1] < days:
            out[j] = (last[0], j - last[1])
    return out


def technical_labels(m: dict, th: dict = THRESHOLDS) -> list[str]:
    """Etiketterna ur kurs och omsättning (används även av historiken och valideringen)."""
    out = []
    if m["above_high20"]:
        out.append("utbrott-omsattning" if (m["volume_ratio"] or 0) >= th["volume_ratio"] else "utbrott")
    if m["rsi2"] is not None and m["rsi2"] < th["rsi2_low"]:
        out.append("rsi2-under-10")
    if m["rsi2"] is not None and m["rsi2"] > th["rsi2_high"]:
        out.append("rsi2-over-90")
    if m["ma20_pct"] > 0 and m["ma20_over_ma50"]:
        out.append("trend-upp")
    if m["ma20_pct"] < 0 and not m["ma20_over_ma50"]:
        out.append("trend-ned")
    if m.get("bb_upper_pct") is not None and m["bb_upper_pct"] >= 0:  # saknas i äldre filer
        out.append("bollinger-ovre")
    if m.get("ma_cross") == "golden":  # saknas i äldre filer
        out.append("golden-cross")
    if m.get("ma_cross") == "death":
        out.append("death-cross")
    return out


def report_label(days_since: int | None, verdict: str | None, th: dict = THRESHOLDS) -> str | None:
    if days_since is None or not 0 <= days_since < th["report_days"]:
        return None
    return {"över": "efter-rapport-over", "under": "efter-rapport-under"}.get(verdict or "", "efter-rapport")


# --- Rapporter och blankning ---------------------------------------------------------


def reaction_day(published: datetime) -> date:
    """Första handelsdag som kan reagera: publiceringsdagen före 17:30, annars dagen efter."""
    local = published.astimezone(STOCKHOLM)
    return local.date() if local.time() < MARKET_CLOSE else local.date() + timedelta(days=1)


def latest_reports(today: date, results_dir: Path = RESULTS_DIR) -> dict[str, dict]:
    """Senaste rapporten per bolag ur rapportutfallen (morgon, uppdateringar och veckor), publicerad före today."""
    out: dict[str, dict] = {}
    files = list(results_dir.glob("*.json")) + list(results_dir.glob("morning/*.json")) + \
        list(results_dir.glob("updates/*.json"))
    for p in files:
        try:
            reports = json.loads(p.read_text(encoding="utf-8")).get("reports", [])
        except (OSError, ValueError):
            continue
        for r in reports:
            pub = datetime.fromisoformat(r["published"])
            if pub.date() >= today:
                continue
            eps = (r.get("vs_consensus") or {}).get("eps")
            cur = out.get(r["ticker"])
            if cur is None or pub > datetime.fromisoformat(cur["published"]):
                out[r["ticker"]] = {"published": r["published"], "verdict": eps["verdict"] if eps else None}
    return out


def short_levels(today: date, instruments: dict[str, Instrument],
                 raw_dirs: tuple[Path, ...] = (RAW_DIR, MORNING_RAW)) -> tuple[str | None, dict[str, float]]:
    """Sammanlagd blankning per bolag ur senaste ögonblicksbilden före today: (datum, {ticker: procent})."""
    from registers import _aggregate_by_ticker

    files = {}
    for raw in raw_dirs:
        for p in raw.glob("*/fi/blankning-*.json"):
            d = p.stem.removeprefix("blankning-")
            if d < today.isoformat():
                files[d] = p
    if not files:
        return None, {}
    day = max(files)
    snap = json.loads(files[day].read_text(encoding="utf-8"))
    isin_to_ticker = {isin: t for t, i in instruments.items() for isin in i.isin}
    return day, {t: round(v, 2) for t, v in _aggregate_by_ticker(snap, instruments, isin_to_ticker).items()}


# --- Sammanställning -------------------------------------------------------------------


def priced(instruments: dict[str, Instrument], today: date) -> dict[str, Instrument]:
    """Bolagen som kurserna hämtas och räknas för: noterade förra handelsdagen (avnoterade hoppas över)."""
    return listed(instruments, previous_business_day(today))


def build(today: date, now: datetime, instruments: dict[str, Instrument], bars: dict[str, list[Bar]],
          reports: dict[str, dict], shorts: tuple[str | None, dict[str, float]]) -> dict:
    short_day, short_pct = shorts
    active = priced(instruments, today)
    delisted = [{"ticker": t, "name": i.namn, "last_day": i.avnoterad.isoformat(), "source": i.avnoterad_kalla}
                for t, i in sorted(instruments.items(), key=lambda x: x[1].namn) if t not in active]
    instruments = active
    # Dagsdatans datum (signals.data_day), inte det senaste datumet hos något bolag: har bara ett fåtal bolag
    # gårdagens rad (Yahoo kl. 08:10) gäller måtten dagen före, för alla bolag, och sidan varnar.
    as_of, with_day, of = data_day(bars, [i.yahoo_ticker for i in instruments.values()], today)
    expected = previous_business_day(today)
    with_expected = sum(1 for i in instruments.values() if any(x.day == expected for x in bars.get(i.yahoo_ticker, [])))
    companies, missing = [], []
    for t, inst in instruments.items():
        b = [x for x in bars.get(inst.yahoo_ticker, []) if as_of is not None and x.day <= as_of]
        if len(b) < MIN_BARS:
            # Visas på sidan i stället för att bolaget bara saknas.
            missing.append({"ticker": t, "name": inst.namn, "reason": "no_data" if not b else "short_history",
                            "bars": len(b)})
            continue
        m = series(b)[-1]
        rep = reports.get(t)
        days_since = None
        if rep:
            # Reaktionsdagen = första handelsdagen på eller efter reaction_day (dag 0).
            r0 = reaction_day(datetime.fromisoformat(rep["published"]))
            after = [x for x in b if x.day >= r0]
            days_since = len(after) - 1 if after else None
        labels = technical_labels(m)
        rl = report_label(days_since, rep["verdict"] if rep else None)
        if rl:
            labels.append(rl)
        # Pullback i trend (kräver 200 handelsdagar): tillståndet sparas för valideringen, nivåerna vid träff.
        o, h, lo, c = pullback.adjusted(b)
        i = len(b) - 1
        pb_state = pullback.state(o, h, lo, c, pullback.ema(c, 20), i)
        pb_labels = pullback.labels(pb_state)
        labels += pb_labels
        atr = m["atr_pct"] / 100 * c[i] if m["atr_pct"] is not None else None
        pb_levels = pullback.levels(h, lo, atr, i, pb_state) if pb_labels else None
        pb = {"state": pb_state, "levels": pb_levels}
        if pb_levels is not None:
            # Gårdagens stängning mot Bollingerbanden (bollinger.position); uppdateras under dagen av bollinger.py.
            pb["bollinger"] = position(c[i], *bands(c[:i + 1]), pb_levels["ref"], pb_levels["stop"])
        # Stöd och motstånd (scripts/levels.py), för alla bolag.
        e20 = pullback.ema(c, 20)[i]
        ma50 = sum(c[i - 49:i + 1]) / 50
        ma200 = sum(c[i - 199:i + 1]) / 200 if i >= 199 else None
        sr = levels.summary(h, lo, c[i], i, atr, e20, ma50, ma200)
        if pb_levels is not None:
            # Rangordningen (scripts/ranking.py): mätvärden och uppfyllda kriterier; rangen sätts efter loopen.
            zones_sr = levels.summary(h, lo, c[i], i, atr, None, None, None, zones_only=True)
            zsup = (zones_sr or {}).get("support")
            rst = (sr or {}).get("resistance")
            meas = ranking.measures(
                pb_levels["signal"] is not None, pb_levels["ref"], pb_levels["stop"],
                levels.resistance_edge(rst) if rst else None, zsup["dist_atr"] if zsup else None,
                pb["bollinger"]["upper_r"], pb_levels["risk_atr"], c, i)
            crit = ranking.criteria(meas)
            pb["rank"] = {"rank": None, "score": sum(crit.values()), "criteria": crit, "measures": meas}
        bb = bb_strategy(b, o, h, lo, c, i, atr, rep)
        companies.append({
            "ticker": t, "name": inst.namn, "sector": inst.sektor, "day": m["day"].isoformat(),
            **{k: m[k] for k in ("ma20_pct", "ma50_pct", "ma20_over_ma50", "rsi2", "rsi14", "atr_pct", "volume_ratio",
                                 "above_high20", "bb_upper_pct", "ma_cross", "ma_cross_days")},
            "report_published": rep["published"] if rep else None,
            "days_since_report": days_since,
            "report_verdict": rep["verdict"] if rep else None,
            "short_pct": short_pct.get(t),
            "pullback": pb,
            "bbrekyl": bb,
            "sr": sr,
            "labels": labels + bbrekyl.labels(bb["state"]),
        })
    companies.sort(key=lambda c: c["name"])
    ranked = [{**c["pullback"]["rank"], "name": c["name"], "_c": c} for c in companies if c["pullback"].get("rank")]
    ranking.rank(ranked)
    for e in ranked:
        e["_c"]["pullback"]["rank"]["rank"] = e["rank"]
    bb_ranked = [{**c["bbrekyl"]["rank"], "name": c["name"], "_c": c} for c in companies if c["bbrekyl"].get("rank")]
    bbrekyl.rank(bb_ranked)
    for e in bb_ranked:
        e["_c"]["bbrekyl"]["rank"]["rank"] = e["rank"]
    return {
        "schema_version": 1,
        "date": today.isoformat(),
        "as_of": as_of.isoformat() if as_of else None,
        # Förra handelsdagen; skiljer den sig från as_of gäller måtten en äldre dag (varning på sidan).
        "expected_day": expected.isoformat(),
        "coverage": {"with_day": with_day, "with_expected": with_expected, "of": of},
        "generated_at": now.isoformat(timespec="seconds"),
        "thresholds": {**THRESHOLDS, "pullback": pullback.THRESHOLDS, "levels": levels.THRESHOLDS,
                       "ranking": dict(ranking.THRESHOLDS),
                       "bbrekyl": {**bbrekyl.THRESHOLDS, "ranking": dict(bbrekyl.RANK_THRESHOLDS)}},
        "ranking_criteria": dict(ranking.CRITERIA),
        "bbrekyl_criteria": dict(bbrekyl.CRITERIA),
        "labels": dict(LABELS),  # en kopia: filen får inte dela modulens dict
        "strategies": {k: {**v, "labels": list(v["labels"])} for k, v in STRATEGIES.items()},  # kopia, som labels
        "short_as_of": short_day,
        "companies": companies,
        "missing": sorted(missing, key=lambda m: m["name"]),
        "delisted": delisted,
    }


def bb_strategy(b: list[Bar], o: list[float], h: list[float], lo: list[float], c: list[float], i: int,
                atr: float | None, rep: dict | None) -> dict:
    """Rekyl efter Bollingerutbrott (scripts/bbrekyl.py) för ett bolag: fönstret, och vid träff nivåerna, utbrottsdagens
    rapport (reaktionsdagen = utbrottsdagen, med utfallet mot förväntan), Bollingerläget och rangens mätvärden."""
    state = bbrekyl.window(o, h, lo, c, [x.volume for x in b], pullback.ema(c, 20), i) if len(b) >= bbrekyl.MIN_BARS \
        else None
    out: dict = {"state": state, "levels": None}
    if not bbrekyl.labels(state):
        return out
    lv = bbrekyl.levels(state, atr)
    day = b[i - lv["breakout"]["days_ago"]].day
    lv["breakout"]["day"] = day.isoformat()
    report = None
    if rep and reaction_day(datetime.fromisoformat(rep["published"])) <= day:
        # Reaktionsdagen = första handelsdagen på eller efter reaction_day; utbrottsdagen om ingen dag ligger emellan.
        r0 = reaction_day(datetime.fromisoformat(rep["published"]))
        if min((x.day for x in b if x.day >= r0), default=None) == day:
            report = {"published": rep["published"], "verdict": rep["verdict"]}
    lv["breakout"]["report"] = report
    out["levels"] = lv
    out["bollinger"] = position(c[i], *bands(c[:i + 1]), lv["ref"], lv["stop"])
    meas = bbrekyl.measures(lv, out["bollinger"]["upper_r"], c, i)
    crit = bbrekyl.criteria(meas)
    out["rank"] = {"rank": None, "score": sum(crit.values()), "criteria": crit, "measures": meas}
    return out


def write(data: dict, instruments: dict[str, Instrument]) -> Path | None:
    """Validerar och skriver data/swing/<datum>.json; None (med fel på stderr) om valideringen underkänner."""
    res = validate_swing(data, instruments)
    if res.errors:
        print("Swing klarar inte valideringen – skrivs inte:", *res.errors[:10], sep="\n  ", file=sys.stderr)
        return None
    out = SWING_DIR / f"{data['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def summary(data: dict, out: Path) -> str:
    from collections import Counter

    n = Counter(lbl for c in data["companies"] for lbl in c["labels"])
    parts = ", ".join(f"{k} {n[k]}" for k in LABELS if n[k])
    return f"Swing {data['date']} (stängning {data['as_of']}): {len(data['companies'])} bolag; {parts} -> {out}"


def main(argv: list[str] | None = None, fetch: BarFetcher = yahoo_bars, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=date.fromisoformat, help="datum (standard: i dag)")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    today = args.date or now.date()
    instruments = load_instruments()
    tickers = [i.yahoo_ticker for i in priced(instruments, today).values()]
    bars = fetch([*tickers, INDEX_TICKER], today - timedelta(days=HISTORY_DAYS), today - timedelta(days=1))
    data = build(today, now, instruments, bars, latest_reports(today), short_levels(today, instruments))
    out = write(data, instruments)
    if out is None:
        return 1
    print(summary(data, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
