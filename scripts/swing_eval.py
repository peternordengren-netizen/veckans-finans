"""Daglig utvärdering i fliken Swing: vad hände efteråt med gårdagens kandidater?

Ett informationsverktyg: underlaget beskriver hur kursen rört sig efter en dag med vissa kännetecken. Det är ingen
uppmaning och säger inget säkert om hur det går nästa gång.

1. Ögonblicksbild (snapshot), varje morgon ur dagens swingfil (data/swing/<datum>.json, stängningen dagen före =
   signaldagen): ALLA kandidater i de tre strategierna (Pullback i trend, Rekyl efter Bollingerutbrott,
   Momentum-utbrott (VCP)) och ALLA bolag med starka signaler (samma regel som fliken Signaler, src/lib/signals.ts
   isStrong, ur senaste veckans signalfil före swingfilens datum: minst två signaler eller minst en av insyn, avviker,
   52v-högsta, 52v-lägsta och pe-upp; blankningen räknas inte, och stor rörelse räknas inte för ett bolag som
   rapporterat under signalveckan). Inget urval efter utfall: alla sparas.
   Kännetecken per bolag: urval (strategier, starka signaler), alla etiketter, uppfyllda rangkriterier, omsättning
   mot snitt, RSI(14), Stoch RSI, avstånd till stöd och motstånd i R, sektor, rapport de senaste 5 handelsdagarna
   (reaktionsdagen inräknad) och veckans signaler. R = strategins R (referens − stopp, den första av Pullback,
   Bollingerrekyl och VCP där bolaget är kandidat); bolag som bara har starka signaler saknar R (fliken har ett enda R).
   Marknadsläget samma dag sparas med varje ögonblicksbild ("market", market_state) och läget ("regime") med varje
   fall: indexet (OMX Stockholm PI) mot 50 och 200 dagars MA, indexets svängningar (20 dagars volatilitet,
   annualiserad), andelen bolag i listan över sitt 50 dagars MA (och en vecka tidigare), antal bolag på 52 veckors
   högsta respektive lägsta (dagens high/low på årets högsta/lägsta) och sektorernas relativa styrka de senaste 20
   dagarna (snittutveckling mot index) med omsättning mot snitt. Läget = trend (upptrend: index över båda MA,
   nedtrend: under båda, annars blandad) och svängningar (lugna under VOL_CALM_MAX % volatilitet, annars stora).
   Marknadstecken (larm): andelen över 50 MA ändrad mer än BREADTH_ALERT_PP procentenheter på en vecka (5
   handelsdagar), en sektors omsättning minst SECTOR_VOLUME_ALERT gånger snittet, ett nytt läge mot dagen före, och
   "Sektor i rörelse" (sector_moves): minst SECTOR_MOVE_MIN (3) bolag i sektorn minst SECTOR_MOVE_PP (3) procentenheter
   bättre än index samma dag, eller sektorns relativa styrka de senaste 5 dagarna (snittet av bolagens utveckling mot
   index) vände från negativ dagen före till positiv; med bolagen.
   "Kortsiktigt översåld i upptrend" (oversold): bolag i listan med RSI(2) under OVERSOLD_RSI2 (10, swingfilens rsi2)
   och kurs över 200 dagars MA, med marknadsläget och de lägen mönstret prövats i hittills.
2. Uppföljning efter 1–5 handelsdagar (HORIZONS) med samma hämtning som morgonens swingfil: rörelse
   (justerad stängning), rörelse mot index (OMX Stockholm PI) i procentenheter, högsta high under dagarna i R
   (från signaldagens stängning) och snittomsättningen under dagarna mot snittet för de 20 dagarna före signaldagen.
   Lyckat fall (success_status): minst SUCCESS_PP (5) procentenheter över index vid någon stängning inom SUCCESS_DAYS
   (5) handelsdagar, oavsett R (sedan 2026-11-11; R påverkade tidigare, men ett litet R gjorde 2R för lätt att nå);
   "ej" när femte dagen följts upp utan det, annars öppet. Högsta high i R visas som information.
3. Sammanställningen (summary.json): "Gårdagens bästa rörelser" (topp 10 mot index efter 1 dag i den senaste
   ögonblicksbilden med 1-dagsuppföljning), dagens marknadsläge, "Mönster" per fönster (WINDOWS: senaste 2 och 6
   veckorna, dagar med samma läge som det senaste, och hela perioden som bakgrund), biblioteket med lyckade fall
   (successes, med alla avgjorda fall i cases_index för jämförelsen och lyckade per läge och strategi senaste
   SIMILAR_DAYS dagarna) och larmen (alerts): dagens kandidater som liknar minst SIMILAR_MIN lyckade fall de senaste
   SIMILAR_DAYS (28) dagarna, räknat i olika bolag (samma strategi och kännetecken med Jaccardlikhet minst
   SIMILAR_JACCARD), med hur stor andel av alla liknande avgjorda fall som lyckades (som jämförelse), och
   marknadstecknen. Larmen står i notisen vid Bollingerkontrollerna 09:20 och 13:20 (alerts_for, scripts/bollinger.py)
   och bygger på gårdagens stängning. "Mönster": för varje kännetecken med minst MIN_FEATURE_CASES fall,
   andelen bland de 20 % (TOP_SHARE) med störst rörelse mot index jämfört med övriga, efter 3 och 5 dagar, med antal
   och 95 % konfidensintervall (Wilson). Ett mönster har "hållit" en vecka när andelen bland de 20 % var högre än
   bland övriga i den veckans fall (signaldagens ISO-vecka, de 20 % räknade inom veckan); markerat när det hållit
   HOLD_WEEKS (4) veckor i rad fram till den senaste veckan med uppföljning.
4. Måndagar (veckans första handelsdag, needs_note): Claude skriver 5–8 meningar om marknadsläget, vilka lägen som
   lyckats de senaste veckorna och vad som ändrats, märkt som
   tolkning (/swing-utvardering, scripts/run_swing_note.ps1). Utkastet i data/swing_eval/<vecka>.json kontrolleras
   (check_note: antal meningar, inga uppmaningar, varje tal finns i sammanställningen) och flyttas sedan till
   rådatarepot.

Underlaget sparas bara i rådatarepot (data/raw/swing_eval/): <signaldag>.json per ögonblicksbild, summary.json och
notes/<vecka>.json. Kända begränsningar: dagens bevakningslista (överlevnadsbias), fallen överlappar i tid och är inte
oberoende, utan courtage.

Användning:
    python scripts/swing_eval.py --efterhand      # ögonblicksbilder ur sparade swingfiler + uppföljning (en hämtning)
    python scripts/swing_eval.py --kontrollera 2026-W47   # kontrollerar Claudes utkast
    python scripts/swing_eval.py --klar 2026-W47          # godkänt utkast till rådatarepot
    python scripts/swing_eval.py --behover-text   # exit 0 om veckans text saknas och underlag finns, annars 1
    python scripts/swing_eval.py --vecka          # dagens ISO-vecka
Morgonen (scripts/morning.py write_swing) anropar morning_update() med swingfilen och morgonens kurser.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prices import INDEX_TICKER  # noqa: E402
from validate import DATA_DIR  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
HORIZONS = (1, 2, 3, 4, 5)
PATTERN_HORIZONS = (3, 5)
TOP_SHARE = 0.2
TOP_N = 10
HOLD_WEEKS = 4
MIN_FEATURE_CASES = 10
VOLUME_AVG_DAYS = 20
REPORT_DAYS = 5
KEEP_OPEN_DAYS = 14  # ögonblicksbilder äldre än så (kalenderdagar) följs inte upp längre
SUCCESS_PP, SUCCESS_DAYS = 5.0, 5
SIMILAR_DAYS, SIMILAR_MIN, SIMILAR_JACCARD = 28, 3, 0.6
BREADTH_ALERT_PP = 10.0
SECTOR_VOLUME_ALERT = 2.0
VOL_CALM_MAX = 20.0
SECTOR_MOVE_MIN, SECTOR_MOVE_PP = 3, 3.0
OVERSOLD_RSI2 = 10.0
MARKET_VERSION = 2  # 2: sektor i rörelse (äldre marknadslägen räknas om när kurserna finns)
MARKET_DAYS = 252  # 52 veckor
# Fönstren för mönstren: (nyckel, text, kalenderdagar bakåt från den senaste signaldagen; None = hela perioden).
WINDOWS = (("2v", "Senaste 2 veckorna", 14), ("6v", "Senaste 6 veckorna", 42), ("lage", "Samma marknadsläge", None),
           ("alla", "Hela perioden", None))
TREND_TEXT = {"upp": "Upptrend", "ned": "Nedtrend", "blandad": "Blandad trend"}
VOL_TEXT = {"lugn": "lugna svängningar", "orolig": "stora svängningar"}
RAW_SUBDIR = "swing_eval"
EVAL_DIR = DATA_DIR / "raw" / RAW_SUBDIR
NOTE_DIR = DATA_DIR / "swing_eval"  # Claudes utkast (en riktig mapp i driftkopian, som data/hb)
SIGNALS_DIR = DATA_DIR / "signals"
SWING_DIR = DATA_DIR / "swing"
STRATEGY_ORDER = ("pullback", "bbrekyl", "vcp")
STRONG_SIGNALS = {"insyn", "avviker", "52v-högsta", "52v-lägsta", "pe-upp"}
CRITERIA_KEYS = {"pullback": "ranking_criteria", "bbrekyl": "bbrekyl_criteria", "vcp": "vcp_criteria"}
NOTE_MIN_SENTENCES, NOTE_MAX_SENTENCES = 5, 8
FORBIDDEN = re.compile(r"\b(köp|sälj|kursmål|rekommend)", re.IGNORECASE)
# Trösklar för kännetecknen (sparas i varje ögonblicksbild).
FEATURE_THRESHOLDS = {"volume_high": 1.5, "volume_low": 0.7, "rsi_high": 70, "rsi_low": 30, "stoch_high": 80,
                      "stoch_low": 20, "support_r": 1.0, "resistance_r": 2.0}


# --- Starka signaler ---------------------------------------------------------------------------


def latest_signals(swing_date: str, signals_dir: Path = SIGNALS_DIR) -> dict | None:
    """Senaste veckans signalfil vars period slutar före swingfilens datum, eller None."""
    best = None
    for p in sorted(signals_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        end = (d.get("period") or {}).get("end")
        if end and end < swing_date and (best is None or end > best["period"]["end"]):
            best = d
    return best


def strong_signals(signals: dict | None, reported: set[str] = frozenset()) -> dict[str, list[str]]:
    """{ticker: räknade signaler} för bolagen med starka signaler (se modulbeskrivningen)."""
    out = {}
    for c in (signals or {}).get("companies", []):
        counted = [s for s in c.get("signals", []) if s != "blankning"
                   and not (c["ticker"] in reported and s == "stor-rörelse")]
        if len(counted) >= 2 or any(s in STRONG_SIGNALS for s in counted):
            out[c["ticker"]] = counted
    return out


def reported_in(swing: dict, signals: dict | None) -> set[str]:
    """Bolag vars senaste rapport publicerades under signalveckan (deras stora rörelse räknas inte)."""
    p = (signals or {}).get("period") or {}
    if not p.get("start"):
        return set()
    return {c["ticker"] for c in swing["companies"] if c.get("report_published")
            and p["start"] <= c["report_published"][:10] <= p["end"]}


# --- Ögonblicksbilden ----------------------------------------------------------------------------


def _strategy_close(c: dict, key: str) -> float | None:
    st = (c.get(key) or {}).get("state") or {}
    if key == "bbrekyl":
        return ((st.get("days") or [{}])[-1]).get("close")
    return st.get("close")


def _criteria(c: dict, key: str) -> dict[str, bool]:
    return ((c.get(key) or {}).get("rank") or {}).get("criteria") or {}


def entry_of(c: dict, swing: dict, strong: dict[str, list[str]], yahoo: str | None) -> dict | None:
    """Kännetecknen för ett bolag, eller None om det varken är kandidat eller har starka signaler."""
    strategies = [k for k in STRATEGY_ORDER if k in swing.get("strategies", {})
                  and any(lb in swing["strategies"][k]["labels"] for lb in c["labels"])
                  and (c.get(k) or {}).get("levels")]
    if not strategies and c["ticker"] not in strong:
        return None
    r = close = None
    r_from = None
    for k in strategies:
        lv = c[k]["levels"]
        if lv.get("ref") is not None and lv.get("stop") is not None and lv["ref"] > lv["stop"]:
            r, close, r_from = lv["ref"] - lv["stop"], _strategy_close(c, k), k
            break
    sr = c.get("sr") or {}
    dist = lambda side: ((sr.get(side) or {}).get("dist_pct"))  # noqa: E731
    in_r = lambda d: round(d / 100 * close / r, 2) if d is not None and r and close else None  # noqa: E731
    mom = c.get("momentum") or {}
    ds = c.get("days_since_report")
    return {
        "ticker": c["ticker"], "name": c["name"], "yahoo_ticker": yahoo, "sector": c.get("sector"),
        "strategies": strategies, "strong": c["ticker"] in strong, "signals": strong.get(c["ticker"], []),
        "labels": list(c["labels"]),
        "criteria": {k: sorted(x for x, ok in _criteria(c, k).items() if ok) for k in strategies},
        "volume_ratio": c.get("volume_ratio"),
        "rsi14": mom.get("rsi14", c.get("rsi14")), "stoch_k": mom.get("stoch_k"), "stoch_d": mom.get("stoch_d"),
        "r": round(r, 4) if r else None, "r_from": r_from, "close": close,
        "support_r": in_r(dist("support")), "resistance_r": in_r(dist("resistance")),
        "report_5d": ds is not None and 0 <= ds < REPORT_DAYS,
        "follow": {},
    }


def features(e: dict, th: dict = FEATURE_THRESHOLDS) -> list[str]:
    """Kännetecknen som nycklar ("strategi:vcp", "etikett:utbrott", "kriterium:pullback:stodzon", ...)."""
    f = [f"strategi:{k}" for k in e["strategies"]]
    if e["strong"]:
        f.append("stark")
    f += [f"etikett:{lb}" for lb in e["labels"]]
    f += [f"kriterium:{k}:{x}" for k, xs in e["criteria"].items() for x in xs]
    f += [f"signal:{s}" for s in e["signals"]]
    v, rsi, sk = e["volume_ratio"], e["rsi14"], e["stoch_k"]
    if v is not None:
        f += ["volym:hog"] if v >= th["volume_high"] else ["volym:lag"] if v < th["volume_low"] else []
    if rsi is not None:
        f += ["rsi:hog"] if rsi >= th["rsi_high"] else ["rsi:lag"] if rsi <= th["rsi_low"] else []
    if sk is not None:
        f += ["stoch:hog"] if sk >= th["stoch_high"] else ["stoch:lag"] if sk <= th["stoch_low"] else []
    if e["support_r"] is not None and e["support_r"] <= th["support_r"]:
        f.append("stod:nara")
    if e["resistance_r"] is not None and e["resistance_r"] >= th["resistance_r"]:
        f.append("motstand:langt")
    if e["sector"]:
        f.append(f"sektor:{e['sector']}")
    if e["report_5d"]:
        f.append("rapport:5d")
    return f


def feature_texts(swing: dict, sectors: dict[str, str], th: dict = FEATURE_THRESHOLDS) -> dict[str, str]:
    """Visningstexterna för kännetecknen i en ögonblicksbild."""
    out = {f"strategi:{k}": f"Strategi: {s['name']}" for k, s in swing.get("strategies", {}).items()}
    out["stark"] = "Starka signaler"
    out.update({f"etikett:{k}": f"Etikett: {n}" for k, n in swing.get("labels", {}).items()})
    for k, ck in CRITERIA_KEYS.items():
        name = (swing.get("strategies", {}).get(k) or {}).get("name", k)
        out.update({f"kriterium:{k}:{x}": f"Kriterium ({name}): {t}" for x, t in (swing.get(ck) or {}).items()})
    for s in ("insyn", "avviker", "stor-rörelse", "omsättning", "pe-upp", "52v-högsta", "52v-lägsta", "blankning"):
        out[f"signal:{s}"] = f"Veckosignal: {s}"
    n = lambda x: f"{x:g}".replace(".", ",")  # noqa: E731
    out.update({
        "volym:hog": f"Omsättning minst {n(th['volume_high'])}× snittet",
        "volym:lag": f"Omsättning under {n(th['volume_low'])}× snittet",
        "rsi:hog": f"RSI(14) minst {th['rsi_high']}", "rsi:lag": f"RSI(14) högst {th['rsi_low']}",
        "stoch:hog": f"Stoch RSI minst {th['stoch_high']}", "stoch:lag": f"Stoch RSI högst {th['stoch_low']}",
        "stod:nara": f"Stöd inom {n(th['support_r'])} R", "motstand:langt": f"Motstånd minst {n(th['resistance_r'])} R bort",
        "rapport:5d": f"Rapport de senaste {REPORT_DAYS} handelsdagarna",
    })
    out.update({f"sektor:{k}": f"Sektor: {v}" for k, v in sectors.items()})
    return out


def snapshot(swing: dict, signals: dict | None, yahoo: dict[str, str], sectors: dict[str, str],
             now: datetime) -> dict:
    """Ögonblicksbilden för swingfilens signaldag (as_of): alla kandidater och bolag med starka signaler."""
    strong = strong_signals(signals, reported_in(swing, signals))
    entries = [e for c in swing["companies"] if (e := entry_of(c, swing, strong, yahoo.get(c["ticker"])))]
    for e in entries:
        e["features"] = features(e)
    used = {f for e in entries for f in e["features"]}
    texts = feature_texts(swing, sectors)
    return {
        "schema_version": 1, "day": swing["as_of"], "swing_date": swing["date"],
        "signals_week": (signals or {}).get("week"), "created_at": now.isoformat(timespec="seconds"),
        "thresholds": dict(FEATURE_THRESHOLDS), "horizons": list(HORIZONS),
        "feature_texts": {k: texts.get(k, k) for k in sorted(used)},
        "entries": sorted(entries, key=lambda e: e["name"]),
    }


# --- Marknadsläget ------------------------------------------------------------------------------


def _sma(x: list[float], i: int, n: int) -> float:
    return sum(x[i - n + 1:i + 1]) / n


def _trend_vol(c: list[float], i: int) -> tuple[str, str, float]:
    """(trend, svängningar, 20 dagars volatilitet i procent, annualiserad) för indexets justerade stängningar."""
    ma50, ma200 = _sma(c, i, 50), _sma(c, i, 200)
    trend = "upp" if c[i] > ma50 and c[i] > ma200 else "ned" if c[i] < ma50 and c[i] < ma200 else "blandad"
    rets = [math.log(c[j] / c[j - 1]) for j in range(i - 19, i + 1)]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(252) * 100
    return trend, "lugn" if vol < VOL_CALM_MAX else "orolig", vol


def regime_text(key: str | None) -> str:
    """"upp-lugn" → "Upptrend, lugna svängningar"."""
    if not key or "-" not in key:
        return "–"
    t, v = key.split("-", 1)
    return f"{TREND_TEXT.get(t, t)}, {VOL_TEXT.get(v, v)}"


def _breadth(series: list[tuple[list, dict[date, int]]], d: date) -> tuple[float | None, int]:
    """Andelen bolag (procent) med justerad stängning över sitt 50 dagars MA dag d, och antalet bolag."""
    above = n = 0
    for b, pos in series:
        i = pos.get(d)
        if i is None or i < 49:
            continue
        c = [x.adj_close for x in b[i - 49:i + 1]]
        n += 1
        above += c[-1] > sum(c) / 50
    return (round(above / n * 100, 1) if n else None), n


def market_state(bars: dict, sector_of: dict[str, str | None], d: date, sectors: dict[str, str],
                 names: dict[str, str] | None = None) -> dict | None:
    """Marknadsläget vid stängningen dag d (se modulbeskrivningen), eller None om indexet saknar 200 dagar fram till d.
    sector_of: Yahoo-ticker → sektorns id; names: Yahoo-ticker → bolagets namn (för "Sektor i rörelse")."""
    names = names or {}
    idx = bars.get(INDEX_TICKER) or []
    ipos = {x.day: n for n, x in enumerate(idx)}
    i = ipos.get(d)
    if i is None or i < 200:
        return None
    c = [x.adj_close for x in idx]
    trend, vol_key, vol = _trend_vol(c, i)
    ptrend, pvol, _ = _trend_vol(c, i - 1)
    regime, prev = f"{trend}-{vol_key}", f"{ptrend}-{pvol}"
    ma50, ma200 = _sma(c, i, 50), _sma(c, i, 200)
    series = []
    for yt, b in bars.items():
        if yt != INDEX_TICKER and yt in sector_of and b:
            series.append((b, {x.day: n for n, x in enumerate(b)}, yt))
    br, n_br = _breadth([(b, pos) for b, pos, _ in series], d)
    br_prev, _ = _breadth([(b, pos) for b, pos, _ in series], idx[i - 5].day)
    highs = lows = 0
    idx20 = c[i] / c[i - 20] - 1
    idx1 = c[i] / c[i - 1] - 1
    idx5, idx5_prev = c[i] / c[i - 5] - 1, c[i - 1] / c[i - 6] - 1
    by_sector: dict[str, dict] = {}
    for b, pos, yt in series:
        j = pos.get(d)
        if j is None:
            continue
        if j >= MARKET_DAYS - 1:
            window = b[j - MARKET_DAYS + 1:j + 1]
            highs += b[j].high >= max(x.high for x in window)
            lows += b[j].low <= min(x.low for x in window)
        sec = sector_of.get(yt)
        if not sec or j < 20:
            continue
        g = by_sector.setdefault(sec, {"ret": [], "vol": 0.0, "avg": 0.0, "r5": [], "r5p": [], "day": []})
        g["ret"].append(b[j].adj_close / b[j - 20].adj_close - 1)
        g["r5"].append(b[j].adj_close / b[j - 5].adj_close - 1 - idx5)
        g["r5p"].append(b[j - 1].adj_close / b[j - 6].adj_close - 1 - idx5_prev)
        g["day"].append((names.get(yt, yt), round((b[j].adj_close / b[j - 1].adj_close - 1 - idx1) * 100, 2)))
        g["vol"] += b[j].volume
        g["avg"] += sum(x.volume for x in b[j - 20:j]) / 20
    secs = sorted(({"sector": k, "name": sectors.get(k, k), "companies": len(g["ret"]),
                    "rs20_pp": round((sum(g["ret"]) / len(g["ret"]) - idx20) * 100, 2),
                    "volume_ratio": round(g["vol"] / g["avg"], 2) if g["avg"] > 0 else None,
                    "rs5_pp": round(sum(g["r5"]) / len(g["r5"]) * 100, 2),
                    "rs5_prev_pp": round(sum(g["r5p"]) / len(g["r5p"]) * 100, 2)}
                   for k, g in by_sector.items() if g["ret"]), key=lambda x: -x["rs20_pp"])
    # Sektor i rörelse: minst SECTOR_MOVE_MIN bolag minst SECTOR_MOVE_PP bättre än index samma dag, eller den relativa
    # styrkan de senaste 5 dagarna vände från negativ till positiv.
    moves = []
    for x in secs:
        g = by_sector[x["sector"]]
        up = sorted((m for m in g["day"] if m[1] >= SECTOR_MOVE_PP), key=lambda m: -m[1])
        turned = x["rs5_prev_pp"] < 0 < x["rs5_pp"]
        if len(up) >= SECTOR_MOVE_MIN or turned:
            shown = up if len(up) >= SECTOR_MOVE_MIN else sorted(g["day"], key=lambda m: -m[1])[:3]
            moves.append({"sector": x["sector"], "name": x["name"], "day_up": len(up) >= SECTOR_MOVE_MIN,
                          "turned": turned, "rs5_pp": x["rs5_pp"], "rs5_prev_pp": x["rs5_prev_pp"],
                          "companies": [{"name": n, "vs_index_pp": v} for n, v in shown]})
    change = round(br - br_prev, 1) if br is not None and br_prev is not None else None
    one = lambda x: f"{x:.1f}".replace(".", ",").replace("-", "−")  # noqa: E731
    signs = []
    if change is not None and abs(change) > BREADTH_ALERT_PP:
        signs.append({"kind": "bredd", "text": f"Andelen bolag över 50 MA {'steg' if change > 0 else 'föll'} "
                      f"{one(abs(change))} procentenheter på en vecka, från {one(br_prev)} % till {one(br)} %"})
    for x in secs:
        if x["volume_ratio"] is not None and x["volume_ratio"] >= SECTOR_VOLUME_ALERT:
            signs.append({"kind": "sektorvolym", "text": f"{x['name']}: omsättning {one(x['volume_ratio'])} gånger snittet"})
    for mv in moves:
        who = ", ".join(f"{c_['name']} {'+' if c_['vs_index_pp'] >= 0 else '−'}{one(abs(c_['vs_index_pp']))}"
                        for c_ in mv["companies"])
        why = []
        if mv["day_up"]:
            why.append(f"{len(mv['companies'])} bolag minst {one(SECTOR_MOVE_PP)} procentenheter bättre än index")
        if mv["turned"]:
            why.append(f"relativ styrka 5 dagar vände från {one(mv['rs5_prev_pp'])} till +{one(mv['rs5_pp'])} "
                       "procentenheter")
        signs.append({"kind": "sektor", "text": f"Sektor i rörelse, {mv['name']}: {' och '.join(why)} ({who})"})
    if regime != prev:
        signs.append({"kind": "läge", "text": f"Marknadsläget skiftade från {regime_text(prev).lower()} till "
                      f"{regime_text(regime).lower()}"})
    return {
        "day": d.isoformat(),
        "index": {"close": round(c[i], 2), "ma50": round(ma50, 2), "ma200": round(ma200, 2),
                  "vs_ma50_pct": round((c[i] / ma50 - 1) * 100, 2), "vs_ma200_pct": round((c[i] / ma200 - 1) * 100, 2),
                  "vol20": round(vol, 1), "change20_pct": round(idx20 * 100, 2)},
        "trend": trend, "volatility": vol_key, "regime": regime, "regime_text": regime_text(regime),
        "prev_regime": prev,
        "breadth": {"above_ma50_pct": br, "week_ago_pct": br_prev, "change_pp": change, "companies": n_br},
        "new_highs": highs, "new_lows": lows,
        "sectors": secs,
        "sector_moves": moves,
        "signs": signs,
        "version": MARKET_VERSION,
    }


# --- Uppföljningen ------------------------------------------------------------------------------


def follow_up(snap: dict, bars: dict) -> int:
    """Fyller i uppföljningen (HORIZONS) för de bolag och dagar som finns i bars; returnerar antal nya värden.
    Redan ifyllda värden ändras inte."""
    idx = {b.day: b.adj_close for b in bars.get(INDEX_TICKER, [])}
    d0 = date.fromisoformat(snap["day"])
    added = 0
    for e in snap["entries"]:
        b = bars.get(e.get("yahoo_ticker") or "")
        if not b:
            continue
        pos = {x.day: n for n, x in enumerate(b)}
        i = pos.get(d0)
        if i is None or d0 not in idx or i < VOLUME_AVG_DAYS:
            continue
        avg = sum(x.volume for x in b[i - VOLUME_AVG_DAYS:i]) / VOLUME_AVG_DAYS
        for k in HORIZONS:
            if str(k) in e["follow"] or i + k >= len(b) or b[i + k].day not in idx:
                continue
            seg = b[i + 1:i + k + 1]
            move = b[i + k].adj_close / b[i].adj_close - 1
            imove = idx[b[i + k].day] / idx[d0] - 1
            hi = max(x.high for x in seg)
            e["follow"][str(k)] = {
                "day": b[i + k].day.isoformat(),
                "move_pct": round(move * 100, 2),
                "vs_index_pp": round((move - imove) * 100, 2),
                "max_high_r": round((hi / b[i].close - 1) * e["close"] / e["r"], 2) if e.get("r") and e.get("close")
                else None,
                "volume_ratio": round(sum(x.volume for x in seg) / k / avg, 2) if avg > 0 else None,
            }
            added += 1
    return added


# --- Sammanställningen ---------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95 % konfidensintervall (Wilson) för andelen k av n, [låg, hög], eller None utan fall."""
    if n == 0:
        return None
    p = k / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)]


