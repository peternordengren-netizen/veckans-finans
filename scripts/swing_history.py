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
Andelen 2R först och stopp först räknas på de avgjorda fallen, med snittresultat utan courtage.
Dagsdatan hämtas med öppningskurser i samma batchanrop.

Rekyl efter Bollingerutbrott (scripts/bbrekyl.py): samma uppföljning av varje dag med strategins etiketter, med
rekylens lägsta low som stopp ("bbrekyl" i filen).

Momentum-utbrott (VCP, scripts/vcp.py): de senaste vcp.HISTORY_YEARS åren (längre än övriga), varje dag med
etiketten, ett fall i taget per bolag och etikett: hur ofta 3R nåddes före stoppet, och utvecklingen mot index efter 5, 20 och 60
handelsdagar från signaldagens stängning ("vcp" i filen). RS-percentilen räknas mot listans bolag samma dag. Volymvillkoren prövas också var för sig ("bbrekyl.rules"):
grundregeln (utbrott med 1,5× omsättning, inga övriga volymvillkor), grundregeln plus ett villkor i taget, och alla
tillsammans (= strategins etiketter), varje variant med sina egna fall (ett i taget per bolag).

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

import bbrekyl  # noqa: E402
import vcp  # noqa: E402
import levels  # noqa: E402
import pullback  # noqa: E402
import ranking  # noqa: E402
from bollinger import bands, position  # noqa: E402
from prices import INDEX_TICKER  # noqa: E402
from signals import yahoo_bars  # noqa: E402
from swing import LABELS, SWING_DIR, THRESHOLDS, latest_reports, reaction_day, report_label, series, technical_labels  # noqa: E402
from validate import listed, load_instruments  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
TEST_DAYS = 252
# 200 dagars MA (Pullback i trend) ska finnas redan vid årets början: cirka 200 + 252 handelsdagar. Momentum-utbrott
# (VCP) testas på vcp.HISTORY_YEARS (3) år och behöver 12 månaders utveckling före första dagen: cirka 4 år.
FETCH_DAYS = 1500
VCP_HORIZONS = (5, 20, 60)
HORIZONS = (2, 5)


def _stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0, "better": None, "ci": None, "mean": None}
    p = sum(v > 0 for v in values) / n
    return {"n": n, "better": round(p, 4), "ci": round(1.96 * math.sqrt(p * (1 - p) / n), 4) if n > 1 else None,
            "mean": round(sum(values) / n, 5)}


def sr_ok(h: list[float], lo: list[float], c: list[float], i: int, m: dict, st: dict, stop: float) -> bool:
    """Stöd nära och motstånd långt bort för ett Pullback-fall på dag i (se "sr_filter" i build).
    Stödet räknas bara med zoner: rekylen till 20 EMA eller 50 MA ger annars alltid ett stöd nära."""
    atr = m["atr_pct"] / 100 * c[i] if m["atr_pct"] is not None else None
    zones_only = levels.summary(h, lo, c[i], i, atr, None, None, None, zones_only=True)
    full = levels.summary(h, lo, c[i], i, atr, st["ema20"], st["ma50"], st["ma200"])
    if zones_only is None or full is None:
        return False
    sup = zones_only["support"]
    if sup is None or sup["dist_atr"] is None or sup["dist_atr"] > 1.0:
        return False
    ref = st["high"] if pullback.signal(st) else st["close"]
    risk = ref - stop
    res = full["resistance"]
    return res is None or (risk > 0 and res["low"] - ref >= 2 * risk)


def rank_measures(h: list[float], lo: list[float], c: list[float], i: int, m: dict, st: dict) -> dict | None:
    """Rangordningens mätvärden (scripts/ranking.py) för ett Pullback-fall på dag i, som swing.py räknar dem."""
    atr = m["atr_pct"] / 100 * c[i] if m["atr_pct"] is not None else None
    lv = pullback.levels(h, lo, atr, i, st)
    full = levels.summary(h, lo, c[i], i, atr, st["ema20"], st["ma50"], st["ma200"])
    zones_only = levels.summary(h, lo, c[i], i, atr, None, None, None, zones_only=True)
    if full is None or zones_only is None:
        return None
    bb = bands(c[:i + 1])
    upper_r = position(c[i], *bb, lv["ref"], lv["stop"])["upper_r"] if bb else None
    rst, zsup = full["resistance"], zones_only["support"]
    return ranking.measures(lv["signal"] is not None, lv["ref"], lv["stop"],
                            levels.resistance_edge(rst) if rst else None, zsup["dist_atr"] if zsup else None,
                            upper_r, lv["risk_atr"], c, i)


