"""Strategin "Rekyl efter Bollingerutbrott" för fliken Swing: etiketter, nivåer, rangkriterier och historik ur dagsdata.

Ett informationsverktyg: etiketterna och nivåerna beskriver ett läge och vad som hänt tidigare i liknande lägen. De
är inga uppmaningar.

Priser: justerade som övriga swingmått (scripts/pullback.adjusted). Dag i är den senaste handelsdagen i serien
("gårdagen" i morgonens fil). Bollingerbanden: 20 dagars stängningar, 2 standardavvikelser (populationens, som
scripts/bollinger.bands); mittbandet är 20 dagars glidande medelvärde.

1. Utbrottsdag: en av de BREAKOUT_DAYS handelsdagarna före dag i (i−7 … i−1) där stängningen ligger över övre bandet,
   omsättningen är minst volume_ratio (2) gånger snittet för de 20 dagarna före, och stängningen ligger över 50
   dagars MA. Finns flera gäller den senaste. Var utbrottsdagen en rapportdag (reaktionsdagen,
   scripts/swing.reaction_day) märks det, med utfallet mot förväntan (läggs till av swing.py).
2. Rekyl: dag i:s low ligger högst touch_pct % från mittbandet eller 20 dagars EMA, eller mellan dem. Ingen
   stängning efter utbrottsdagen (t.o.m. dag i) under utbrottsdagens low eller under dagens mittband.
   "lower_volume": snittomsättningen under rekylen (dagarna efter utbrottet t.o.m. dag i) är lägre än på
   utbrottsdagen (ett rangkriterium; med volymvillkor 3 nedan alltid uppfyllt).
   Volymvillkoren (volume_checks, RULES), alla krav för etiketten sedan 2026-11-10:
   r1 utbrottsdagens omsättning minst volume_ratio (2) gånger 20-dagarssnittet (grundregeln var base_volume_ratio, 1,5)
   r2 ingen röd dag i rekylen (stängning under öppningen) med omsättning över red_volume_ratio (1,5) gånger dagens
      20-dagarssnitt
   r3 snittomsättningen under rekyldagarna under 20-dagarssnittet före utbrottsdagen
   r4 summan av omsättningen på uppdagar i rekylen (stängning över föregående dags) minst lika stor som på neddagar
      (stängning under föregående dags); oförändrade dagar räknas inte
   Historiken prövar grundregeln, varje villkor för sig ovanpå den och alla tillsammans (labels med rules).
3. Vändsignal som i Pullback i trend (scripts/pullback.signal: hammer eller bullish engulfing). Etikett
   "Rekyl efter utbrott, vändsignal" i stället för "Rekyl efter utbrott". Triggernivå = dag i:s high.
4. Nivåer: referens = triggernivån vid vändsignal, annars dag i:s stängning (som Pullback). Stopp = rekylens lägsta
   low (dagarna efter utbrottet t.o.m. dag i). R = referens − stopp, samma R överallt. Mål: utbrottets högsta high
   (utbrottsdagen t.o.m. dag i) med avståndet i R, 2R och 3R, och övre bandet (scripts/bollinger.position).
5. Rang (CRITERIA, RANK_THRESHOLDS), en poäng per uppfyllt kriterium; vid lika poäng mest utrymme till målet i R
   (scripts/ranking.rank med room_r = (utbrottets high − referens) / R).
6. Historik: som Pullback (scripts/pullback.simulate): start nästa dags öppning, vid vändsignal när triggernivån nås
   inom 3 dagar; avgjort när 2R eller stoppet nås, högst 60 dagar; ett fall i taget per bolag.

Allt räknas ur fönstret (window): dagarna i−BREAKOUT_DAYS−1 … i med de mått reglerna behöver. Fönstret sparas i
swingfilen ("bbrekyl.state"), så att valideringen räknar om etiketten och nivåerna ur samma tal.
"""

from __future__ import annotations

import statistics

import pullback
import ranking