def _split_top(rows: list[dict], h: str) -> tuple[list[dict], list[dict]]:
    """De TOP_SHARE med störst rörelse mot index efter h dagar, och övriga (lika värden: bolagsnamn, dag)."""
    s = sorted(rows, key=lambda r: (-r["follow"][h]["vs_index_pp"], r["name"], r["day"]))
    n_top = max(1, round(len(s) * TOP_SHARE)) if s else 0
    return s[:n_top], s[n_top:]


def _share(group: list[dict], f: str) -> dict:
    k = sum(f in r["features"] for r in group)
    return {"k": k, "n": len(group), "share": round(k / len(group), 4) if group else None, "ci": wilson(k, len(group))}


def _iso_week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def patterns(rows: list[dict], h: str, texts: dict[str, str]) -> dict:
    """Mönstren efter h dagar (se modulbeskrivningen)."""
    rows = [r for r in rows if h in r["follow"]]
    top, other = _split_top(rows, h)
    weeks = sorted({_iso_week(r["day"]) for r in rows})
    by_week = {w: _split_top([r for r in rows if _iso_week(r["day"]) == w], h) for w in weeks}
    counts: dict[str, int] = {}
    for r in rows:
        for f in r["features"]:
            counts[f] = counts.get(f, 0) + 1
    out = []
    for f, n in counts.items():
        if n < MIN_FEATURE_CASES:
            continue
        held = []
        for w in weeks:
            wt, wo = by_week[w]
            st, so = _share(wt, f)["share"], _share(wo, f)["share"]
            held.append(st is not None and so is not None and st > so)
        streak = 0
        for ok in reversed(held):
            if not ok:
                break
            streak += 1
        t, o = _share(top, f), _share(other, f)
        out.append({"feature": f, "text": texts.get(f, f), "cases": n, "top": t, "other": o,
                    "diff_pp": round((t["share"] - o["share"]) * 100, 1) if t["share"] is not None and o["share"] is not None
                    else None,
                    "weeks_held": streak, "held": streak >= HOLD_WEEKS})
    out.sort(key=lambda x: (-(x["diff_pp"] if x["diff_pp"] is not None else -1e9), x["text"]))
    return {"horizon": int(h), "cases": len(rows), "top_n": len(top), "weeks": weeks, "features": out}