def market_filter(index_bars: list) -> dict[date, bool]:
    """Per dag: ligger indexets (justerade) stängning över både 50- och 200-dagars MA? Dagar utan 200 dagars
    historik saknas (räknas som inte uppfyllt)."""
    closes = [b.adj_close for b in index_bars]
    out = {}
    for i in range(199, len(closes)):
        ma50, ma200 = sum(closes[i - 49:i + 1]) / 50, sum(closes[i - 199:i + 1]) / 200
        out[index_bars[i].day] = closes[i] > ma50 and closes[i] > ma200
    return out


def _vcp_stats(values: list[float]) -> dict:
    """_stats med 95 % konfidensintervall (Wilson) för andelen bättre än index, [låg, hög] ("wilson")."""
    out = _stats(values)
    out["wilson"] = list(vcp.wilson(sum(v > 0 for v in values), len(values))) if values else None
    return out


def vcp_history(bars: dict, instruments: dict) -> dict:
    """Momentum-utbrott (VCP) de senaste vcp.HISTORY_YEARS åren: fall (3R eller stoppet först) och utvecklingen mot
    index efter VCP_HORIZONS dagar, per etikett och totalt (se modulbeskrivningen)."""
    idx = {b.day: b.adj_close for b in bars.get(INDEX_TICKER, [])}
    days = sorted(idx)
    if len(days) < 2:
        return {}
    start_day = days[max(0, len(days) - 252 * vcp.HISTORY_YEARS)]
    data = {}
    for t, inst in instruments.items():
        b = bars.get(inst.yahoo_ticker)
        if not inst.yahoo_ticker.endswith(".ST") or not b or len(b) < vcp.MIN_BARS:
            continue
        o, h, lo, c = pullback.adjusted(b)
        data[t] = (b, o, h, lo, c, [x.volume for x in b])
    # RS: viktad utveckling per bolag och dag, sedan percentilen mot alla bolag samma dag.
    by_day: dict[date, list[float]] = defaultdict(list)
    scores: dict[str, dict[int, float]] = {}
    for t, (b, o, h, lo, c, v) in data.items():
        sc = {}
        for i in range(252, len(c)):
            if b[i].day >= start_day:
                s = vcp.rs_score(c, i)
                if s is not None:
                    sc[i] = s
                    by_day[b[i].day].append(s)
        scores[t] = sc
    for lst in by_day.values():
        lst.sort()
    cases: dict[str, list[dict]] = {k: [] for k in vcp.LABELS}
    fwd: dict[str, dict[int, list[float]]] = {k: defaultdict(list) for k in vcp.LABELS}
    for t, (b, o, h, lo, c, v) in data.items():
        pre = [0.0]
        for x in c:
            pre.append(pre[-1] + x)
        ma = lambda i, n: (pre[i + 1] - pre[i + 1 - n]) / n  # noqa: E731
        # Ett fall i taget per bolag och etikett: ett pågående "Nära pivot"-fall spärrar inte ett utbrott.
        busy = {k: -1 for k in vcp.LABELS}
        for i, s in scores[t].items():
            if i <= min(busy.values()):
                continue
            pct = vcp.percentile(s, by_day[b[i].day])
            # Snabbt förfilter (samma villkor som vcp.labels): RS och MA-ordningen, innan måtten räknas.
            if pct < vcp.THRESHOLDS["rs_min"] or not c[i] > ma(i, 50) > ma(i, 150) > ma(i, 200):
                continue
            st = vcp.state(o, h, lo, c, v, i, pct)
            lbls = vcp.labels(st)
            if not lbls or i <= busy[lbls[0]]:
                continue
            lv = vcp.levels(st, lbls[0], None)
            case = vcp.simulate(o, h, lo, i, lbls[0], lv)
            cases[lbls[0]].append(case)
            busy[lbls[0]] = case["end"]
            d0 = b[i].day
            for k in VCP_HORIZONS:
                if i + k < len(b) and d0 in idx and b[i + k].day in idx:
                    fwd[lbls[0]][k].append((c[i + k] / c[i] - 1) - (idx[b[i + k].day] / idx[d0] - 1))
    both = {k: [x for lab in fwd.values() for x in lab.get(k, [])] for k in VCP_HORIZONS}
    return {
        "period": {"start": start_day.isoformat(), "end": days[-1].isoformat()},
        "thresholds": dict(vcp.THRESHOLDS),
        "labels": {k: vcp.summarize(v) for k, v in cases.items()},
        "all": vcp.summarize([x for v in cases.values() for x in v]),
        "forward": {
            **{lab: {str(k): _vcp_stats(fwd[lab].get(k, [])) for k in VCP_HORIZONS} for lab in vcp.LABELS},
            "all": {str(k): _vcp_stats(both[k]) for k in VCP_HORIZONS},
        },
        # Etiketterna räknas var för sig (ett fall i taget per bolag och etikett).
        "separate_labels": True,
        "note": "Dagens bevakningslista: bolag som avnoterats under perioden saknas (överlevnadsbias). RS-percentilen "
                "jämförs mot listans bolag samma dag. Etiketterna räknas var för sig, så ett utbrott kan infalla under "
                "ett pågående Nära pivot-fall i samma bolag. Utan courtage, spread och glidning.",
    }


