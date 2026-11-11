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
2. Uppföljning efter 1, 3 och 5 handelsdagar (HORIZONS) med samma hämtning som morgonens swingfil: rörelse
   (justerad stängning), rörelse mot index (OMX Stockholm PI) i procentenheter, högsta high under dagarna i R
   (från signaldagens stängning) och snittomsättningen under dagarna mot snittet för de 20 dagarna före signaldagen.
3. Sammanställningen (summary.json): "Gårdagens bästa rörelser" (topp 10 mot index efter 1 dag i den senaste
   ögonblicksbilden med 1-dagsuppföljning) och "Mönster": för varje kännetecken med minst MIN_FEATURE_CASES fall,
   andelen bland de 20 % (TOP_SHARE) med störst rörelse mot index jämfört med övriga, efter 3 och 5 dagar, med antal
   och 95 % konfidensintervall (Wilson). Ett mönster har "hållit" en vecka när andelen bland de 20 % var högre än
   bland övriga i den veckans fall (signaldagens ISO-vecka, de 20 % räknade inom veckan); markerat när det hållit
   HOLD_WEEKS (4) veckor i rad fram till den senaste veckan med uppföljning.
4. Måndagar (veckans första handelsdag, needs_note): Claude skriver 5–8 meningar om vad underlaget visar, märkt som
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
HORIZONS = (1, 3, 5)
PATTERN_HORIZONS = (3, 5)
TOP_SHARE = 0.2
TOP_N = 10
HOLD_WEEKS = 4
MIN_FEATURE_CASES = 10
VOLUME_AVG_DAYS = 20
REPORT_DAYS = 5
KEEP_OPEN_DAYS = 14  # ögonblicksbilder äldre än så (kalenderdagar) följs inte upp längre
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


def summary(snaps: list[dict], now: datetime) -> dict:
    """Sammanställningen för sidan och Claudes text (summary.json)."""
    texts: dict[str, str] = {}
    rows = []
    for s in sorted(snaps, key=lambda s: s["day"]):
        texts.update(s.get("feature_texts", {}))
        for e in s["entries"]:
            rows.append({**e, "day": s["day"]})
    # Gårdagens bästa rörelser: den senaste ögonblicksbilden med 1-dagsuppföljning.
    with1 = [s for s in snaps if any("1" in e["follow"] for e in s["entries"])]
    top = None
    if with1:
        s = max(with1, key=lambda s: s["day"])
        cand = [e for e in s["entries"] if "1" in e["follow"]]
        cand.sort(key=lambda e: (-e["follow"]["1"]["vs_index_pp"], e["name"]))
        top = {"day": s["day"], "followed_day": cand[0]["follow"]["1"]["day"], "cases": len(cand),
               "rows": [{k: e[k] for k in ("ticker", "name", "sector", "strategies", "strong", "signals", "labels",
                                           "criteria", "volume_ratio", "rsi14", "stoch_k", "stoch_d", "r",
                                           "support_r", "resistance_r", "report_5d", "features")}
                        | {"follow": e["follow"]["1"]} for e in cand[:TOP_N]]}
    return {
        "schema_version": 1, "generated_at": now.isoformat(timespec="seconds"),
        "period": {"start": min((s["day"] for s in snaps), default=None), "end": max((s["day"] for s in snaps), default=None)},
        "snapshots": len(snaps), "entries": len(rows),
        "thresholds": {"top_share": TOP_SHARE, "top_n": TOP_N, "hold_weeks": HOLD_WEEKS,
                       "min_feature_cases": MIN_FEATURE_CASES, "horizons": list(HORIZONS), **FEATURE_THRESHOLDS},
        "feature_texts": texts,
        "top": top,
        "patterns": {str(h): patterns(rows, str(h), texts) for h in PATTERN_HORIZONS},
        "note": "Dagens bevakningslista (bolag som avnoterats saknas). Fallen överlappar i tid och är inte oberoende. "
                "Utan courtage. Mönstren beskriver vad som hänt, inte vad som kommer att hända.",
    }


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
           signals_dir: Path = SIGNALS_DIR) -> tuple[list[dict], dict, list[tuple[Path, str]]]:
    """Nya ögonblicksbilder ur swingfilerna (befintliga skrivs inte över), uppföljning av de öppna och en ny
    sammanställning. Returnerar (ögonblicksbilder, sammanställning, ändrade filer)."""
    snaps = {s["day"]: s for s in load_snapshots(eval_dir)}
    yahoo = {t: i.yahoo_ticker for t, i in instruments.items()}
    sectors = _sectors()
    for sw in swing_files:
        if sw.get("as_of") and sw["as_of"] not in snaps:
            snaps[sw["as_of"]] = snapshot(sw, latest_signals(sw["date"], signals_dir), yahoo, sectors, now)
    last = max((b.day for b in bars.get(INDEX_TICKER, [])), default=None)
    for s in snaps.values():
        if last and (last - date.fromisoformat(s["day"])).days <= KEEP_OPEN_DAYS:
            follow_up(s, bars)
    all_snaps = sorted(snaps.values(), key=lambda s: s["day"])
    summ = summary(all_snaps, now)
    return all_snaps, summ, save(all_snaps, summ, eval_dir)


def morning_update(swing: dict, bars: dict, instruments: dict, now: datetime) -> str:
    """Morgonens steg (scripts/morning.py): dagens ögonblicksbild, uppföljningen och rådatarepot. Kastar inte."""
    try:
        snaps, summ, changed = update([swing], bars, instruments, now)
        msg = commit(changed, f"Swing-utvärdering {swing['as_of']}")
        top = summ.get("top") or {}
        return (f"Swing-utvärdering: {len(snaps)} dagar, {summ['entries']} fall, i dag "
                f"{len(next((s for s in snaps if s['day'] == swing['as_of']), {'entries': []})['entries'])} bolag; "
                f"topp {len(top.get('rows', []))} ({top.get('day', '–')}); {msg}")
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
    walk(obj)
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
    return any(summ["patterns"][h]["cases"] > 0 for h in summ.get("patterns", {}))


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
        bars = yahoo_bars([*tickers, INDEX_TICKER], first - timedelta(days=45), now.date() - timedelta(days=1))
        if args.cache:
            args.cache.write_bytes(pickle.dumps(bars))
    snaps, summ, changed = update(files, bars, instruments, now)
    print(f"{len(snaps)} ögonblicksbilder, {summ['entries']} fall; {commit(changed, 'Swing-utvärdering i efterhand')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