def success_status(e: dict) -> str | None:
    """"lyckad" (minst SUCCESS_PP pe över index vid någon stängning inom SUCCESS_DAYS dagar, oavsett R), "ej" (femte
    dagen uppföljd utan det) eller None (öppet)."""
    f = e.get("follow") or {}
    if any(f[str(k)]["vs_index_pp"] >= SUCCESS_PP for k in range(1, SUCCESS_DAYS + 1) if str(k) in f):
        return "lyckad"
    return "ej" if str(SUCCESS_DAYS) in f else None


def success_info(e: dict) -> dict:
    """Det högsta mot index (procentenheter, vid stängning) och högsta high i R (information, avgör inte) inom
    SUCCESS_DAYS dagar."""
    f = [e["follow"][str(k)] for k in range(1, SUCCESS_DAYS + 1) if str(k) in e["follow"]]
    rs = [x["max_high_r"] for x in f if x.get("max_high_r") is not None]
    return {"max_vs_index_pp": max((x["vs_index_pp"] for x in f), default=None), "max_high_r": max(rs, default=None)}


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def similar_cases(cand: dict, day: str, cases: list[dict]) -> list[dict]:
    """Fall de senaste SIMILAR_DAYS dagarna före day som delar en strategi med kandidaten och har kännetecken med
    Jaccardlikhet minst SIMILAR_JACCARD."""
    d0 = date.fromisoformat(day)
    out = []
    for x in cases:
        dd = date.fromisoformat(x["day"])
        if not (d0 - timedelta(days=SIMILAR_DAYS) <= dd < d0):
            continue
        if set(x["strategies"]) & set(cand["strategies"]) and jaccard(x["features"], cand["features"]) >= SIMILAR_JACCARD:
            out.append(x)
    return out