def build(bars: dict, instruments: dict, today: date, results_dir: Path | None = None) -> dict:
    idx = {b.day: b.adj_close for b in bars.get(INDEX_TICKER, [])}
    days = sorted(idx)
    end_i = len(days) - 1 - max(HORIZONS)
    start_day, end_day = days[max(0, end_i - TEST_DAYS + 1)], days[end_i]
    base: dict[int, list[float]] = defaultdict(list)
    per_label: dict[str, dict[int, list[float]]] = {k: defaultdict(list) for k in LABELS}
    pb_cases: dict[str, list[dict]] = {k: [] for k in pullback.LABELS}
    bb_cases: dict[str, list[dict]] = {k: [] for k in bbrekyl.LABELS}
    # Volymvillkoren var för sig: variant -> villkor som krävs.
    bb_variants = {"base": (), **{r: (r,) for r in bbrekyl.RULES}, "all": bbrekyl.ALL_RULES}
    # Strategins krav (REQUIRED_RULES) är en av varianterna ovan; "strategy" pekar ut den i filen.
    bb_rule_cases: dict[str, list[dict]] = {k: [] for k in bb_variants}
    # Marknadsfilter: bara fall när indexet vid rekyldagens stängning ligger över sitt 50- och 200-dagars MA.
    market_up = market_filter(bars.get(INDEX_TICKER, []))
    pb_filtered: dict[str, list[dict]] = {k: [] for k in pullback.LABELS}
    # Stöd och motstånd: fall med en stödzon inom 1 ATR under stängningen och närmaste motstånd minst 2R över
    # referensen, mot övriga fall (samma fall som utan filter).
    pb_sr: dict[str, list[dict]] = {"with": [], "without": []}
    # Rangordningen (scripts/ranking.py): fallen som startar samma dag rangordnas; topp N per dag mot övriga.
    pb_ranked: dict[date, list[dict]] = defaultdict(list)
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
        # Ett pullback-fall i taget per bolag, räknat var för sig utan och med marknadsfilter (ett bortfiltrerat
        # fall ska inte spärra nästa fall som klarar filtret).
        busy_until, busy_filtered, busy_bb = -1, -1, -1
        busy_rule = {k: -1 for k in bb_variants}
        vols = [x.volume for x in b]
        for i, m in enumerate(ms):
            bb_labels: list[str] = []
            if m is not None and start_day <= m["day"] <= end_day:
                # Rekyl efter Bollingerutbrott: fallet följs som Pullback, med rekylens lägsta low som stopp.
                bst = bbrekyl.window(o, h, lo, c, vols, e20, i)
                bb_labels = bbrekyl.labels(bst)
                if bb_labels and i > busy_bb:
                    case = bbrekyl.simulate(o, h, lo, i, bst, bbrekyl.stop_of(bst))
                    bb_cases[bb_labels[0]].append(case)
                    busy_bb = case["end"]
                for name, rules in bb_variants.items():
                    if i > busy_rule[name] and bst is not None and bbrekyl.labels(bst, rules=rules):
                        case = bbrekyl.simulate(o, h, lo, i, bst, bbrekyl.stop_of(bst, rules))
                        bb_rule_cases[name].append(case)
                        busy_rule[name] = case["end"]
            # Pullback i trend: utfall mot stopp och 2R, oberoende av indexjämförelsen nedan (som kräver 5 dagar framåt).
            if m is not None and start_day <= m["day"] <= end_day and (i > busy_until or i > busy_filtered):
                st = pullback.state(o, h, lo, c, e20, i)
                pls = pullback.labels(st)
                if pls:
                    stop = pullback.stop_level(h, lo, i, pullback.touched(st)[1])
                    case = pullback.simulate(o, h, lo, i, st, stop)
                    if i > busy_until:
                        pb_cases[pls[0]].append(case)
                        busy_until = case["end"]
                        pb_sr["with" if sr_ok(h, lo, c, i, m, st, stop) else "without"].append(case)
                        meas = rank_measures(h, lo, c, i, m, st)
                        if meas is not None:
                            crit = ranking.criteria(meas)
                            pb_ranked[m["day"]].append({"score": sum(crit.values()), "measures": meas, "name": t,
                                                        "case": case})
                    if i > busy_filtered and market_up.get(m["day"], False):
                        pb_filtered[pls[0]].append(case)
                        busy_filtered = case["end"]
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
            labels = technical_labels(m) + pullback.labels(pullback.state(o, h, lo, c, e20, i)) + bb_labels
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
    top_n = ranking.THRESHOLDS["top"]
    rank_top, rank_rest, by_score = [], [], defaultdict(list)
    for entries in pb_ranked.values():
        ranking.rank(entries)
        for e in entries:
            (rank_top if e["rank"] <= top_n else rank_rest).append(e["case"])
            by_score[e["score"]].append(e["case"])
    return {
        "generated_at": datetime.now(STOCKHOLM).isoformat(timespec="seconds"),
        "period": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        "horizons": list(HORIZONS),
        "thresholds": THRESHOLDS,
        "method": "Andel fall där aktien gått bättre än OMX Stockholm PI efter 2 och 5 handelsdagar (justerad "
                  "stängning), jämfört med alla aktie-dagar. Dagens bevakningslista, bara aktier i kronor, utan "
                  "courtage och spread. Rapportetiketterna bygger på rapportutfallen sedan oktober 2026.",
        "base": {str(k): _stats(base.get(k, [])) for k in HORIZONS},
        # Pullback i trend: hur ofta 2R nåddes före stoppet och tvärtom (scripts/pullback.py).
        "pullback": {
            "thresholds": pullback.THRESHOLDS,
            "labels": {k: pullback.summarize(v) for k, v in pb_cases.items()},
            "all": pullback.summarize([x for v in pb_cases.values() for x in v]),
            "sr_filter": {
                "description": "Fall med en stödzon (tidigare vändpunkter, inte medelvärdena) högst 1 ATR under "
                               "stängningen och närmaste motstånd (zoner och medelvärden) minst 2R över referensen, "
                               "eller inget motstånd inom 120 dagar, mot övriga fall.",
                "with": pullback.summarize(pb_sr["with"]),
                "without": pullback.summarize(pb_sr["without"]),
            },
            "ranking": {
                "description": f"Fallen som startar samma dag rangordnade efter antal uppfyllda kriterier (vid lika "
                               f"poäng mest utrymme till motståndet i R): topp {top_n} per dag mot övriga fall. "
                               f"Samma fall som utan filter; en dag med högst {top_n} fall har bara topp-fall.",
                "top_n": top_n,
                "days": len(pb_ranked),
                "days_over_top": sum(1 for v in pb_ranked.values() if len(v) > top_n),
                "top": pullback.summarize(rank_top),
                "others": pullback.summarize(rank_rest),
                "by_score": {str(k): pullback.summarize(v) for k, v in sorted(by_score.items())},
            },
            "market_filter": {
                "description": "Bara fall när OMX Stockholm PI vid rekyldagens stängning låg över sitt 50- och "
                               "200-dagars glidande medelvärde.",
                "days_up": sum(1 for d, up in market_up.items() if up and start_day <= d <= end_day),
                "days": sum(1 for d in market_up if start_day <= d <= end_day),
                "labels": {k: pullback.summarize(v) for k, v in pb_filtered.items()},
                "all": pullback.summarize([x for v in pb_filtered.values() for x in v]),
            },
        },
        # Momentum-utbrott (VCP): 3R före stoppet och utvecklingen mot index, de senaste tre åren.
        "vcp": vcp_history(bars, instruments),
        # Rekyl efter Bollingerutbrott: hur ofta 2R nåddes före stoppet (rekylens lägsta low) och tvärtom.
        "bbrekyl": {
            "thresholds": {**pullback.THRESHOLDS, **bbrekyl.THRESHOLDS},
            "labels": {k: pullback.summarize(v) for k, v in bb_cases.items()},
            "all": pullback.summarize([x for v in bb_cases.values() for x in v]),
            "rules": {
                "description": "Volymvillkoren var för sig: grundregeln (utbrott med minst 1,5× omsättning, inga övriga "
                               "volymvillkor), grundregeln plus ett villkor i taget, och alla fyra tillsammans. Strategin "
                               "är varianten i \"strategy\". Båda etiketterna, varje variant med sina egna fall.",
                "texts": dict(bbrekyl.RULES),
                "strategy": "+".join(bbrekyl.REQUIRED_RULES) if len(bbrekyl.REQUIRED_RULES) != 1
                else bbrekyl.REQUIRED_RULES[0],
                **{k: pullback.summarize(v) for k, v in bb_rule_cases.items()},
            },
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
    # Avnoterade bolag hoppas över (instruments.csv), som i swing.py.
    instruments = listed(load_instruments(), today - timedelta(days=1))
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
