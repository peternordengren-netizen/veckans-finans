"""Historik per swingetikett: hur ofta har aktien gått bättre än index 2 och 5 handelsdagar efter att
etiketten gällt, jämfört med alla aktie-dagar. Skriver data/swing/history.json, som visas under tabellen
i fliken Swing.

Samma lägesmått och etiketter som scripts/swing.py (series, technical_labels), på de senaste TEST_DAYS
handelsdagarna där kursen 5 dagar framåt finns. Överavkastning = aktiens förändring − indexets
(OMX Stockholm PI), justerad stängning. Bara aktier i kronor (.ST), dagens bevakningslista (bolag som
avnoterats under året saknas). Courtage och spread ingår inte.

Rapportetiketterna bygger på rapportutfallen i data/results/ (utfall mot förväntan finns bara sedan
oktober 2026) och räknas på reaktionsdagen: få fall, visas med antal.

Ett 95-procentsintervall (±) räknas som om fallen vore oberoende; de infaller ofta samma dagar, så den
verkliga osäkerheten är större.

Pullback i trend (scripts/pullback.py): varje dag med etiketten "Rekyl i trend" eller "Rekyl med vändsignal"
under samma år följs fram till att 2R eller stoppet nås (högst 60 handelsdagar), ett fall i taget per bolag.
Andelen 2R först och stopp först räknas på de avgjorda fallen, med snittresultat efter courtage 0,2 % per affär.
Dagsdatan hämtas med öppningskurser i samma batchanrop.

Användning:
    python scripts/swing_history.py                    # hämtar dagsdata (ett batchanrop till Yahoo)
    python scripts/swing_history.py --cache bars.pkl   # återanvänder en sparad hämtning
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pullback  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from signals import yahoo_bars  # noqa: E402
from swing import LABELS, SWING_DIR, THRESHOLDS, latest_reports, reaction_day, report_label, series, technical_labels  # noqa: E402
from validate import load_instruments  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
TEST_DAYS = 252
# 200 dagars MA (Pullback i trend) ska finnas redan vid årets början: cirka 200 + 252 handelsdagar.
FETCH_DAYS = 760
HORIZONS = (2, 5)


def _stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0, "better": None, "ci": None, "mean": None}
    p = sum(v > 0 for v in values) / n
    return {"n": n, "better": round(p, 4), "ci": round(1.96 * math.sqrt(p * (1 - p) / n), 4) if n > 1 else None,
            "mean": round(sum(values) / n, 5)}


def build(bars: dict, instruments: dict, today: date, results_dir: Path | None = None) -> dict:
    idx = {b.day: b.adj_close for b in bars.get(INDEX_TICKER, [])}
    days = sorted(idx)
    end_i = len(days) - 1 - max(HORIZONS)
    start_day, end_day = days[max(0, end_i - TEST_DAYS + 1)], days[end_i]
    base: dict[int, list[float]] = defaultdict(list)
    per_label: dict[str, dict[int, list[float]]] = {k: defaultdict(list) for k in LABELS}
    pb_cases: dict[str, list[dict]] = {k: [] for k in pullback.LABELS}
    reports = latest_reports(today, results_dir) if results_dir else latest_reports(today)
    report_days: dict[str, date] = {}
    for t, r in reports.items():
        report_days[t] = reaction_day(datetime.fromisoformat(r["published"]))
    for t, inst in instruments.items():
        if not inst.yahoo_ticker.endswith(".ST") or inst.yahoo_ticker not in bars:
            continue
        b = bars[inst.yahoo_ticker]
        ms = series(b)
        closes = [x.adj_close for x in b]
        rday = report_days.get(t)
        reacted = False
        o, h, lo, c = pullback.adjusted(b)
        e20 = pullback.ema(c, 20)
        busy_until = -1  # ett pullback-fall i taget per bolag
        for i, m in enumerate(ms):
            # Pullback i trend: utfall mot stopp och 2R, oberoende av indexjämförelsen nedan (som kräver 5 dagar framåt).
            if m is not None and start_day <= m["day"] <= end_day and i > busy_until:
                st = pullback.state(o, h, lo, c, e20, i)
                pls = pullback.labels(st)
                if pls:
                    stop = pullback.stop_level(h, lo, i, pullback.touched(st)[1])
                    case = pullback.simulate(o, h, lo, i, st, stop)
                    pb_cases[pls[0]].append(case)
                    busy_until = case["end"]
            if m is None or not start_day <= m["day"] <= end_day or i + max(HORIZONS) >= len(b):
                continue
            fwd = {}
            for k in HORIZONS:
                d0, dk = b[i].day, b[i + k].day
                if d0 not in idx or dk not in idx:
                    break
                fwd[k] = (closes[i + k] / closes[i] - 1) - (idx[dk] / idx[d0] - 1)
            if len(fwd) < len(HORIZONS):
                continue
            labels = technical_labels(m) + pullback.labels(pullback.state(o, h, lo, c, e20, i))
            # Rapportetiketten räknas en gång per rapport, på reaktionsdagen (dag 0).
            if rday and not reacted and m["day"] >= rday:
                reacted = True
                rl = report_label(0, reports[t]["verdict"])
                if rl:
                    labels.append(rl)
            for k, x in fwd.items():
                base[k].append(x)
                for lbl in labels:
                    per_label[lbl][k].append(x)
    return {
        "generated_at": datetime.now(STOCKHOLM).isoformat(timespec="seconds"),
        "period": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        "horizons": list(HORIZONS),
        "thresholds": THRESHOLDS,
        "method": "Andel fall där aktien gått bättre än OMX Stockholm PI efter 2 och 5 handelsdagar (justerad "
                  "stängning), jämfört med alla aktie-dagar. Dagens bevakningslista, bara aktier i kronor, utan "
                  "courtage och spread. Rapportetiketterna bygger på rapportutfallen sedan oktober 2026.",
        "base": {str(k): _stats(base.get(k, [])) for k in HORIZONS},
        # Pullback i trend: hur ofta 2R nåddes före stoppet och tvärtom, efter courtage (scripts/pullback.py).
        "pullback": {
            "thresholds": pullback.THRESHOLDS,
            "labels": {k: pullback.summarize(v) for k, v in pb_cases.items()},
            "all": pullback.summarize([x for v in pb_cases.values() for x in v]),
        },
        "labels": {lbl: {str(k): _stats(per_label[lbl].get(k, [])) for k in HORIZONS} for lbl in LABELS},
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, help="pickle med {yahoo_ticker: [Bar]} från en tidigare hämtning")
    args = p.parse_args(argv)
    today = datetime.now(STOCKHOLM).date()
    instruments = load_instruments()
    if args.cache and args.cache.exists():
        bars = pickle.loads(args.cache.read_bytes())
    else:
        tickers = [i.yahoo_ticker for i in instruments.values() if i.yahoo_ticker.endswith(".ST")]
        bars = yahoo_bars([*tickers, INDEX_TICKER], today - timedelta(days=FETCH_DAYS), today - timedelta(days=1))
    data = build(bars, instruments, today)
    out = SWING_DIR / "history.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    b5 = data["base"]["5"]
    print(f"Swinghistorik {data['period']['start']}–{data['period']['end']}: {b5['n']} aktie-dagar, "
          f"bättre än index efter 5 d {b5['better'] * 100:.1f} % -> {out}")
    for lbl, s in data["labels"].items():
        s5 = s.get("5", {})
        if s5.get("n"):
            print(f"  {LABELS[lbl]}: n={s5['n']}, bättre efter 5 d {s5['better'] * 100:.1f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