def complete_days(snaps: list[dict]) -> set[str]:
    """Signaldagar med full femdagarsuppföljning (minst ett bolag följt upp dag SUCCESS_DAYS)."""
    return {s["day"] for s in snaps if any(str(SUCCESS_DAYS) in e["follow"] for e in s["entries"])}


def episodes(rows: list[dict]) -> list[dict]:
    """Ett fall per uppgång: samma bolag på signaldagar inom SUCCESS_DAYS handelsdagar från uppgångens första
    signaldag räknas som en uppgång. Kännetecknen och uppföljningen är den första dagens; uppgången är "lyckad" om
    någon av dagarna blev det ("ej" om ingen blev det och minst en är avgjord, annars None). rows: fallen med "day"."""
    from workdays import business_days_between

    out = []
    by_ticker: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["ticker"], r["day"])):
        by_ticker.setdefault(r["ticker"], []).append(r)
    for rs in by_ticker.values():
        group: list[dict] = []
        for r in rs + [None]:
            if r is not None and group and business_days_between(date.fromisoformat(group[0]["day"]),
                                                                 date.fromisoformat(r["day"])) <= SUCCESS_DAYS:
                group.append(r)
                continue
            if group:
                st = [success_status(g) for g in group]
                status = "lyckad" if "lyckad" in st else "ej" if "ej" in st else None
                won = next((g for g, x in zip(group, st) if x == "lyckad"), None)
                out.append({**group[0], "status": status, "episode_days": [g["day"] for g in group],
                            "success_day": won["day"] if won else None, "won": won})
            group = [r] if r is not None else []
    return sorted(out, key=lambda r: (r["day"], r["name"]))


