"""Vyn Rotationer: var pengarna rör sig, i Sverige och globalt.

Ett informationsverktyg: måtten beskriver rörelser bakåt i tiden och är inga uppmaningar.

- Svenska sektorer (bevakningslistans bolag per sektor i data/sectors.json): relativ styrka = snittet av bolagens
  utveckling (justerad stängning) minus OMX Stockholm PI:s, i procentenheter, senaste 5 och 20 handelsdagarna; och
  omsättning = sektorns samlade omsättning per dag senaste 5 respektive 20 dagarna mot snittet för de 60 dagarna
  före (kvot). Samma kursdata som swinglistan (ingen extra hämtning).
- Globala sektorer: de amerikanska sektor-ETF:erna (GLOBAL_SECTORS, Select Sector SPDR) mot SPY, på samma sätt.
- Räntor och valuta ur Riksbankens öppna API (SWEA): svensk och amerikansk 10-årig statsobligation (SEGVB10YC,
  USGVB10Y; förändring i räntepunkter) och USD/SEK (SEKUSDPMI; förändring i procent).
- Råvaror ur Yahoo: Brent (BZ=F) och koppar (HG=F), förändring i procent.
- "Topp förändringar": eftersom enheterna skiljer sig rangordnas posterna efter hur ovanlig 5-dagarsförändringen är
  (z = förändringen delad med standardavvikelsen för postens alla 5-dagarsförändringar det senaste året), störst
  först. Sektorerna rangordnas på sin relativa styrka.

Hämtning: ett batchanrop till Yahoo (ETF:erna, SPY och råvarorna) och ett anrop per Riksbanksserie (3), med tydlig
User-Agent och tidsgräns. Utfil: data/rotation/<datum>.json (datum = dagen sidan gäller; måtten = stängningen dagen
före). Körs i kvällsbygget (scripts/morning.py --kvall) och som reserv på morgonen.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prices import INDEX_TICKER  # noqa: E402
from validate import DATA_DIR  # noqa: E402

ROTATION_DIR = DATA_DIR / "rotation"
SPY = "SPY"
GLOBAL_SECTORS = {
    "XLK": "Teknik", "XLF": "Finans", "XLE": "Energi", "XLV": "Hälsovård", "XLI": "Industri",
    "XLY": "Sällanköpsvaror", "XLP": "Dagligvaror", "XLU": "Kraftförsörjning", "XLB": "Material",
    "XLRE": "Fastigheter", "XLC": "Kommunikation",
}
COMMODITIES = {"BZ=F": "Brent (olja)", "HG=F": "Koppar"}
RIKSBANK_SERIES = {"SEGVB10YC": ("Sverige 10 år", "bp"), "USGVB10Y": ("USA 10 år", "bp"), "SEKUSDPMI": ("USD/SEK", "pct")}
RIKSBANK_OBS_URL = "https://api.riksbank.se/swea/v1/Observations/{series}/{start}/{end}"
USER_AGENT = "veckans-finans-rotation/0.1 (+https://github.com/peternordengren-netizen/veckans-finans)"
TIMEOUT_S = 30
HORIZONS = (5, 20)
VOLUME_BASE_DAYS = 60
Z_YEAR = 252
TOP_N = 10


# --- Beräkningar --------------------------------------------------------------------------------------


def _z(series: list[float], k: int, value: float | None) -> float | None:
    """Hur ovanlig en k-dagarsförändring är: value / standardavvikelsen för seriens k-dagarsförändringar senaste året."""
    if value is None or len(series) < k + 30:
        return None
    tail = series[-(Z_YEAR + k):]
    diffs = [tail[i] - tail[i - k] for i in range(k, len(tail))]
    sd = statistics.pstdev(diffs) if len(diffs) > 2 else 0
    return round(value / sd, 2) if sd > 0 else None


def _volume_ratio(vol_days: list[float], k: int) -> float | None:
    """Snittomsättningen senaste k dagarna mot snittet för de VOLUME_BASE_DAYS dagarna före."""
    if len(vol_days) < k + VOLUME_BASE_DAYS:
        return None
    base = sum(vol_days[-(k + VOLUME_BASE_DAYS):-k]) / VOLUME_BASE_DAYS
    return round(sum(vol_days[-k:]) / k / base, 2) if base > 0 else None


def relative_group(members: list[list], bench: list, day: date) -> dict | None:
    """Relativ styrka och omsättning för en grupp mot ett jämförelseindex fram till day. members: listor med Bar.
    Den relativa styrkans dagsserie (snittet av bolagens dagsavkastning minus indexets, ackumulerad) ger z."""
    bpos = {b.day: i for i, b in enumerate(bench)}
    if day not in bpos:
        return None
    days = [b.day for b in bench[:bpos[day] + 1]]
    pos = [{b.day: i for i, b in enumerate(m)} for m in members]
    rel, vol = [0.0], []
    for j in range(1, len(days)):
        d0, d1 = days[j - 1], days[j]
        rets = [m[p[d1]].adj_close / m[p[d0]].adj_close - 1 for m, p in zip(members, pos) if d0 in p and d1 in p]
        iret = bench[bpos[d1]].adj_close / bench[bpos[d0]].adj_close - 1
        rel.append(rel[-1] + ((sum(rets) / len(rets) - iret) * 100 if rets else 0.0))
        vol.append(sum(m[p[d1]].volume for m, p in zip(members, pos) if d1 in p))
    out = {"companies": len(members)}
    for k in HORIZONS:
        if len(rel) <= k:
            return None
        rs = round(rel[-1] - rel[-1 - k], 2)
        out[f"rs{k}_pp"] = rs
        out[f"volume{k}_ratio"] = _volume_ratio(vol, k)
    out["z5"] = _z(rel, 5, out["rs5_pp"])
    return out


def level_item(key: str, name: str, group: str, unit: str, obs: list[tuple[date, float]]) -> dict | None:
    """En nivåserie (ränta, valuta, råvara): senaste värdet och förändringen 5 och 20 observationer bakåt (ränta i
    räntepunkter, övrigt i procent), med z för 5-dagarsförändringen."""
    if len(obs) < 21:
        return None
    vals = [v for _, v in obs]
    series = [v * 100 for v in vals] if unit == "bp" else [math.log(v) * 100 for v in vals]
    item = {"key": key, "name": name, "group": group, "unit": unit, "day": obs[-1][0].isoformat(),
            "last": round(vals[-1], 4)}
    for k in HORIZONS:
        item[f"chg{k}"] = round((vals[-1] - vals[-1 - k]) * 100, 1) if unit == "bp" else \
            round((vals[-1] / vals[-1 - k] - 1) * 100, 2)
    item["z5"] = _z(series, 5, series[-1] - series[-6])
    return item


def build(day: date, now: datetime, bars: dict, instruments: dict, sectors: dict[str, str],
          riksbank: dict[str, list[tuple[date, float]]], target: date) -> dict:
    """Rotationerna vid stängningen day, för sidan som gäller target."""
    by_sector: dict[str, list] = {}
    for inst in instruments.values():
        b = bars.get(inst.yahoo_ticker)
        if inst.yahoo_ticker.endswith(".ST") and b and inst.sektor:
            by_sector.setdefault(inst.sektor, []).append(b)
    items = []
    index = bars.get(INDEX_TICKER) or []
    for sec, members in by_sector.items():
        r = relative_group(members, index, day)
        if r:
            items.append({"key": f"se:{sec}", "name": sectors.get(sec, sec), "group": "Svensk sektor", "unit": "pe",
                          "benchmark": "OMX Stockholm PI", **r})
    spy = bars.get(SPY) or []
    for t, name in GLOBAL_SECTORS.items():
        if bars.get(t):
            last_us = max((b.day for b in spy), default=None)
            r = relative_group([bars[t]], spy, last_us) if last_us else None
            if r:
                items.append({"key": f"us:{t}", "name": f"{name} ({t})", "group": "Global sektor (USA)", "unit": "pe",
                              "benchmark": "S&P 500 (SPY)", "day": last_us.isoformat(), **r})
    for key, (name, unit) in RIKSBANK_SERIES.items():
        it = level_item(key, name, "Ränta" if unit == "bp" else "Valuta", unit, riksbank.get(key, []))
        if it:
            items.append(it)
    for t, name in COMMODITIES.items():
        obs = [(b.day, b.close) for b in bars.get(t, []) if b.close]
        it = level_item(t, name, "Råvara", "pct", obs)
        if it:
            items.append(it)
    ranked = sorted([i for i in items if i.get("z5") is not None], key=lambda i: -abs(i["z5"]))
    return {
        "schema_version": 1, "date": target.isoformat(), "as_of": day.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "thresholds": {"horizons": list(HORIZONS), "volume_base_days": VOLUME_BASE_DAYS, "z_year": Z_YEAR,
                       "top_n": TOP_N},
        "items": items,
        "top": [i["key"] for i in ranked[:TOP_N]],
        "sources": {"riksbank": {k: f"https://api.riksbank.se/swea/v1/Observations/{k}" for k in RIKSBANK_SERIES},
                    "yahoo": [*GLOBAL_SECTORS, SPY, *COMMODITIES]},
    }


def validate(data: dict) -> list[str]:
    """Fel i rotationsfilen: z stämmer med tecknet på förändringen, topplistan är rangordnad och utan dubbletter."""
    errors = []
    by_key = {i["key"]: i for i in data["items"]}
    if len(by_key) != len(data["items"]):
        errors.append("dubbletter bland posterna")
    for i in data["items"]:
        chg = i.get("rs5_pp", i.get("chg5"))
        if i.get("z5") is not None and chg is not None and chg != 0 and (i["z5"] > 0) != (chg > 0):
            errors.append(f"{i['name']}: z5 {i['z5']} har fel tecken mot förändringen {chg}")
    zs = [abs(by_key[k]["z5"]) for k in data["top"] if k in by_key]
    if len(zs) != len(data["top"]) or zs != sorted(zs, reverse=True):
        errors.append("topplistan är inte rangordnad efter |z5|")
    return errors


# --- Hämtning och skrivning ---------------------------------------------------------------------------


def fetch_riksbank(start: date, end: date, get=None) -> tuple[dict[str, list[tuple[date, float]]], list[str]]:
    """Ett anrop per serie. Returnerar ({serie: [(dag, värde)]}, fel)."""
    out, errors = {}, []
    for series in RIKSBANK_SERIES:
        url = RIKSBANK_OBS_URL.format(series=series, start=start, end=end)
        try:
            if get:
                raw = get(url)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    raw = resp.read()
            obs = [(date.fromisoformat(o["date"]), float(o["value"])) for o in json.loads(raw) if o.get("value") is not None]
            out[series] = sorted(obs)
        except Exception as exc:  # noqa: BLE001 - en serie som fallerar stoppar inte resten
            errors.append(f"Riksbanken {series}: {type(exc).__name__}")
    return out, errors


def write_rotation(target: date, day: date, now: datetime, instruments: dict, fetch, sectors: dict[str, str],
                   riksbank_get=None, out_dir: Path | None = None) -> Path | None:
    """Hämtar ETF:erna och råvarorna (ett batchanrop via fetch) och Riksbankens serier, bygger, validerar och skriver
    data/rotation/<target>.json. fetch: samma (cachade) hämtare som swinglistan, så de svenska kurserna inte hämtas
    igen. Fel här stoppar aldrig anroparen."""
    try:
        tickers = [i.yahoo_ticker for i in instruments.values() if i.yahoo_ticker.endswith(".ST")]
        bars = dict(fetch([*tickers, INDEX_TICKER], target - timedelta(days=400), target - timedelta(days=1)))
        bars.update(fetch([*GLOBAL_SECTORS, SPY, *COMMODITIES], target - timedelta(days=400), target - timedelta(days=1)))
        rb, errors = fetch_riksbank(target - timedelta(days=420), target - timedelta(days=1), riksbank_get)
        data = build(day, now, bars, instruments, sectors, rb, target)
        data["errors"] = errors
        problems = validate(data)
        if problems:
            print("Rotationer klarar inte valideringen – skrivs inte:", *problems, sep="\n  ", file=sys.stderr)
            return None
        out = (out_dir or ROTATION_DIR) / f"{target}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Rotationer {target} (stängning {day}): {len(data['items'])} poster, topp {len(data['top'])}, "
              f"{len(errors)} fel -> {out}")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"Rotationer kunde inte skrivas: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