BREAKOUT_DAYS = 7
THRESHOLDS = {"breakout_days": BREAKOUT_DAYS, "volume_ratio": 2.0, "base_volume_ratio": 1.5, "red_volume_ratio": 1.5,
              "touch_pct": 1.0, "bb_period": 20, "bb_width": 2.0, "volume_days": 20}
# Volymvillkoren (punkt 2 i modulbeskrivningen): nyckel och kort text.
RULES = {
    "r1": "Utbrottsdagens omsättning minst 2× 20-dagarssnittet",
    "r2": "Ingen röd dag i rekylen med omsättning över 1,5× snittet",
    "r3": "Snittomsättningen i rekylen under 20-dagarssnittet",
    "r4": "Omsättning på uppdagar minst lika stor som på neddagar i rekylen",
}
ALL_RULES = tuple(RULES)
LABELS = {"bbrekyl": "Rekyl efter utbrott", "bbrekyl-vandsignal": "Rekyl efter utbrott, vändsignal"}
# Rangordningen: en poäng per uppfyllt kriterium (beskrivande, ingen värdering eller uppmaning).
CRITERIA = {
    "vandsignal": "Vändsignal",
    "lagre_omsattning": "Lägre omsättning i rekylen än på utbrottsdagen",
    "mal_2r": "Utbrottets högsta high minst 2R bort",
    "bollinger_1r": "Minst 1R kvar till övre Bollingerbandet",
    "stopp_atr": "Stoppet högst 1,5 ATR bort",
    "ma50_stigande": "50 MA stigande senaste 20 dagarna",
}
RANK_THRESHOLDS = {"target_r": 2.0, "bollinger_r": 1.0, "stop_atr": 1.5, "ma50_days": 20, "top": 5}
MIN_BARS = 70  # 50 dagars MA och 20 dagars snittomsättning för den äldsta dagen i fönstret


def window(o: list[float], h: list[float], lo: list[float], c: list[float], v: list[float],
           ema20: list[float | None], i: int, th: dict = THRESHOLDS) -> dict | None:
    """Fönstret för dag i (None utan tillräcklig historik): per dag öppning, high, low, stängning, omsättning,
    snittomsättningen de volume_days dagarna före, Bollingerbanden och 50 dagars MA; 20 dagars EMA för dag i."""
    n = th["breakout_days"] + 2  # dag i−breakout_days−1 … i: utbrottsdagarna, dag i och marginal (engulfing läser i−1)
    first = i - n + 1
    if first - max(49, th["bb_period"] - 1, th["volume_days"]) < 0 or ema20[i] is None:
        return None
    r = lambda x: round(x, 4)  # noqa: E731
    days = []
    for k in range(first, i + 1):
        # Som bollinger.bands (populationens standardavvikelse), med flyttal: historiken räknar fönstret för varje dag.
        last = c[k - th["bb_period"] + 1:k + 1]
        mid = sum(last) / len(last)
        sd = (sum((x - mid) ** 2 for x in last) / len(last)) ** 0.5
        base = v[k - th["volume_days"]:k]
        days.append({"open": r(o[k]), "high": r(h[k]), "low": r(lo[k]), "close": r(c[k]), "volume": v[k],
                     "volume_avg": round(sum(base) / len(base), 1), "mid": r(mid), "upper": r(mid + th["bb_width"] * sd),
                     "ma50": r(sum(c[k - 49:k + 1]) / 50)})
    return {"days": days, "ema20": r(ema20[i])}


def breakout(state: dict | None, th: dict = THRESHOLDS, rules: tuple[str, ...] = ALL_RULES) -> int | None:
    """Index i fönstret för den senaste utbrottsdagen (punkt 1), eller None. Omsättningskravet är volume_ratio med
    villkor r1, annars grundregelns base_volume_ratio."""
    if not state:
        return None
    ratio = th["volume_ratio"] if "r1" in rules else th.get("base_volume_ratio", th["volume_ratio"])
    days = state["days"]
    last = len(days) - 1
    for k in range(last - 1, last - th["breakout_days"] - 1, -1):
        d = days[k]
        if d["close"] > d["upper"] and d["volume_avg"] > 0 and d["volume"] >= ratio * d["volume_avg"] \
                and d["close"] > d["ma50"]:
            return k
    return None