def _window_rows(rows: list[dict], key: str, days: int | None, last: str | None, regime: str | None) -> list[dict]:
    if key == "lage":
        return [r for r in rows if regime and r.get("regime") == regime]
    if days is None or last is None:
        return rows
    first = (date.fromisoformat(last) - timedelta(days=days)).isoformat()
    return [r for r in rows if r["day"] > first]


def _rate(group: list[dict]) -> dict:
    k = sum(g["status"] == "lyckad" for g in group)
    return {"cases": len(group), "successes": k, "share": round(k / len(group), 4) if group else None,
            "ci": wilson(k, len(group))}


def summary(snaps: list[dict], now: datetime) -> dict:
    """Sammanställningen för sidan, larmen och Claudes text (summary.json). Mönstren, de lyckade fallen och
    jämförelserna räknar bara signaldagar med full femdagarsuppföljning och ett fall per uppgång (episodes)."""
    snaps = sorted(snaps, key=lambda s: s["day"])
    texts: dict[str, str] = {}
    rows = []
    for s in snaps:
        texts.update(s.get("feature_texts", {}))
        for e in s["entries"]:
            rows.append({**e, "day": s["day"]})
    full = complete_days(snaps)
    eps = episodes([r for r in rows if r["day"] in full])
    last = snaps[-1] if snaps else None
    market = [s["market"] for s in snaps if s.get("market")]
    today = market[-1] if market else None
    week_ago = market[-6] if len(market) >= 6 else None
    # Gårdagens bästa rörelser: den senaste ögonblicksbilden med 1-dagsuppföljning.
    with1 = [s for s in snaps if any("1" in e["follow"] for e in s["entries"])]
    top = None
    if with1:
        s = with1[-1]
        cand = [e for e in s["entries"] if "1" in e["follow"]]
        cand.sort(key=lambda e: (-e["follow"]["1"]["vs_index_pp"], e["name"]))
        top = {"day": s["day"], "followed_day": cand[0]["follow"]["1"]["day"], "cases": len(cand),
               "regime": (s.get("market") or {}).get("regime"),
               "rows": [{k: e.get(k) for k in ("ticker", "name", "sector", "strategies", "strong", "signals", "labels",
                                               "criteria", "volume_ratio", "rsi14", "stoch_k", "stoch_d", "r",
                                               "support_r", "resistance_r", "report_5d", "features", "regime")}
                        | {"follow": e["follow"]["1"]} for e in cand[:TOP_N]]}
    # Mönstren per fönster: senaste 2 och 6 veckorna, samma läge som det senaste, hela perioden.
    last_day = last["day"] if last else None
    regime = today["regime"] if today else None
    pats = {}
    for key, text, days in WINDOWS:
        sub = _window_rows(eps, key, days, last_day, regime)
        pats[key] = {"text": text if key != "lage" else f"{text} ({regime_text(regime).lower()})", "regime": regime
                     if key == "lage" else None, "days": sorted({r["day"] for r in sub}),
                     **{str(h): patterns(sub, str(h), texts) for h in PATTERN_HORIZONS}}
    # Lyckade uppgångar och alla avgjorda (för jämförelsen), ett fall per uppgång på dagar med full uppföljning.
    decided = []
    successes = []
    decided_rows = []
    for r in eps:
        st = r["status"]
        if st is None:
            continue
        decided_rows.append(r)
        decided.append({"day": r["day"], "strategies": r["strategies"], "strong": r["strong"], "sector": r["sector"],
                        "regime": r.get("regime"), "status": st})
        if st == "lyckad":
            w = r["won"]
            successes.append({k: r.get(k) for k in ("day", "ticker", "name", "sector", "strategies", "strong",
                                                    "signals", "labels", "criteria", "volume_ratio", "rsi14", "stoch_k",
                                                    "r", "support_r", "resistance_r", "report_5d", "features",
                                                    "regime", "episode_days", "success_day")}
                             | {**success_info(w),
                                "follow": {k: w["follow"][k] for k in sorted(w["follow"]) if int(k) <= SUCCESS_DAYS}})
    successes.sort(key=lambda x: (x["day"], x["name"]), reverse=True)
    recent_first = (date.fromisoformat(last_day) - timedelta(days=SIMILAR_DAYS)).isoformat() if last_day else ""
    recent = [x for x in decided if x["day"] >= recent_first]
    by_regime = {}
    for x in recent:
        by_regime.setdefault(x["regime"] or "okänt", []).append(x)
    by_strategy = {k: [x for x in recent if k in x["strategies"]] for k in STRATEGY_ORDER}
    by_strategy["stark"] = [x for x in recent if x["strong"]]
    # Larmen: dagens kandidater (senaste ögonblicksbilden) som liknar minst SIMILAR_MIN nyligen lyckade fall.
    similar = []
    if last:
        for e in last["entries"]:
            if not e["strategies"]:
                continue
            sim = similar_cases(e, last["day"], decided_rows)
            ok = [x for x in sim if x["status"] == "lyckad"]
            companies = sorted({x["name"] for x in ok})
            if len(companies) >= SIMILAR_MIN:
                similar.append({"ticker": e["ticker"], "name": e["name"], "strategies": e["strategies"],
                                "matches": len(companies), "similar_cases": len(sim), "similar_successes": len(ok),
                                "examples": companies[:3]})
    similar.sort(key=lambda x: (-x["matches"], x["name"]))
    # Kortsiktigt översåld i upptrend: dagens bolag (senaste ögonblicksbilden), med marknadsläget och de lägen där
    # RSI(2) under 10 prövats i underlaget (uppgångar med etiketten, på dagar med full uppföljning).
    tried: dict[str, list[dict]] = {}
    for r in decided_rows:
        if "etikett:rsi2-under-10" in r["features"]:
            tried.setdefault(r.get("regime") or "okänt", []).append(r)
    oversold = {"companies": (last or {}).get("oversold") or [], "regime": regime,
                "regime_text": regime_text(regime) if regime else None,
                "tried": {k: _rate(v) for k, v in sorted(tried.items())}}
    return {
        "schema_version": 4, "generated_at": now.isoformat(timespec="seconds"),
        "period": {"start": snaps[0]["day"] if snaps else None, "end": last_day},
        "snapshots": len(snaps), "entries": len(rows),
        # Underlaget för mönster och lyckade fall: dagar med full uppföljning, ett fall per uppgång.
        "full_days": sorted(full), "episodes": len(eps),
        "thresholds": {"top_share": TOP_SHARE, "top_n": TOP_N, "hold_weeks": HOLD_WEEKS,
                       "min_feature_cases": MIN_FEATURE_CASES, "horizons": list(HORIZONS),
                       "success_pp": SUCCESS_PP, "success_days": SUCCESS_DAYS,
                       "similar_days": SIMILAR_DAYS, "similar_min": SIMILAR_MIN, "similar_jaccard": SIMILAR_JACCARD,
                       "breadth_alert_pp": BREADTH_ALERT_PP, "sector_volume_alert": SECTOR_VOLUME_ALERT,
                       "sector_move_min": SECTOR_MOVE_MIN, "sector_move_pp": SECTOR_MOVE_PP,
                       "oversold_rsi2": OVERSOLD_RSI2,
                       "vol_calm_max": VOL_CALM_MAX, **FEATURE_THRESHOLDS},
        "feature_texts": texts,
        "regime_texts": {f"{t}-{v}": regime_text(f"{t}-{v}") for t in TREND_TEXT for v in VOL_TEXT},
        "market_today": today,
        "market_week_ago": week_ago,
        "market_history": [{"day": m["day"], "regime": m["regime"], "above_ma50_pct": m["breadth"]["above_ma50_pct"],
                            "vs_ma50_pct": m["index"]["vs_ma50_pct"], "vol20": m["index"]["vol20"],
                            "new_highs": m["new_highs"], "new_lows": m["new_lows"]} for m in market],
        "top": top,
        "patterns": pats,
        "successes": successes,
        "success_recent": {"since": recent_first, "all": _rate(recent),
                           "by_regime": {k: _rate(v) for k, v in sorted(by_regime.items())},
                           "by_strategy": {k: _rate(v) for k, v in by_strategy.items()}},
        "cases_index": [[x["day"], x["strategies"], x["strong"], x["sector"], x["regime"], x["status"]] for x in decided],
        "alerts": {"swing_date": last.get("swing_date") if last else None, "day": last_day, "similar": similar,
                   "market": (today or {}).get("signs", []) if today and today["day"] == last_day else [],
                   "sector_moves": (today or {}).get("sector_moves", []) if today and today["day"] == last_day else [],
                   "oversold": oversold},
        "note": "Mönstren och de lyckade fallen räknar bara signaldagar med full femdagarsuppföljning och ett fall per "
                "uppgång (samma bolag inom 5 handelsdagar räknas en gång). Dagens bevakningslista (bolag som avnoterats "
                "saknas). Fallen är inte oberoende. Utan courtage. Mönstren och de lyckade fallen beskriver vad som hänt, "
                "inte vad som kommer att hända.",
    }


