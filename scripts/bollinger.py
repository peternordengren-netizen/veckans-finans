"""Bollingerkontroll under handelsdagen: bolag vid eller över det övre Bollingerbandet, och inom 1 % under det.

Körs av scripts/run_bollinger.ps1 (schemalagd vardagar 09:20 och 13:20, utan Claude), som visar en Windows-notis
och bygger om sidan ("Bollinger" överst på startsidan). Ett informationsverktyg: listan beskriver var kursen ligger
mot bandet och är ingen uppmaning.

- Kurserna: ett batchanrop till Yahoo (scripts/signals.yahoo_bars) för alla bolag i instruments.csv. Dagens senaste
  kurs är fördröjd ca 15 minuter (Yahoo). Ett bolag utan kurs i dag (ingen handel än) räknas inte.
- Övre bandet (upper_band): medelvärdet av de 20 senaste stängningskurserna plus 2 standardavvikelser (populationens,
  som i Bollingers definition). Under dagen är den aktuella kursen den tjugonde, efter de 19 senaste stängningarna;
  i fliken Swing (scripts/swing.py) gårdagens stängning efter de 19 före. Under dagen används faktiska kurser
  (dagens kurs är inte justerad); i Swing justerade stängningar som övriga mått.
- "Vid eller över": kursen ≥ övre bandet. "Inom 1 %": kursen under bandet men högst NEAR_PCT % under.
- Pullback-kandidater ("pullback"): bolagen med en etikett ur strategin Pullback i trend i dagens swingfil
  (data/swing/<datum>.json). För dem räknas båda banden med dagens kurs (position()): läget mellan banden
  (%B = (kurs − undre) / (övre − undre); 0 = undre bandet, 1 = övre) och avståndet till övre bandet i R,
  (övre − kurs) / (referens − stopp) med referens och stopp ur swingfilen. ⚠ "vid eller över" när kursen ≥ övre
  bandet. Saknas dagens swingfil blir avsnittet null.
- Status i dag ("today" per pullback-kandidat, day_status): ur samma hämtning, dagens high, low och senaste kurs mot
  swingfilens nivåer. Triggernivån är gårdagens high (pullback.state.high), R = referens − stopp (strategins R).
  I prioritetsordning:
    "below_stop"  Ogiltig: under stopp        dagens low under stoppet
    "below_ma50"  Ogiltig: under 50 MA        senaste kursen under 50 dagars MA (gårdagens)
    "late"        Sen: mer än 1R över trigger senaste kursen över triggernivån + 1 R
    "triggered"   Trigger bruten              dagens high över triggernivån
    "waiting"     Väntar på trigger           dagens high högst triggernivån
  Dagens high och low gäller hela dagen fram till kontrollen, så "trigger bruten" och "under stopp" står kvar
  resten av dagen. Tiden då triggern först sågs (trigger_seen_at) är den kontroll där det syntes första gången;
  den exakta tiden är okänd. Kurserna är fördröjda ca 15 minuter.
- Ändringar sedan föregående kontroll samma dag (data/bollinger/<datum>-<hhmm>.json): "changes" och texten
  "notice" till Windows-notisen. Vid dagens första kontroll räknas alla som väntande innan.

Skriver data/bollinger/<datum>-<hhmm>.json (git-ignorerad).

Användning:
    python scripts/bollinger.py                # kontrollen nu
    python scripts/bollinger.py --handelsdag   # bara kontrollen av börsdag: exit 0 = handelsdag, 3 = inte
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from morning import is_business_day  # noqa: E402
from signals import BarFetcher, yahoo_bars  # noqa: E402
from validate import DATA_DIR, Instrument, listed, load_instruments  # noqa: E402

STOCKHOLM = ZoneInfo("Europe/Stockholm")
BOLLINGER_DIR = DATA_DIR / "bollinger"
PERIOD = 20
WIDTH = 2.0
NEAR_PCT = 1.0
FETCH_DAYS = 45  # 20 handelsdagar och marginal för helger och helgdagar
DELAY_NOTE = "Kurserna från Yahoo är fördröjda ca 15 minuter."
# Status i dag för pullback-kandidaterna (day_status), i prioritetsordning. Beskrivande, inga uppmaningar.
TODAY_STATUS = {
    "below_stop": "Ogiltig: under stopp",
    "below_ma50": "Ogiltig: under 50 MA",
    "late": "Sen: mer än 1R över trigger",
    "triggered": "Trigger bruten",
    "waiting": "Väntar på trigger",
}
INVALID = {"below_stop", "below_ma50"}


def bands(values: list[float], n: int = PERIOD, k: float = WIDTH) -> tuple[float, float] | None:
    """(undre, övre) Bollingerband för de n sista värdena (medel ∓ k populationsstandardavvikelser), None om färre än n."""
    if len(values) < n:
        return None
    last = values[-n:]
    mean, sd = statistics.fmean(last), statistics.pstdev(last)
    return mean - k * sd, mean + k * sd


def upper_band(values: list[float], n: int = PERIOD, k: float = WIDTH) -> float | None:
    """Övre Bollingerbandet för de n sista värdena (medel + k populationsstandardavvikelser), None om färre än n."""
    b = bands(values, n, k)
    return b[1] if b else None


def position(price: float, lower: float, upper: float, ref: float, stop: float) -> dict:
    """En pullback-kandidats läge mot banden: %B (0 = undre bandet, 1 = övre; utanför banden under 0 eller över 1),
    avståndet från kursen till övre bandet i R (R = referens − stopp; negativt över bandet) och om kursen är vid
    eller över övre bandet."""
    risk = ref - stop
    return {
        "price": round(price, 4), "lower": round(lower, 4), "upper": round(upper, 4),
        "pct_b": round((price - lower) / (upper - lower), 2) if upper > lower else None,
        "upper_r": round((upper - price) / risk, 2) if risk > 0 else None,
        "at_upper": price >= upper,
    }


def position_errors(p: dict, where: str) -> list[str]:
    """Läget räknas om ur kurs och band (avrundade värden, så med marginal); R kan inte räknas om här."""
    errors = []
    if p["lower"] > p["upper"]:
        errors.append(f"{where}: undre bandet {p['lower']} ligger över övre {p['upper']}")
    if p["at_upper"] != (p["price"] >= p["upper"]):
        errors.append(f"{where}: kursen {p['price']} mot övre bandet {p['upper']} stämmer inte med \"vid eller över\"")
    if p["pct_b"] is not None and p["upper"] > p["lower"]:
        if abs(p["pct_b"] - (p["price"] - p["lower"]) / (p["upper"] - p["lower"])) > 0.011:
            errors.append(f"{where}: %B {p['pct_b']} stämmer inte med kursen och banden")
    if p["upper_r"] is not None and (p["upper_r"] > 0 if p["at_upper"] else p["upper_r"] < 0):
        errors.append(f"{where}: avståndet {p['upper_r']} R har fel tecken mot \"vid eller över\"")
    return errors


def pullback_candidates(swing: dict | None) -> list[dict]:
    """Bolagen med en etikett ur strategin Pullback i trend och nivåer i swingfilen."""
    if not swing:
        return []
    labels = set((swing.get("strategies") or {}).get("pullback", {}).get("labels", []))
    return [c for c in swing["companies"]
            if labels & set(c["labels"]) and (c.get("pullback") or {}).get("levels")]


def load_previous(today: date, now: datetime, directory: Path | None = None) -> dict | None:
    """Föregående kontroll samma dag (senaste data/bollinger/<datum>-<hhmm>.json före now), eller None."""
    directory = directory or BOLLINGER_DIR
    earlier = sorted(p for p in directory.glob(f"{today.isoformat()}-[0-9][0-9][0-9][0-9].json")
                     if p.stem[-4:] < f"{now:%H%M}")
    if not earlier:
        return None
    try:
        return json.loads(earlier[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_swing(today: date) -> dict | None:
    """Dagens swingfil (data/swing/<datum>.json), eller None."""
    p = DATA_DIR / "swing" / f"{today.isoformat()}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def day_status(high: float, low: float, price: float, trigger: float, ref: float, stop: float, ma50: float) -> str:
    """Pullback-kandidatens status i dag (nyckel i TODAY_STATUS), i prioritetsordning: under stoppet, under 50 MA,
    mer än 1 R (referens − stopp) över triggernivån, trigger bruten, väntar."""
    if low < stop:
        return "below_stop"
    if price < ma50:
        return "below_ma50"
    if price > trigger + (ref - stop):
        return "late"
    if high > trigger:
        return "triggered"
    return "waiting"


def today_row(bar, c: dict, now: datetime, prev: dict | None) -> dict | None:
    """Status i dag för en kandidat ur dagens dagsrad (high, low, senaste kurs) och swingfilens nivåer; None utan
    nivåer. prev: kandidatens "today" i föregående kontroll samma dag (för tiden då triggern först sågs)."""
    st, lv = c["pullback"].get("state") or {}, c["pullback"]["levels"]
    if st.get("high") is None or st.get("ma50") is None:
        return None
    # Statusen räknas på de avrundade värdena som sparas, så att valideringen får samma utfall (ett high som bara
    # skiljer sig från triggernivån i femte decimalen räknas som på nivån).
    r = lambda x: round(x, 4)  # noqa: E731
    high, low, price, trigger = r(bar.high), r(bar.low), r(bar.close), r(st["high"])
    s = day_status(high, low, price, trigger, lv["ref"], lv["stop"], st["ma50"])
    seen = None
    if high > trigger:
        seen = (prev or {}).get("trigger_seen_at") or now.isoformat(timespec="seconds")
    return {"high": high, "low": low, "price": price, "trigger": trigger, "ref": lv["ref"],
            "stop": lv["stop"], "ma50": st["ma50"], "status": s, "trigger_seen_at": seen}


def status_changes(rows: list[dict], prev_rows: dict[str, dict] | None) -> list[dict]:
    """Kandidater vars status ändrats sedan föregående kontroll ({ticker, name, from, to}). Utan föregående kontroll
    (dagens första) räknas alla som väntande innan; saknades kandidaten i den föregående är from None."""
    out = []
    for r in rows:
        if not r.get("today"):
            continue
        if prev_rows is None:
            before = "waiting"
        else:
            before = ((prev_rows.get(r["ticker"]) or {}).get("today") or {}).get("status")
        if before != r["today"]["status"]:
            out.append({"ticker": r["ticker"], "name": r["name"], "from": before, "to": r["today"]["status"]})
    return sorted(out, key=lambda c: c["name"])


def _lower(key: str) -> str:
    text = TODAY_STATUS[key]
    return text[0].lower() + text[1:]


def status_notice(changes: list[dict], since: str | None, max_names: int = 8) -> str:
    """Notisens rad om status i dag: ändringarna sedan föregående kontroll (eller sedan morgonen)."""
    when = f"sedan kl. {datetime.fromisoformat(since):%H:%M}" if since else "sedan morgonen"
    if not changes:
        return f"Status i dag: inga ändringar {when}."
    parts = [f"{c['name']} {_lower(c['from']) + ' → ' if c['from'] else ''}{_lower(c['to'])}" for c in changes]
    more = f" och {len(parts) - max_names} till" if len(parts) > max_names else ""
    return f"Status i dag, ändringar {when}: " + "; ".join(parts[:max_names]) + more + "."


def status(price: float, upper: float, near_pct: float = NEAR_PCT) -> str | None:
    """"over" (vid eller över bandet), "near" (högst near_pct % under) eller None."""
    if price >= upper:
        return "over"
    if price >= upper * (1 - near_pct / 100):
        return "near"
    return None


def check(today: date, now: datetime, instruments: dict[str, Instrument], fetch: BarFetcher,
          swing: dict | None = None, previous: dict | None = None) -> dict:
    """previous: föregående kontroll samma dag (load_previous), för statusändringarna."""
    # Avnoterade bolag hoppas över (instruments.csv).
    tickers = {i.yahoo_ticker: t for t, i in listed(instruments, today).items()}
    bars = fetch(list(tickers), today - timedelta(days=FETCH_DAYS), today)
    over, near, missing = [], [], []
    today_bands: dict[str, tuple[float, float, float]] = {}  # ticker -> (kurs, undre, övre)
    today_bars = {}  # ticker -> dagens dagsrad (high, low, senaste kurs)
    for yt, t in tickers.items():
        b = bars.get(yt, [])
        if not b or b[-1].day != today:
            missing.append(t)
            continue
        price = b[-1].close
        bb = bands([x.close for x in b[:-1]][-(PERIOD - 1):] + [price])
        if bb is None:
            missing.append(t)
            continue
        today_bands[t] = (price, *bb)
        today_bars[t] = b[-1]
        upper = bb[1]
        st = status(price, upper)
        if st:
            row = {"ticker": t, "name": instruments[t].namn, "price": round(price, 4), "upper": round(upper, 4),
                   "vs_upper_pct": round((price / upper - 1) * 100, 2)}
            (over if st == "over" else near).append(row)
    for rows in (over, near):
        rows.sort(key=lambda r: -r["vs_upper_pct"])
    pb = None
    if swing is not None:
        prev_pb = (previous or {}).get("pullback")
        if prev_pb and prev_pb.get("swing_date") != swing["date"]:
            prev_pb = None  # en annan swingfil: andra kandidater och nivåer
        prev_rows = {r["ticker"]: r for r in prev_pb["rows"]} if prev_pb else None
        pb_rows = []
        for c in pullback_candidates(swing):
            if c["ticker"] not in today_bands:
                continue  # ingen kurs i dag: kolumnen visar swingfilens läge
            lv = c["pullback"]["levels"]
            price, lower, upper = today_bands[c["ticker"]]
            prev_today = ((prev_rows or {}).get(c["ticker"]) or {}).get("today")
            pb_rows.append({"ticker": c["ticker"], "name": c["name"],
                            **position(price, lower, upper, lv["ref"], lv["stop"]),
                            "today": today_row(today_bars[c["ticker"]], c, now, prev_today)})
        pb_rows.sort(key=lambda r: (not r["at_upper"], r["upper_r"] if r["upper_r"] is not None else 1e9))
        since = previous["checked_at"] if prev_pb else None
        changes = status_changes(pb_rows, prev_rows)
        pb = {"swing_date": swing["date"], "candidates": len(pullback_candidates(swing)), "rows": pb_rows,
              "status_since": since, "changes": changes, "notice": status_notice(changes, since)}
    return {
        "date": today.isoformat(),
        "checked_at": now.isoformat(timespec="seconds"),
        "delay_note": DELAY_NOTE,
        "thresholds": {"period": PERIOD, "width": WIDTH, "near_pct": NEAR_PCT},
        "checked": len(tickers) - len(missing),
        "missing": len(missing),
        "over": over,
        "near": near,
        "pullback": pb,
    }


def validate(data: dict) -> list[str]:
    """Statusen räknas om ur pris och band; listorna får inte överlappa. Pullback-kandidaternas läge räknas om."""
    errors = []
    for r in (data.get("pullback") or {}).get("rows", []):
        errors += position_errors(r, r["name"])
        td = r.get("today")
        if td:
            expected = day_status(td["high"], td["low"], td["price"], td["trigger"], td["ref"], td["stop"], td["ma50"])
            if td["status"] != expected:
                errors.append(f"{r['name']}: status {td['status']!r} stämmer inte med dagens kurser (väntat {expected!r})")
            if (td["trigger_seen_at"] is not None) != (td["high"] > td["trigger"]):
                errors.append(f"{r['name']}: tiden för bruten trigger stämmer inte med dagens high mot triggernivån")
    for key in ("over", "near"):
        for r in data[key]:
            if status(r["price"], r["upper"], data["thresholds"]["near_pct"]) != key:
                errors.append(f"{r['name']}: {r['price']} mot bandet {r['upper']} hör inte till {key!r}")
    both = {r["ticker"] for r in data["over"]} & {r["ticker"] for r in data["near"]}
    if both:
        errors.append(f"i båda listorna: {sorted(both)}")
    return errors


def main(argv: list[str] | None = None, fetch: BarFetcher = yahoo_bars, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--handelsdag", action="store_true", help="kontrollera bara om i dag är en handelsdag")
    args = p.parse_args(argv)
    now = now or datetime.now(STOCKHOLM)
    today = now.date()
    if args.handelsdag:
        return 0 if is_business_day(today) else 3
    if not is_business_day(today):
        print(f"{today} är ingen handelsdag – ingen Bollingerkontroll.")
        return 3
    data = check(today, now, load_instruments(), fetch, load_swing(today), load_previous(today, now))
    errors = validate(data)
    if errors:
        print("Bollingerkontrollen klarar inte valideringen – skrivs inte:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    if data["checked"] == 0:
        print("Inga kurser för i dag från Yahoo – ingen Bollingerkontroll skrivs.", file=sys.stderr)
        return 2
    out = BOLLINGER_DIR / f"{today}-{now:%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pb = data["pullback"]
    pb_text = (f", pullback-kandidater vid eller över {sum(r['at_upper'] for r in pb['rows'])} av {len(pb['rows'])}"
               if pb else ", ingen swingfil för i dag (inga pullback-kandidater)")
    print(f"Bollinger {now:%Y-%m-%d %H:%M}: {data['checked']} bolag, vid eller över övre bandet "
          f"{len(data['over'])}, inom 1 % {len(data['near'])}{pb_text} -> {out}")
    if pb:
        print(pb["notice"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