def volume_checks(state: dict, k: int, th: dict = THRESHOLDS) -> dict:
    """Volymvillkoren r1–r4 för utbrottsdagen k, med siffrorna (visas på sidan och sparas i nivåerna)."""
    days = state["days"]
    b, rekyl = days[k], days[k + 1:]
    reds = [d["volume"] / d["volume_avg"] for d in rekyl if d["close"] < d["open"] and d["volume_avg"] > 0]
    up = sum(d["volume"] for p, d in zip(days[k:], rekyl) if d["close"] > p["close"])
    down = sum(d["volume"] for p, d in zip(days[k:], rekyl) if d["close"] < p["close"])
    avg_ratio = statistics.fmean(d["volume"] for d in rekyl) / b["volume_avg"] if b["volume_avg"] > 0 else None
    br = b["volume"] / b["volume_avg"] if b["volume_avg"] > 0 else 0.0
    return {
        "breakout_ratio": round(br, 2),
        "r1": br >= th["volume_ratio"],
        "red_days": len(reds),
        "red_max_ratio": round(max(reds), 2) if reds else None,
        "r2": all(x <= th["red_volume_ratio"] for x in reds),
        "rekyl_avg_ratio": round(avg_ratio, 2) if avg_ratio is not None else None,
        "r3": avg_ratio is not None and avg_ratio < 1,
        "up_volume": up,
        "down_volume": down,
        "r4": up >= down,
    }


def touched(state: dict, th: dict = THRESHOLDS) -> str | None:
    """Nivån dag i:s low nådde: "mid" (mittbandet), "ema20", "mellan" (mellan dem) eller None."""
    d = state["days"][-1]
    lo, mid, e = d["low"], d["mid"], state["ema20"]
    near = [(name, x) for name, x in (("mid", mid), ("ema20", e)) if x > 0 and abs(lo - x) / x * 100 <= th["touch_pct"]]
    if near:
        return min(near, key=lambda t: abs(lo - t[1]))[0]
    return "mellan" if min(mid, e) <= lo <= max(mid, e) else None


def held(state: dict, k: int) -> bool:
    """Ingen stängning efter utbrottsdagen k (t.o.m. dag i) under utbrottsdagens low eller under dagens mittband."""
    days = state["days"]
    return all(d["close"] >= days[k]["low"] and d["close"] >= d["mid"] for d in days[k + 1:])


def candle(state: dict) -> dict:
    """Dag i:s candle i den form pullback.signal läser (öppning, high, low, stängning och förra dagens)."""
    d, p = state["days"][-1], state["days"][-2]
    return {"open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"],
            "prev_open": p["open"], "prev_close": p["close"]}


def labels(state: dict | None, th: dict = THRESHOLDS, rules: tuple[str, ...] = ALL_RULES) -> list[str]:
    """[], ["bbrekyl"] eller ["bbrekyl-vandsignal"] (används även av historiken och valideringen). rules: volymvillkoren
    som krävs (alla för etiketten; historiken prövar dem var för sig, och () är grundregeln)."""
    k = breakout(state, th, rules)
    if k is None or touched(state, th) is None or not held(state, k):
        return []
    vc = volume_checks(state, k, th) if any(r in rules for r in ("r2", "r3", "r4")) else {}
    if any(not vc[r] for r in rules if r != "r1"):
        return []
    days = state["days"]
    stop = min(d["low"] for d in days[k + 1:])
    if _ref(state) - stop <= 0:
        return []  # inget R (stängning på rekylens lägsta low utan vändsignal)
    return ["bbrekyl-vandsignal"] if pullback.signal(candle(state)) else ["bbrekyl"]