def alerts_notice(summ: dict | None, swing_date: str | None) -> dict | None:
    """Larmen för notisen vid Bollingerkontrollerna (scripts/bollinger.py), eller None om sammanställningen inte gäller
    dagens swingfil. {"similar", "market", "notice"}."""
    a = (summ or {}).get("alerts") or {}
    if not swing_date or a.get("swing_date") != swing_date:
        return None
    parts = []
    if a.get("similar"):
        names = ", ".join(f"{x['name']} ({x['similar_successes']} av {x['similar_cases']} liknande lyckades)"
                          for x in a["similar"][:6])
        more = f" och {len(a['similar']) - 6} till" if len(a["similar"]) > 6 else ""
        parts.append(f"Liknar nyligen lyckade fall: {names}{more}.")
    if a.get("market"):
        parts.append("Marknadstecken (gårdagens stängning): " + "; ".join(x["text"] for x in a["market"]) + ".")
    ov = a.get("oversold") or {}
    if ov.get("companies"):
        names = ", ".join(x["name"] for x in ov["companies"][:6])
        more = f" och {len(ov['companies']) - 6} till" if len(ov["companies"]) > 6 else ""
        tried = ", ".join(regime_text(k).lower() for k in ov.get("tried", {})) or "inget läge än"
        parts.append(f"Kortsiktigt översåld i upptrend: {names}{more} (läge nu: {(ov.get('regime_text') or '–').lower()}; "
                     f"mönstret prövat hittills i: {tried}).")
    return {"similar": a.get("similar", []), "market": a.get("market", []), "oversold": ov.get("companies", []),
            "notice": " ".join(parts)}


