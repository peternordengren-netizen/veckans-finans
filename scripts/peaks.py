"""Kursens läge mot toppnivåer: 52 veckors högsta och rekordnivån (högsta stängningen någonsin).

Ett informationsverktyg: flaggorna beskriver var kursen ligger och påverkar ingen rang eller kandidat.

- "Nära 52v-högsta": stängningen ligger högst NEAR_PCT (3) procent under den högsta stängningen de 52 veckorna före
  dagen (WEEKS52_DAYS kalenderdagar, dagen ingår inte). "Ny 52v-högsta": stängningen är högre än den.
- "Nära rekordnivå" / "Ny rekordnivå": samma sak mot den högsta stängningen någonsin före dagen, i Yahoos historik.
- Avståndet: (stängningen / toppnivån − 1) × 100, negativt under toppnivån och positivt över den.
- Stängningen är Yahoos "Close": justerad för splitar men inte för utdelningar, som kurserna brukar visas.

Rekordnivån per bolag räknas en gång i veckan på full kurshistorik (veckokörningen, `python scripts/peaks.py`, ett
batchanrop till Yahoo) och sparas i data/peaks/records.json. Mellan veckokörningarna läggs dagsdatans stängningar
efter den senaste hämtningen till. En split efter hämtningen syns som en ändrad stängning för den senaste hämtade
dagen; rekordnivån skalas då om med samma faktor.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate import DATA_DIR  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
PEAKS_DIR = DATA_DIR / "peaks"
RECORDS_FILE = PEAKS_DIR / "records.json"
NEAR_PCT = 3.0
WEEKS52_DAYS = 364  # 52 veckor i kalenderdagar
MIN_COVER_DAYS = 350  # 52v-flaggan kräver kurser från minst så här långt före dagen
SPLIT_TOLERANCE = 0.005  # ändrad stängning för den senaste hämtade dagen över så här: omräkning (split)
FULL_HISTORY_START = date(1970, 1, 1)
THRESHOLDS = {"near_pct": NEAR_PCT, "weeks52_days": WEEKS52_DAYS}
KINDS = {"52v": "52v-högsta", "rekord": "rekordnivå"}


def _flag(kind: str, close: float, level: float | None, level_day: date | None) -> dict | None:
    if level is None or level <= 0:
        return None
    dist = (close / level - 1) * 100
    if close > level:
        status = "ny"
    elif round(dist, 9) >= -NEAR_PCT:  # exakt 3 % under räknas (utan flyttalsavrundningens brus)
        status = "nära"
    else:
        return None
    return {"kind": kind, "status": status, "dist_pct": round(dist, 2), "level": round(level, 4),
            "level_day": level_day.isoformat() if level_day else None}


def high_52w(b: list, i: int) -> tuple[float, date] | None:
    """Högsta stängningen de 52 veckorna före dag i (dagen ingår inte); None med för kort historik."""
    d = b[i].day
    if not b or b[0].day > d - timedelta(days=MIN_COVER_DAYS):
        return None
    win = [x for x in b[:i] if x.day > d - timedelta(days=WEEKS52_DAYS + 1)]
    if not win:
        return None
    top = max(win, key=lambda x: x.close)
    return top.close, top.day


def prior_record(b: list, i: int, rec: dict | None) -> tuple[float, date | None] | None:
    """Högsta stängningen någonsin före dag i: den sparade rekordnivån (omräknad vid split) och dagsdatans stängningar
    efter den senaste hämtningen. None utan sparad nivå eller när den inte går att skilja från dagen."""
    if not rec:
        return None
    d = b[i].day
    last_day = date.fromisoformat(rec["last_day"])
    record_day = date.fromisoformat(rec["record_day"])
    ratio = 1.0
    at_last = next((x for x in b if x.day == last_day), None)
    if at_last is not None and rec.get("last_close"):
        r = at_last.close / rec["last_close"]
        if abs(r - 1) > SPLIT_TOLERANCE:
            ratio = r
    if last_day < d:
        level, day = rec["record"] * ratio, record_day
        for x in b[:i]:
            if x.day > last_day and x.close > level:
                level, day = x.close, x.day
        return level, day
    # Hämtningen gjordes efter dagen (t.ex. omräkning i efterhand): rekordet före dagen är känt bara när rekorddagen
    # ligger före dagen, eller är dagen själv (då gäller rekordet före den).
    if record_day < d:
        return rec["record"] * ratio, record_day
    if record_day == d and rec.get("prev_record"):
        day = rec.get("prev_record_day")
        return rec["prev_record"] * ratio, date.fromisoformat(day) if day else None
    return None


def flags(b: list, i: int, rec: dict | None = None) -> list[dict]:
    """Flaggorna för dag i (index i dagsdatan b): 52v-högsta och rekordnivå, "nära" eller "ny", med avståndet."""
    if i < 1 or not b[i].close:
        return []
    out = []
    h = high_52w(b, i)
    f = _flag("52v", b[i].close, *h) if h else None
    if f:
        out.append(f)
    r = prior_record(b, i, rec)
    f = _flag("rekord", b[i].close, *r) if r else None
    if f:
        out.append(f)
    return out


def flags_at(b: list | None, day: date, rec: dict | None = None) -> list[dict] | None:
    """flags() för dagen `day`; None om dagen saknas i dagsdatan."""
    if not b:
        return None
    i = next((n for n, x in enumerate(b) if x.day == day), None)
    return flags(b, i, rec) if i is not None else None


# --- Rekordnivåerna (veckokörningen) ------------------------------------------------------------------


def record_of(b: list) -> dict | None:
    """Rekordnivån i en full kurshistorik: högsta stängningen, dess dag, högsta stängningen före den dagen och den
    senaste dagens stängning (för split-kontrollen)."""
    b = [x for x in b if x.close and x.close > 0]
    if not b:
        return None
    k = max(range(len(b)), key=lambda n: b[n].close)
    prev = max(b[:k], key=lambda x: x.close) if k else None
    return {"record": round(b[k].close, 4), "record_day": b[k].day.isoformat(),
            "prev_record": round(prev.close, 4) if prev else None,
            "prev_record_day": prev.day.isoformat() if prev else None,
            "first_day": b[0].day.isoformat(), "last_day": b[-1].day.isoformat(), "last_close": round(b[-1].close, 4)}


def load_records(path: Path | None = None) -> dict[str, dict]:
    p = path or RECORDS_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("records", {}) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def build_records(bars: dict, instruments: dict, now: datetime, old: dict[str, dict] | None = None) -> dict:
    """Rekordnivån per bolag (ticker) ur full kurshistorik; ett bolag som saknas i hämtningen behåller sin gamla."""
    out, kept = {}, []
    for t, inst in instruments.items():
        rec = record_of(bars.get(inst.yahoo_ticker) or [])
        if rec:
            out[t] = rec
        elif old and t in old:
            out[t] = old[t]
            kept.append(t)
    return {"schema_version": 1, "generated_at": now.isoformat(timespec="seconds"), "source": "Yahoo (Close, full historik)",
            "kept_from_before": sorted(kept), "records": out}


def write_records(data: dict, path: Path | None = None) -> Path:
    p = path or RECORDS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def main(argv: list[str] | None = None, fetch=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    argparse.ArgumentParser(description="Rekordnivåerna per bolag (full kurshistorik, ett batchanrop)").parse_args(argv)
    from signals import yahoo_bars
    from validate import listed, load_instruments

    now = datetime.now(TZ)
    instruments = {t: i for t, i in listed(load_instruments(), now.date() - timedelta(days=1)).items()
                   if i.yahoo_ticker.endswith(".ST")}
    bars = (fetch or yahoo_bars)([i.yahoo_ticker for i in instruments.values()], FULL_HISTORY_START,
                                 now.date() - timedelta(days=1))
    data = build_records(bars, instruments, now, load_records())
    if not data["records"]:
        print("Rekordnivåer: ingen kurshistorik hämtades – filen skrivs inte", file=sys.stderr)
        return 1
    out = write_records(data)
    print(f"Rekordnivåer: {len(data['records'])} bolag ({len(data['kept_from_before'])} från förra hämtningen) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