def _ref(state: dict) -> float:
    d = state["days"][-1]
    return d["high"] if pullback.signal(candle(state)) else d["close"]


def levels(state: dict, atr: float | None, th: dict = THRESHOLDS, rules: tuple[str, ...] = ALL_RULES) -> dict:
    """Nivåerna för ett bolag med etikett (punkt 2 och 4 i modulbeskrivningen), med volymvillkoren och siffrorna."""
    days = state["days"]
    k = breakout(state, th, rules)
    b, d = days[k], days[-1]
    sig = pullback.signal(candle(state))
    ref = d["high"] if sig else d["close"]
    rekyl = days[k + 1:]
    stop = min(x["low"] for x in rekyl)
    target = max(x["high"] for x in days[k:])
    risk = ref - stop
    r = lambda x: round(x, 4)  # noqa: E731
    return {
        "level": touched(state, th), "signal": sig, "trigger": d["high"] if sig else None, "ref": r(ref),
        "stop": r(stop), "risk_pct": round(risk / ref * 100, 2), "risk_atr": round(risk / atr, 2) if atr else None,
        "target": r(target), "target_r": round((target - ref) / risk, 2),
        "r2": r(ref + 2 * risk), "r3": r(ref + 3 * risk),
        "breakout": {"days_ago": len(days) - 1 - k, "close": b["close"], "upper": b["upper"], "low": b["low"],
                     "volume_ratio": round(b["volume"] / b["volume_avg"], 2)},
        "lower_volume": statistics.fmean(x["volume"] for x in rekyl) < b["volume"],
        "volume": volume_checks(state, k, th),
    }


def measures(lv: dict, upper_r: float | None, c: list[float], i: int, th: dict = RANK_THRESHOLDS) -> dict:
    """Rangordningens mätvärden (sparas i swingfilen, så att valideringen kan räkna om). room_r och has_resistance
    i den form scripts/ranking.rank sorterar på (mest utrymme till målet först)."""
    n = th["ma50_days"]
    ma50 = lambda j: sum(c[j - 49:j + 1]) / 50  # noqa: E731
    return {
        "signal": lv["signal"] is not None,
        "lower_volume": lv["lower_volume"],
        "room_r": lv["target_r"],
        "has_resistance": True,
        "upper_r": upper_r,
        "stop_atr": lv["risk_atr"],
        "ma50_change_pct": round((ma50(i) / ma50(i - n) - 1) * 100, 2) if i - n >= 49 else None,
    }


def criteria(m: dict, th: dict = RANK_THRESHOLDS) -> dict[str, bool]:
    """Vilka kriterier som är uppfyllda (CRITERIA)."""
    return {
        "vandsignal": bool(m["signal"]),
        "lagre_omsattning": bool(m["lower_volume"]),
        "mal_2r": m["room_r"] is not None and m["room_r"] >= th["target_r"],
        "bollinger_1r": m["upper_r"] is not None and m["upper_r"] >= th["bollinger_r"],
        "stopp_atr": m["stop_atr"] is not None and m["stop_atr"] <= th["stop_atr"],
        "ma50_stigande": m["ma50_change_pct"] is not None and m["ma50_change_pct"] > 0,
    }


def rank(entries: list[dict]) -> None:
    """Rang 1..n: flest kriterier först, vid lika poäng mest utrymme till målet i R, sedan namnet."""
    ranking.rank(entries)


def simulate(o: list[float], h: list[float], lo: list[float], i: int, state: dict, stop: float) -> dict:
    """Ett historiskt fall från dag i (pullback.simulate med dag i:s candle och rekylens stopp)."""
    return pullback.simulate(o, h, lo, i, candle(state), stop)


def stop_of(state: dict, rules: tuple[str, ...] = ALL_RULES) -> float:
    """Rekylens lägsta low (stoppet) för ett fönster med etikett (med samma volymvillkor som etiketten)."""
    k = breakout(state, THRESHOLDS, rules)
    return min(d["low"] for d in state["days"][k + 1:])