def load_summary(eval_dir: Path = EVAL_DIR) -> dict | None:
    p = eval_dir / "summary.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


# --- Filer och rådatarepot -----------------------------------------------------------------------


def load_snapshots(eval_dir: Path = EVAL_DIR) -> list[dict]:
    out = []
    for p in sorted(eval_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"Utvärdering: {p.name} kunde inte läsas ({exc}), hoppar över", file=sys.stderr)
    return out


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def save(snaps: list[dict], summ: dict, eval_dir: Path = EVAL_DIR) -> list[tuple[Path, str]]:
    """Skriver ögonblicksbilderna och sammanställningen; returnerar (fil, sökväg i rådatarepot) för de ändrade."""
    eval_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, obj in [(f"{s['day']}.json", s) for s in snaps] + [("summary.json", summ)]:
        p = eval_dir / name
        text = _dump(obj)
        old = p.read_text(encoding="utf-8") if p.exists() else None
        if name == "summary.json" and old is not None:
            # Bara tiden ändrad: ingen ny commit.
            prev = json.loads(old)
            if {**prev, "generated_at": None} == {**obj, "generated_at": None}:
                continue
        if old != text:
            p.write_text(text, encoding="utf-8")
            changed.append((p, f"{RAW_SUBDIR}/{name}"))
    return changed


def commit(changed: list[tuple[Path, str]], message: str, raw_dir: Path = DATA_DIR / "raw") -> str:
    """Committar och pushar de ändrade filerna i rådatarepot (scripts/backup_week.commit_files)."""
    if not changed:
        return "utvärderingen oförändrad"
    from backup_week import git

    # Filerna ligger redan på plats i rådatarepot: committa bara dem (samma steg som commit_files, utan kopiering).
    rels = [rel for _, rel in changed]
    if git(raw_dir, "add", "--", *rels).returncode != 0:
        return "git add misslyckades"
    if git(raw_dir, "diff", "--cached", "--quiet").returncode == 0:
        return "utvärderingen oförändrad"
    if git(raw_dir, "commit", "-q", "-m", message, "--", *rels).returncode != 0:
        return "git commit misslyckades"
    push = git(raw_dir, "push", "-q")
    if push.returncode != 0:
        pull = git(raw_dir, "pull", "--rebase", "-q")
        push = git(raw_dir, "push", "-q") if pull.returncode == 0 else pull
        if push.returncode != 0:
            return f"push misslyckades (commit finns lokalt i data/raw/): {push.stderr.strip()}"
    return f"pushad till veckans-finans-raw ({len(rels)} filer)"


