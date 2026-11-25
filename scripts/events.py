"""Händelser i sektorn: stora bolagsrörelser samma dag som ett eget pressmeddelande eller en rapport, och hur de
bolag som brukar röra sig tillsammans med bolaget rörde sig.

Ett informationsverktyg: händelserna beskriver vad som hänt och är inga uppmaningar.

a) Händelse: ett bolag i listan vars dagsrörelse mot index (OMX Stockholm PI, procentenheter) är minst ATR_MULT (3)
   gånger bolagets ATR (14 dagar, i procent av stängningen, dagen före), samma dag som ett eget pressmeddelande (MFN,
   Cision eller bolagets egen webbplats; rutinmeddelanden räknas inte) eller en rapport enligt rapportkalendern. "Samma
   dag" = publicerat efter förra handelsdagens stängning 17:30 och senast dagens 17:30.
b) Bedömning (Claude, Sonnet, /handelser via scripts/run_events.ps1): "bolagsspecifik" eller "branschgemensam" med en
   motivering på en mening, märkt "Claudes tolkning". Kontrolleras (check_judgements) innan den används.
c) Kollegor: bolag i listan vars dagsavkastning korrelerat med händelsebolagets det senaste året (CORR_DAYS
   handelsdagar före dagen, minst MIN_OVERLAP gemensamma dagar) med minst PEER_SECTOR_CORR (0,4) i samma sektor eller
   minst PEER_ANY_CORR (0,55) oavsett sektor; högst PEER_N (5), högst korrelation först. Uppfyller inget bolag kraven
   står det NO_PEERS_TEXT ("inga tydliga kollegor i listan"). "Följde med": rörde sig
   minst PEER_MOVE_PP (2) procentenheter mot index åt samma håll samma dag, utan eget pressmeddelande.
d) Uppföljningen av kollegorna efter 1, 3 och 5 dagar görs i utvärderingen (scripts/swing_eval.py).
e) Historiken på kursdata (scripts/swing_history.py events_history) använder samma rörelse- och kollegeregler utan
   nyhetsdata: en grov jämförelse.

Utfil: data/events/<datum>.json (datum = dagen sidan gäller, måtten = stängningen dagen före), i kvällsbygget.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prices import INDEX_TICKER  # noqa: E402
from validate import DATA_DIR, RAW_DIR  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
EVENTS_DIR = DATA_DIR / "events"
ATR_MULT = 3.0
ATR_DAYS = 14
PEER_N = 5
PEER_MOVE_PP = 2.0
PEER_SECTOR_CORR = 0.4  # kollega i samma sektor: korrelation minst så här
PEER_ANY_CORR = 0.55  # kollega oavsett sektor: korrelation minst så här
NO_PEERS_TEXT = "inga tydliga kollegor i listan"
CORR_DAYS = 252
MIN_OVERLAP = 200
CLOSE = time(17, 30)
CLASSES = ("bolagsspecifik", "branschgemensam")
JUDGEMENT_LABEL = "Claudes tolkning"
FORBIDDEN = re.compile(r"\b(köp|sälj|kursmål|rekommend)", re.IGNORECASE)
EVENT_HORIZONS = (1, 3, 5)  # kollegornas uppföljning (utvärderingen och historiken)
HISTORY_YEARS = 3  # historiken på kursdata (e)
THRESHOLDS = {"atr_mult": ATR_MULT, "atr_days": ATR_DAYS, "peer_n": PEER_N, "peer_move_pp": PEER_MOVE_PP,
              "peer_sector_corr": PEER_SECTOR_CORR, "peer_any_corr": PEER_ANY_CORR,
              "corr_days": CORR_DAYS, "min_overlap": MIN_OVERLAP}


# --- Kursmått ------------------------------------------------------------------------------------------


def atr_pct(b: list, i: int, n: int = ATR_DAYS) -> float | None:
    """ATR (snittet av true range för n dagar t.o.m. dag i) i procent av stängningen dag i."""
    if i < n or not b[i].close:
        return None
    tr = [max(b[j].high, b[j - 1].close) - min(b[j].low, b[j - 1].close) for j in range(i - n + 1, i + 1)]
    return sum(tr) / n / b[i].close * 100


def day_move(b: list, i: int) -> float | None:
    """Dagens utveckling i procent (justerad stängning)."""
    return (b[i].adj_close / b[i - 1].adj_close - 1) * 100 if i >= 1 and b[i - 1].adj_close else None


def returns_matrix(bars: dict, tickers: list[str], day: date, days: int = CORR_DAYS) -> tuple[list[str], np.ndarray]:
    """Dagsavkastningar (justerad stängning) de `days` handelsdagarna före day (day ingår inte), en rad per ticker,
    på indexets kalender; saknade dagar som NaN."""
    idx = [b.day for b in bars.get(INDEX_TICKER, []) if b.day < day][-(days + 1):]
    pos = {d: k for k, d in enumerate(idx)}
    m = np.full((len(tickers), len(idx) - 1), np.nan)
    for r, t in enumerate(tickers):
        closes = {b.day: b.adj_close for b in bars.get(t, [])}
        prev = None
        for d in idx:
            c = closes.get(d)
            if c and prev:
                m[r, pos[d] - 1] = c / prev - 1
            prev = c
    return tickers, m


def is_peer(corr: float, same_sector: bool) -> bool:
    """Kollegevillkoret: samma sektor och korrelation minst PEER_SECTOR_CORR, eller minst PEER_ANY_CORR oavsett sektor."""
    return corr >= PEER_ANY_CORR or (same_sector and corr >= PEER_SECTOR_CORR)


def top_peers(t: str, tickers: list[str], m: np.ndarray, sectors: dict[str, str | None], n: int = PEER_N,
              min_overlap: int = MIN_OVERLAP) -> list[tuple[str, float]]:
    """Högst n kollegor till t, de med högst korrelation först (Pearson på gemensamma dagar, minst min_overlap), bland
    bolagen som uppfyller is_peer; sectors = sektor per ticker i `tickers`. Kan bli tom."""
    i = tickers.index(t)
    x = m[i]
    own = sectors.get(t)
    out = []
    for j, u in enumerate(tickers):
        if j == i:
            continue
        ok = ~np.isnan(x) & ~np.isnan(m[j])
        if ok.sum() < min_overlap:
            continue
        a, b = x[ok], m[j][ok]
        if a.std() == 0 or b.std() == 0:
            continue
        corr = round(float(np.corrcoef(a, b)[0, 1]), 3)
        if is_peer(corr, own is not None and sectors.get(u) == own):
            out.append((u, corr))
    out.sort(key=lambda p: -p[1])
    return out[:n]


# --- Nyheter -------------------------------------------------------------------------------------------


def company_news(raw_dirs: list[Path], instruments: dict, since: datetime, until: datetime) -> dict[str, list[dict]]:
    """Egna pressmeddelanden per ticker publicerade i (since, until], med rådatafilen (för Claudes bedömning)."""
    import report_calendar

    patterns = report_calendar.name_patterns(instruments)
    weeks = {f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}" for d in (since.date(), until.date())}
    out: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for raw in raw_dirs:
        for wk in sorted(weeks):
            p = raw / wk / "index.json"
            if not p.exists():
                continue
            for it in json.loads(p.read_text(encoding="utf-8")).get("items", []):
                if it.get("source") not in ("mfn", "cision", "bolag") or it.get("routine"):
                    continue
                published = datetime.fromisoformat(it["published"])
                if not since < published <= until or it["url"] in seen:
                    continue
                data = it.get("data") or {}
                ticker = (data.get("ticker") if data.get("ticker") in instruments else None) if it["source"] == "bolag" \
                    else report_calendar.match_company(it, instruments, patterns)
                if ticker is None:
                    continue
                seen.add(it["url"])
                text = raw / wk / it["source"] / f"{it['id']}.txt"
                out.setdefault(ticker, []).append({
                    "published": published.isoformat(), "title": it["title"], "url": it["url"],
                    "source": it.get("source_name") or it["source"],
                    "raw_file": _rel(text) if text.exists() else None})
    for v in out.values():
        v.sort(key=lambda x: x["published"])
    return out


def _rel(p: Path) -> str:
    """Sökvägen relativt repots rot (för Claude, som arbetar i repots rot), med snedstreck."""
    try:
        return str(p.resolve().relative_to(DATA_DIR.parent.resolve())).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def reports_on(day: date, calendar_dir: Path | None = None) -> set[str]:
    """Bolag med rapport dagen day enligt rapportkalendern (data/calendar/<vecka>.json)."""
    y, w, _ = day.isocalendar()
    p = (calendar_dir or DATA_DIR / "calendar") / f"{y}-W{w:02d}.json"
    try:
        cal = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return set()
    return {e["ticker"] for e in cal.get("events", []) if e.get("kind") == "rapport" and e.get("date") == day.isoformat()}


# --- Händelserna ---------------------------------------------------------------------------------------


def detect(day: date, prev_day: date, bars: dict, instruments: dict, news: dict[str, list[dict]],
           reporting: set[str]) -> list[dict]:
    """Händelserna dag `day` (se modulbeskrivningen a och c)."""
    idx = bars.get(INDEX_TICKER) or []
    ipos = {b.day: i for i, b in enumerate(idx)}
    if day not in ipos:
        return []
    imove = day_move(idx, ipos[day])
    listed = {t: i for t, i in instruments.items() if i.yahoo_ticker.endswith(".ST") and bars.get(i.yahoo_ticker)}
    moves: dict[str, tuple[float, float, float]] = {}
    for t, inst in listed.items():
        b = bars[inst.yahoo_ticker]
        pos = {x.day: k for k, x in enumerate(b)}
        i = pos.get(day)
        if i is None or i < ATR_DAYS + 1 or b[i - 1].day != prev_day:
            continue
        mv, atr = day_move(b, i), atr_pct(b, i - 1)
        if mv is None or atr is None or imove is None:
            continue
        moves[t] = (float(mv), float(mv - imove), float(atr))  # vanliga tal (kurserna kan vara numpy-tal)
    candidates = [t for t, (_, rel, atr) in moves.items()
                  if abs(rel) >= ATR_MULT * atr and (t in news or t in reporting)]
    if not candidates:
        return []
    yts = [listed[t].yahoo_ticker for t in listed]
    names, m = returns_matrix(bars, yts, day)
    by_yt = {listed[t].yahoo_ticker: t for t in listed}
    sectors = {yt: instruments[t].sektor for yt, t in by_yt.items()}
    out = []
    for t in sorted(candidates, key=lambda t: -abs(moves[t][1])):
        mv, rel, atr = moves[t]
        peers = []
        for yt, corr in top_peers(listed[t].yahoo_ticker, names, m, sectors):
            u = by_yt[yt]
            pm = moves.get(u)
            prel = pm[1] if pm else None
            own = u in news or u in reporting
            followed = bool(prel is not None and not own and abs(prel) >= PEER_MOVE_PP and (prel > 0) == (rel > 0))
            same = instruments[t].sektor is not None and instruments[u].sektor == instruments[t].sektor
            peers.append({"ticker": u, "name": instruments[u].namn, "sector": instruments[u].sektor, "corr": corr,
                          "same_sector": same,
                          "move_pct": round(pm[0], 2) if pm else None, "vs_index_pp": round(prel, 2) if pm else None,
                          "own_news": own, "followed": followed})
        out.append({"ticker": t, "name": instruments[t].namn, "sector": instruments[t].sektor,
                    "move_pct": round(mv, 2), "vs_index_pp": round(rel, 2), "atr_pct": round(atr, 2),
                    "atr_mult": round(abs(rel) / atr, 2), "direction": "upp" if rel > 0 else "ned",
                    "report": t in reporting, "news": news.get(t, []), "peers": peers, "judgement": None})
    return out


def build(target: date, day: date, prev_day: date, now: datetime, bars: dict, instruments: dict,
          raw_dirs: list[Path], calendar_dir: Path | None = None) -> dict:
    """Händelsefilen för sidan som gäller target (stängningen day)."""
    since = datetime.combine(prev_day, CLOSE, TZ)
    until = datetime.combine(day, CLOSE, TZ)
    news = company_news(raw_dirs, instruments, since, until)
    events = detect(day, prev_day, bars, instruments, news, reports_on(day, calendar_dir))
    return {"schema_version": 1, "date": target.isoformat(), "as_of": day.isoformat(),
            "generated_at": now.isoformat(timespec="seconds"), "news_window": [since.isoformat(), until.isoformat()],
            "thresholds": dict(THRESHOLDS), "events": events}


def validate(data: dict) -> list[str]:
    """Fel i händelsefilen: ATR-villkoret, kollegornas "följde med" och bedömningarna."""
    errors = []
    for e in data["events"]:
        if e["atr_mult"] + 1e-6 < ATR_MULT or abs(abs(e["vs_index_pp"]) / e["atr_pct"] - e["atr_mult"]) > 0.02:
            errors.append(f"{e['name']}: rörelsen {e['vs_index_pp']} pe är inte minst {ATR_MULT:g} ATR ({e['atr_pct']} %)")
        if not e["news"] and not e["report"]:
            errors.append(f"{e['name']}: varken pressmeddelande eller rapport")
        if len(e["peers"]) > PEER_N:
            errors.append(f"{e['name']}: fler än {PEER_N} kollegor")
        for p in e["peers"]:
            if not is_peer(p["corr"], bool(p.get("same_sector"))):
                errors.append(f"{e['name']}: {p['name']} uppfyller inte kollegevillkoret (korrelation {p['corr']})")
            want = (p["vs_index_pp"] is not None and not p["own_news"] and abs(p["vs_index_pp"]) >= PEER_MOVE_PP
                    and (p["vs_index_pp"] > 0) == (e["vs_index_pp"] > 0))
            if p["followed"] != want:
                errors.append(f"{e['name']}: kollegan {p['name']} har fel \"följde med\"")
        j = e.get("judgement")
        if j is not None:
            errors += [f"{e['name']}: {x}" for x in judgement_errors(j)]
    return errors


# --- Claudes bedömning ---------------------------------------------------------------------------------


def judgement_errors(j: dict) -> list[str]:
    errors = []
    if j.get("class") not in CLASSES:
        errors.append(f"bedömningen ska vara {' eller '.join(CLASSES)}")
    reason = (j.get("reason") or "").strip()
    if not reason or len(re.findall(r"[.!?](\s|$)", reason)) != 1 or not reason.endswith((".", "!", "?")):
        errors.append("motiveringen ska vara en mening")
    if len(reason) > 300:
        errors.append("motiveringen är längre än 300 tecken")
    if FORBIDDEN.search(reason):
        errors.append("motiveringen innehåller en uppmaning (köp, sälj, kursmål eller rekommendation)")
    if j.get("label") != JUDGEMENT_LABEL:
        errors.append(f'label ska vara "{JUDGEMENT_LABEL}"')
    return errors


def check_judgements(data: dict, claude: dict) -> list[str]:
    """Fel i Claudes fil: en bedömning per händelse, med giltig klass och en menings motivering."""
    errors = []
    if claude.get("date") != data["date"]:
        errors.append(f"date ska vara {data['date']}")
    got = {j.get("ticker"): j for j in claude.get("judgements", [])}
    for e in data["events"]:
        j = got.get(e["ticker"])
        if j is None:
            errors.append(f"bedömning saknas för {e['name']} ({e['ticker']})")
            continue
        errors += [f"{e['name']}: {x}" for x in judgement_errors(j)]
    extra = set(got) - {e["ticker"] for e in data["events"]}
    if extra:
        errors.append(f"bedömningar för bolag utan händelse: {sorted(extra)}")
    return errors


def merge_judgements(data: dict, claude: dict) -> None:
    got = {j["ticker"]: j for j in claude["judgements"]}
    for e in data["events"]:
        j = got[e["ticker"]]
        e["judgement"] = {"class": j["class"], "reason": j["reason"].strip(), "label": JUDGEMENT_LABEL}


def needs_judgement(data: dict | None) -> bool:
    return bool(data) and any(e.get("judgement") is None for e in data["events"])


# --- Utfall och historik ------------------------------------------------------------------------------


def outcome_stats(vals: list[float]) -> dict:
    """Antal, snitt och median (procentenheter) och andelen åt händelsens håll (> 0) med 95 % intervall (Wilson)."""
    from vcp import wilson

    n = len(vals)
    s = sorted(vals)
    k = sum(v > 0 for v in vals)
    return {"n": n, "mean_pp": round(sum(vals) / n, 2) if n else None,
            "median_pp": round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2) if n else None,
            "same_dir": k, "same_dir_share": round(k / n, 3) if n else None,
            "ci": [round(x, 3) for x in wilson(k, n)] if n else None}


def history(bars: dict, instruments: dict, years: int = HISTORY_YEARS) -> dict:
    """e) Grov jämförelse på kursdata, de senaste `years` åren: varje dag ett bolag i listan rörde sig minst ATR_MULT
    gånger sin ATR mot index (utan nyhetsdata: alla sådana rörelser räknas, också marknads- och branschdagar), med ett
    fall per bolag inom 5 handelsdagar. Kollegorna väljs som i drift (top_peers: samma sektor och korrelation minst
    PEER_SECTOR_CORR eller minst PEER_ANY_CORR oavsett sektor, året före dagen, högst PEER_N). Deras rörelse mot index efter 1, 3 och 5 dagar från händelsedagens stängning, åt
    händelsebolagets håll, för dem som följde med samma dag (minst PEER_MOVE_PP åt samma håll) och övriga. Kollegornas
    egna nyheter kan inte uteslutas."""
    ib = bars.get(INDEX_TICKER) or []
    days = [b.day for b in ib]
    if len(days) < CORR_DAYS + 20:
        return {}
    pos = {d: k for k, d in enumerate(days)}
    ic = np.array([b.adj_close for b in ib], dtype=float)
    tick = [t for t, i in instruments.items() if i.yahoo_ticker.endswith(".ST") and bars.get(i.yahoo_ticker)]
    C = np.full((len(tick), len(days)), np.nan)
    A = np.full((len(tick), len(days)), np.nan)  # ATR i procent, dag k
    for r, t in enumerate(tick):
        b = bars[instruments[t].yahoo_ticker]
        for i, x in enumerate(b):
            k = pos.get(x.day)
            if k is None:
                continue
            C[r, k] = x.adj_close
            a = atr_pct(b, i)
            if a is not None:
                A[r, k] = a
    with np.errstate(invalid="ignore", divide="ignore"):
        R = C[:, 1:] / C[:, :-1] - 1  # R[:, k - 1] = dag k
    IR = ic[1:] / ic[:-1] - 1
    start = max(CORR_DAYS + 1, len(days) - 252 * years)
    groups = {g: {str(h): [] for h in EVENT_HORIZONS} for g in ("all", "followed", "not_followed")}
    by_dir = {d: {g: {str(h): [] for h in EVENT_HORIZONS} for g in ("followed", "not_followed")} for d in ("upp", "ned")}
    last_event = {r: -10 for r in range(len(tick))}
    n_events = n_peers = n_followed = n_no_peers = 0
    sectors = {t: instruments[t].sektor for t in tick}
    for k in range(start, len(days)):
        rel = (R[:, k - 1] - IR[k - 1]) * 100
        atr = A[:, k - 1]
        with np.errstate(invalid="ignore"):
            hits = np.where(np.abs(rel) >= ATR_MULT * atr)[0]
        if not len(hits):
            continue
        window = R[:, k - 1 - CORR_DAYS:k - 1]
        for r in hits:
            if k - last_event[r] <= 5:
                continue
            last_event[r] = k
            n_events += 1
            sign = 1 if rel[r] > 0 else -1
            peers = top_peers(tick[r], tick, window, sectors)
            n_no_peers += not peers
            for u_t, _ in peers:
                u = tick.index(u_t)
                if np.isnan(rel[u]) or np.isnan(C[u, k]):
                    continue
                followed = bool(abs(rel[u]) >= PEER_MOVE_PP and (rel[u] > 0) == (sign > 0))
                n_peers += 1
                n_followed += followed
                g = "followed" if followed else "not_followed"
                for h in EVENT_HORIZONS:
                    if k + h >= len(days) or np.isnan(C[u, k + h]):
                        continue
                    v = round(float(sign * ((C[u, k + h] / C[u, k] - 1) - (ic[k + h] / ic[k] - 1)) * 100), 4)
                    groups["all"][str(h)].append(v)
                    groups[g][str(h)].append(v)
                    by_dir["upp" if sign > 0 else "ned"][g][str(h)].append(v)
    return {
        "period": {"start": days[start].isoformat(), "end": days[-1].isoformat()},
        "thresholds": dict(THRESHOLDS), "horizons": list(EVENT_HORIZONS),
        "events": n_events, "peers": n_peers, "followed": n_followed, "events_without_peers": n_no_peers,
        "groups": {g: {h: outcome_stats(v) for h, v in hs.items()} for g, hs in groups.items()},
        "by_direction": {d: {g: {h: outcome_stats(v) for h, v in hs.items()} for g, hs in gs.items()}
                         for d, gs in by_dir.items()},
        "rough": True,
        "note": "Grov jämförelse utan nyhetsdata: alla rörelser på minst 3 ATR mot index räknas som händelser, också "
                "dagar då hela marknaden eller branschen rörde sig, och kollegornas egna nyheter kan inte uteslutas. "
                "Rörelsen räknas mot index och åt händelsebolagets håll (positivt = samma håll som händelsen). Ett fall "
                "per bolag inom 5 handelsdagar; kollegorna återkommer i flera fall och fallen överlappar i tid, så "
                "intervallen är för smala. Dagens bevakningslista (överlevnadsbias). Utan courtage.",
    }


# --- Filer ---------------------------------------------------------------------------------------------


def load(target: date | str, events_dir: Path | None = None) -> dict | None:
    p = (events_dir or EVENTS_DIR) / f"{target}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def latest(events_dir: Path | None = None) -> dict | None:
    d = events_dir or EVENTS_DIR
    files = sorted(f for f in d.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")) if d.exists() else []
    return load(files[-1].stem, d) if files else None


def write(data: dict, events_dir: Path | None = None) -> Path:
    out = (events_dir or EVENTS_DIR) / f"{data['date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def write_events(target: date, day: date, prev_day: date, now: datetime, bars: dict, instruments: dict,
                 raw_dirs: list[Path] | None = None, events_dir: Path | None = None) -> Path | None:
    """Bygger, validerar och skriver händelsefilen; en befintlig bedömning för samma händelse behålls. Fel här stoppar
    aldrig anroparen."""
    try:
        data = build(target, day, prev_day, now, bars, instruments, raw_dirs or [RAW_DIR, DATA_DIR / "morning" / "raw"])
        old = load(target, events_dir) or {}
        kept = {e["ticker"]: e.get("judgement") for e in old.get("events", [])}
        for e in data["events"]:
            e["judgement"] = kept.get(e["ticker"])
        problems = validate(data)
        if problems:
            print("Händelserna klarar inte valideringen – skrivs inte:", *problems, sep="\n  ", file=sys.stderr)
            return None
        out = write(data, events_dir)
        print(f"Händelser {target} (stängning {day}): {len(data['events'])} händelser, "
              f"{sum(p['followed'] for e in data['events'] for p in e['peers'])} kollegor följde med -> {out}")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"Händelserna kunde inte skrivas: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def fmt1(x: float, sign: bool = True) -> str:
    """En decimal med decimalkomma, avrundad som sidan (Intl.NumberFormat, halva bort från noll på talets kortaste
    decimalform: 12,25 -> 12,3, 2,15 -> 2,2), med tecken utom för noll (signDisplay "exceptZero"), så att notisen och sidan visar samma tal."""
    d = Decimal(repr(float(x))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if d == 0:
        d = abs(d)
    s = f"{d:+.1f}" if sign and d != 0 else f"{d:.1f}"
    return s.replace(".", ",").replace("-", "−")


def notice_text(data: dict | None, max_events: int = 3) -> str:
    """Notisens rad om händelserna (Bollingerkontrollerna 09:20 och 13:20); tom utan händelser."""
    if not data or not data.get("events"):
        return ""
    num = fmt1
    parts = []
    for e in data["events"][:max_events]:
        j = e.get("judgement")
        kind = f", {j['class']} enligt Claude" if j else ""
        fol = [p for p in e["peers"] if p["followed"]]
        if not e["peers"]:
            peers = f" – {NO_PEERS_TEXT}"
        else:
            peers = (" – följde med: " + ", ".join(f"{p['name']} {num(p['vs_index_pp'])}" for p in fol)) if fol else ""
        parts.append(f"{e['name']} {num(e['vs_index_pp'])} pe mot index ({num(e['atr_mult'], False)} ATR{kind}){peers}")
    more = f" och {len(data['events']) - max_events} till" if len(data["events"]) > max_events else ""
    return "Händelser i sektorn (gårdagens stängning): " + "; ".join(parts) + more + "."


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--behover-bedomning", metavar="DATUM", help="exit 0 om händelser saknar bedömning")
    g.add_argument("--kontrollera", metavar="DATUM", help="kontrollerar Claudes fil data/events/<datum>.claude.json")
    g.add_argument("--klar", metavar="DATUM", help="godkänd bedömning in i händelsefilen")
    g.add_argument("--senaste", action="store_true", help="skriver datumet för senaste händelsefilen")
    args = p.parse_args(argv)
    if args.senaste:
        d = latest()
        print(d["date"] if d else "")
        return 0 if d else 1
    target = args.behover_bedomning or args.kontrollera or args.klar
    data = load(target)
    if args.behover_bedomning:
        return 0 if needs_judgement(data) else 1
    if data is None:
        print(f"data/events/{target}.json saknas", file=sys.stderr)
        return 1
    cp = EVENTS_DIR / f"{target}.claude.json"
    if not cp.exists():
        print(f"{cp} saknas", file=sys.stderr)
        return 1
    claude = json.loads(cp.read_text(encoding="utf-8"))
    errors = check_judgements(data, claude)
    for e in errors:
        print(f"FEL: {e}")
    if errors:
        return 1
    if args.kontrollera:
        print("Bedömningarna är godkända.")
        return 0
    merge_judgements(data, claude)
    problems = validate(data)
    if problems:
        print("FEL:", *problems, sep="\n  ")
        return 1
    write(data)
    cp.unlink()
    print(f"Bedömningarna för {target} är sparade ({len(data['events'])} händelser).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