def _sectors() -> dict[str, str]:
    try:
        return {s["id"]: s["name"] for s in json.loads((DATA_DIR / "sectors.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {}


def update(swing_files: list[dict], bars: dict, instruments: dict, now: datetime, eval_dir: Path = EVAL_DIR,
           signals_dir: Path = SIGNALS_DIR, keep_open: int | None = KEEP_OPEN_DAYS
           ) -> tuple[list[dict], dict, list[tuple[Path, str]]]:
    """Nya ögonblicksbilder ur swingfilerna (befintliga skrivs inte över), marknadsläget där det saknas, uppföljning av
    de öppna (keep_open kalenderdagar; None = alla) och en ny sammanställning. Returnerar (ögonblicksbilder,
    sammanställning, ändrade filer)."""
    snaps = {s["day"]: s for s in load_snapshots(eval_dir)}
    yahoo = {t: i.yahoo_ticker for t, i in instruments.items()}
    sector_of = {i.yahoo_ticker: i.sektor for i in instruments.values()}
    names = {i.yahoo_ticker: i.namn for i in instruments.values()}
    sectors = _sectors()
    for sw in swing_files:
        if sw.get("as_of") and sw["as_of"] not in snaps:
            snaps[sw["as_of"]] = snapshot(sw, latest_signals(sw["date"], signals_dir), yahoo, sectors, now)
        s0 = snaps.get(sw.get("as_of"))
        if s0 is not None and "oversold" not in s0 and bars.get(INDEX_TICKER):
            s0["oversold"] = oversold(sw, bars, yahoo)
    last = max((b.day for b in bars.get(INDEX_TICKER, [])), default=None)
    for s in snaps.values():
        if (s.get("market") or {}).get("version") != MARKET_VERSION:
            m = market_state(bars, sector_of, date.fromisoformat(s["day"]), sectors, names)
            if m:
                s["market"] = m
        for e in s["entries"]:
            e["regime"] = (s.get("market") or {}).get("regime")
        if last and (keep_open is None or (last - date.fromisoformat(s["day"])).days <= keep_open):
            follow_up(s, bars)
    all_snaps = sorted(snaps.values(), key=lambda s: s["day"])
    summ = summary(all_snaps, now)
    return all_snaps, summ, save(all_snaps, summ, eval_dir)


def oversold(swing: dict, bars: dict, yahoo: dict[str, str]) -> list[dict]:
    """Bolag i swingfilen med RSI(2) under OVERSOLD_RSI2 och justerad stängning över 200 dagars MA på signaldagen."""
    d = date.fromisoformat(swing["as_of"])
    out = []
    for c in swing["companies"]:
        b = bars.get(yahoo.get(c["ticker"], ""))
        if c.get("rsi2") is None or c["rsi2"] >= OVERSOLD_RSI2 or not b:
            continue
        j = next((n for n, x in enumerate(b) if x.day == d), None)
        if j is None or j < 199:
            continue
        ma200 = sum(x.adj_close for x in b[j - 199:j + 1]) / 200
        if b[j].adj_close > ma200:
            out.append({"ticker": c["ticker"], "name": c["name"], "sector": c.get("sector"), "rsi2": c["rsi2"],
                        "vs_ma200_pct": round((b[j].adj_close / ma200 - 1) * 100, 2)})
    return sorted(out, key=lambda x: x["rsi2"])


def morning_update(swing: dict, bars: dict, instruments: dict, now: datetime) -> str:
    """Morgonens steg (scripts/morning.py): dagens ögonblicksbild, uppföljningen och rådatarepot. Kastar inte."""
    try:
        snaps, summ, changed = update([swing], bars, instruments, now)
        msg = commit(changed, f"Swing-utvärdering {swing['as_of']}")
        top = summ.get("top") or {}
        mt = summ.get("market_today") or {}
        return (f"Swing-utvärdering: {len(snaps)} dagar, {summ['entries']} fall, i dag "
                f"{len(next((s for s in snaps if s['day'] == swing['as_of']), {'entries': []})['entries'])} bolag; "
                f"topp {len(top.get('rows', []))} ({top.get('day', '–')}); läge {mt.get('regime_text', '–')}; "
                f"{len(summ['successes'])} lyckade fall; larm {len(summ['alerts']['similar'])} liknande, "
                f"{len(summ['alerts']['market'])} marknadstecken; {msg}")
    except Exception as exc:  # noqa: BLE001 - utvärderingen får aldrig stoppa morgonen
        return f"Swing-utvärdering misslyckades: {type(exc).__name__}: {exc}"


# --- Claudes text (måndagar) ---------------------------------------------------------------------


def week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def accepted_numbers(obj) -> set[str]:
    """Skrivsätten för talen i sammanställningen som får stå i texten: varje tal med 0–2 decimaler och decimalkomma,
    andelar (0–1) också i procent, och datumens delar."""
    ok: set[str] = set()

    def add(x: float) -> None:
        a = abs(x)
        for d in (0, 1, 2):
            ok.add(f"{a:.{d}f}".replace(".", ","))
        ok.add(f"{a:g}".replace(".", ","))

    def walk(v) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            add(float(v))
            if isinstance(v, float) and 0 <= v <= 1:
                add(v * 100)
        elif isinstance(v, str):
            m = re.fullmatch(r"(\d{4})-W?(\d{2})(?:-(\d{2}))?", v)
            if m:
                ok.update({m[1], m[2], str(int(m[2]))} | ({m[3], str(int(m[3]))} if m[3] else set()))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk({k: v for k, v in obj.items() if k != "cases_index"} if isinstance(obj, dict) else obj)
    # Texter som "20 %", "95 %", "5 dagar" och kännetecknens texter (t.ex. RSI(14), 1,5×) ingår i summary.
    for t in (obj.get("feature_texts") or {}).values():
        ok.update(re.findall(r"\d+(?:,\d+)?", t))
    ok.update({"95", "20", "1", "3", "5", "10", "4"})
    return {x for x in ok if x}


def sentences(text: str) -> list[str]:
    """Meningarna (slutar med . ! eller ?, decimalkomma räknas inte)."""
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_note(note: dict, summ: dict, week: str) -> list[str]:
    """Fel i Claudes text (tom lista = godkänd)."""
    errors = []
    if note.get("week") != week:
        errors.append(f"week ska vara {week!r}")
    if note.get("label") != "Claudes tolkning":
        errors.append('label ska vara "Claudes tolkning"')
    text = note.get("text") or ""
    n = len(sentences(text))
    if not NOTE_MIN_SENTENCES <= n <= NOTE_MAX_SENTENCES:
        errors.append(f"texten har {n} meningar, ska ha {NOTE_MIN_SENTENCES}–{NOTE_MAX_SENTENCES}")
    if FORBIDDEN.search(text):
        errors.append("texten innehåller en uppmaning (köp, sälj, kursmål eller rekommendation)")
    if re.search(r"\bbäst", text, re.IGNORECASE) and "bästa rörelser" not in text and "20 % bästa" not in text:
        errors.append('"bäst" får bara stå i avsnittens namn ("Gårdagens bästa rörelser", "de 20 % bästa")')
    allowed = accepted_numbers(summ)
    rest = re.sub(r"\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}", lambda m: " " if all(
        x in allowed for x in re.findall(r"\d+", m[0])) else " OKÄNT-DATUM ", text)
    missing = [tok for tok in re.findall(r"\d+(?:,\d+)?", rest) if tok not in allowed]
    if "OKÄNT-DATUM" in rest:
        missing.append("ett datum")
    if missing:
        errors.append(f"tal som inte finns i underlaget: {sorted(set(missing))}")
    if note.get("summary_generated_at") != summ.get("generated_at"):
        errors.append("summary_generated_at ska vara sammanställningens generated_at")
    return errors


def first_business_day(today: date) -> date:
    """Veckans första handelsdag (måndag, eller nästa vardag när måndagen är helgdag)."""
    from workdays import non_business_days

    d = today - timedelta(days=today.weekday())
    while d.weekday() < 5 and d in non_business_days(d.year):
        d += timedelta(days=1)
    return d


def needs_note(today: date, eval_dir: Path = EVAL_DIR) -> bool:
    """Veckans första handelsdag (normalt måndag): saknas veckans text och finns det mönster att skriva om?"""
    if today != first_business_day(today):
        return False
    if (eval_dir / "notes" / f"{week_of(today)}.json").exists():
        return False
    p = eval_dir / "summary.json"
    if not p.exists():
        return False
    summ = json.loads(p.read_text(encoding="utf-8"))
    return bool(summ.get("market_today")) or summ.get("entries", 0) > 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--efterhand", action="store_true")
    g.add_argument("--kontrollera", metavar="VECKA")
    g.add_argument("--klar", metavar="VECKA")
    g.add_argument("--behover-text", action="store_true")
    g.add_argument("--vecka", action="store_true", help="skriver dagens ISO-vecka (för run_swing_note.ps1)")
    p.add_argument("--cache", type=Path, help="pickle med kurser (test)")
    args = p.parse_args(argv)
    now = datetime.now(TZ)
    if args.vecka:
        print(week_of(now.date()))
        return 0
    if args.behover_text:
        return 0 if needs_note(now.date()) else 1
    if args.kontrollera or args.klar:
        week = args.kontrollera or args.klar
        draft = NOTE_DIR / f"{week}.json"
        if not draft.exists():
            print(f"{draft} saknas", file=sys.stderr)
            return 1
        summ = json.loads((EVAL_DIR / "summary.json").read_text(encoding="utf-8"))
        errors = check_note(json.loads(draft.read_text(encoding="utf-8")), summ, week)
        for e in errors:
            print(f"FEL: {e}")
        if errors:
            return 1
        if args.kontrollera:
            print("Texten är godkänd.")
            return 0
        dst = EVAL_DIR / "notes" / f"{week}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
        print(commit([(dst, f"{RAW_SUBDIR}/notes/{week}.json")], f"Swing-utvärdering: text {week}"))
        draft.unlink()
        return 0
    # --efterhand: ögonblicksbilder ur alla sparade swingfiler, en hämtning för uppföljningen.
    import pickle

    from validate import listed, load_instruments

    instruments = load_instruments()
    files = []
    for f in sorted(SWING_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")):
        files.append(json.loads(f.read_text(encoding="utf-8")))
    if not files:
        print("Inga swingfiler.")
        return 1
    if args.cache and args.cache.exists():
        bars = pickle.loads(args.cache.read_bytes())
    else:
        from signals import yahoo_bars

        first = min(date.fromisoformat(f["as_of"]) for f in files)
        tickers = sorted({i.yahoo_ticker for i in listed(instruments, now.date()).values()
                          if i.yahoo_ticker.endswith(".ST")})
        # Marknadsläget behöver 52 veckor (och 200 dagars MA) före den första signaldagen.
        bars = yahoo_bars([*tickers, INDEX_TICKER], first - timedelta(days=400), now.date() - timedelta(days=1))
        if args.cache:
            args.cache.write_bytes(pickle.dumps(bars))
    snaps, summ, changed = update(files, bars, instruments, now, keep_open=None)
    print(f"{len(snaps)} ögonblicksbilder, {summ['entries']} fall; {commit(changed, 'Swing-utvärdering i efterhand')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
