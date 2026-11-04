"""Strategin "Pullback i trend" för fliken Swing: etiketter, nivåer och historik ur dagsdata.

Ett informationsverktyg: etiketterna och nivåerna beskriver ett läge och vad som hänt tidigare i
liknande lägen. De är inga uppmaningar.

Priser: justerade som övriga swingmått (open, high och low gånger justerad stängning / stängning).
Allt gäller den senaste handelsdagen i serien ("gårdagen" i morgonens fil).

1. Trend: stängningen över 50 och 200 dagars MA, och 50 dagars MA över 200 dagars MA.
2. Rekyl: gårdagens low högst touch_pct % från 20 dagars EMA eller 50 dagars MA, och stängningen över
   samma nivå. Når den båda används den lägre (MA-nivån). Etikett "Rekyl i trend".
3. Vändsignal: gårdagens candle är en hammer eller en bullish engulfing (definitioner i is_hammer och
   is_engulfing). Etikett "Rekyl med vändsignal" i stället för "Rekyl i trend". Triggernivå = gårdagens high.
4. Nivåer: referensnivå = triggernivån vid vändsignal, annars gårdagens stängning. Stopp = lägsta low under
   rekylen (sedan den högsta toppen de senaste stop_days dagarna), eller MA-nivån om den ligger lägre.
   R = referensnivå − stopp, i procent och i ATR(14). Föregående topp = högsta high top_days dagar bakåt.
   2R och 3R = referensnivå + 2 respektive 3 R.
5. Historik (history_cases): varje dag med etiketten under det senaste året, ett fall i taget per bolag.
   Start: nästa dags öppning; vid vändsignal i stället när triggernivån nås inom trigger_days dagar (till
   triggernivån, eller öppningen om den ligger över), annars "ej utlöst". Stopp och 2R räknas om från
   startkursen med samma stopp. Avgjort när 2R eller stoppet nås (nås båda samma dag räknas stoppet
   först; öppnar dagen bortom nivån gäller öppningen); efter max_days dagar "öppet". Courtage fee_pct %
   per affär (två per fall) dras från resultatet.
"""

from __future__ import annotations

from signals import Bar

THRESHOLDS = {"touch_pct": 1.0, "hammer_shadow": 2.0, "hammer_upper": 0.25, "stop_days": 10, "top_days": 60,
              "trigger_days": 3, "max_days": 60, "fee_pct": 0.2}
LABELS = {"rekyl-i-trend": "Rekyl i trend", "rekyl-vandsignal": "Rekyl med vändsignal"}
MIN_BARS = 200


def adjusted(bars: list[Bar]) -> tuple[list[float], list[float], list[float], list[float]]:
    """(open, high, low, close), justerade. Saknas öppningen används stängningen (ingen candle)."""
    f = [b.adj_close / b.close if b.close else 1.0 for b in bars]
    o = [(b.open if b.open is not None else b.close) * k for b, k in zip(bars, f)]
    return o, [b.high * k for b, k in zip(bars, f)], [b.low * k for b, k in zip(bars, f)], [b.adj_close for b in bars]


def ema(values: list[float], n: int) -> list[float | None]:
    """Exponentiellt glidande medelvärde (alfa 2/(n+1)), startat med det enkla medlet av de n första."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    e = sum(values[:n]) / n
    out[n - 1] = e
    k = 2 / (n + 1)
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def state(o: list[float], h: list[float], lo: list[float], c: list[float], ema20: list[float | None],
          i: int) -> dict | None:
    """Det som etiketterna avgörs av, för dag i (None utan MIN_BARS dagar)."""
    if i < MIN_BARS - 1 or ema20[i] is None:
        return None
    r = lambda x: round(x, 4)  # noqa: E731
    return {"open": r(o[i]), "high": r(h[i]), "low": r(lo[i]), "close": r(c[i]),
            "prev_open": r(o[i - 1]), "prev_close": r(c[i - 1]),
            "ema20": r(ema20[i]), "ma50": r(sum(c[i - 49:i + 1]) / 50), "ma200": r(sum(c[i - 199:i + 1]) / 200)}


def in_trend(p: dict) -> bool:
    return p["close"] > p["ma50"] and p["close"] > p["ma200"] and p["ma50"] > p["ma200"]


def touched(p: dict, th: dict = THRESHOLDS) -> tuple[str, float] | None:
    """Nivån rekylen nådde: ("ema20" | "ma50", värde); den lägre om båda, None om ingen."""
    hits = [(name, p[name]) for name in ("ema20", "ma50")
            if p[name] > 0 and abs(p["low"] - p[name]) / p[name] * 100 <= th["touch_pct"] and p["close"] > p[name]]
    return min(hits, key=lambda x: x[1]) if hits else None


def is_hammer(p: dict, th: dict = THRESHOLDS) -> bool:
    """Hammer: nedre skuggan minst hammer_shadow gånger kroppen och övre skuggan högst hammer_upper av dagens spann.
    Kropp = |stängning − öppning|; nedre skugga = min(öppning, stängning) − low; övre = high − max(öppning, stängning)."""
    rng = p["high"] - p["low"]
    if rng <= 0:
        return False
    body = abs(p["close"] - p["open"])
    lower = min(p["open"], p["close"]) - p["low"]
    upper = p["high"] - max(p["open"], p["close"])
    return lower >= th["hammer_shadow"] * body and lower > 0 and upper <= th["hammer_upper"] * rng


def is_engulfing(p: dict) -> bool:
    """Bullish engulfing: förra dagen stängde under öppningen, gårdagen över, och gårdagens kropp omsluter förra
    dagens (öppning ≤ förra stängningen och stängning ≥ förra öppningen, och kroppen är större)."""
    return (p["prev_close"] < p["prev_open"] and p["close"] > p["open"] and p["open"] <= p["prev_close"]
            and p["close"] >= p["prev_open"] and (p["close"] - p["open"]) > (p["prev_open"] - p["prev_close"]))


def signal(p: dict, th: dict = THRESHOLDS) -> str | None:
    """"hammer", "engulfing" eller None."""
    return "hammer" if is_hammer(p, th) else "engulfing" if is_engulfing(p) else None


def labels(p: dict | None, th: dict = THRESHOLDS) -> list[str]:
    """[], ["rekyl-i-trend"] eller ["rekyl-vandsignal"] (används även av historiken och valideringen)."""
    if p is None or not in_trend(p) or touched(p, th) is None:
        return []
    return ["rekyl-vandsignal"] if signal(p, th) else ["rekyl-i-trend"]


def stop_level(h: list[float], lo: list[float], i: int, ma_level: float, th: dict = THRESHOLDS) -> float:
    """Lägsta low sedan den högsta toppen de senaste stop_days dagarna (t.o.m. dag i), eller MA-nivån om lägre."""
    window = range(max(0, i - th["stop_days"] + 1), i + 1)
    top = max(window, key=lambda j: (h[j], j))
    return min(min(lo[top:i + 1]), ma_level)


def levels(h: list[float], lo: list[float], atr: float | None, i: int, p: dict, th: dict = THRESHOLDS) -> dict:
    """Nivåerna för ett bolag med etikett (punkt 4 i modulbeskrivningen)."""
    level_name, level_value = touched(p, th)
    sig = signal(p, th)
    ref = p["high"] if sig else p["close"]
    stop = stop_level(h, lo, i, level_value, th)
    risk = ref - stop
    r = lambda x: round(x, 4)  # noqa: E731
    return {
        "level": level_name, "level_value": r(level_value), "signal": sig,
        "trigger": r(p["high"]) if sig else None, "ref": r(ref), "stop": r(stop),
        "risk_pct": round(risk / ref * 100, 2) if ref else None,
        "risk_atr": round(risk / atr, 2) if atr else None,
        "top": r(max(h[max(0, i - th["top_days"] + 1):i + 1])),
        "r2": r(ref + 2 * risk), "r3": r(ref + 3 * risk),
    }


# --- Historik ------------------------------------------------------------------------


def simulate(o: list[float], h: list[float], lo: list[float], i: int, p: dict, stop: float,
             th: dict = THRESHOLDS) -> dict:
    """Ett fall från dag i: {"outcome": "2R" | "stopp" | "öppet" | "ej utlöst", "net_pct", "risk_pct", "end": index}."""
    n = len(o)
    if signal(p, th):
        start, entry = None, None
        for j in range(i + 1, min(n, i + 1 + th["trigger_days"])):
            if h[j] >= p["high"]:
                start, entry = j, max(p["high"], o[j])
                break
        if start is None:
            return {"outcome": "ej utlöst", "net_pct": None, "risk_pct": None, "end": min(n - 1, i + th["trigger_days"])}
    else:
        if i + 1 >= n:
            return {"outcome": "öppet", "net_pct": None, "risk_pct": None, "end": n - 1}
        start, entry = i + 1, o[i + 1]
    risk = entry - stop
    if risk <= 0:  # öppnade under stoppet: inget fall
        return {"outcome": "ej utlöst", "net_pct": None, "risk_pct": None, "end": start}
    target = entry + 2 * risk
    fee = 2 * th["fee_pct"]
    for j in range(start, min(n, start + th["max_days"])):
        # Ordningen inom en dag är okänd: nås stoppet samma dag som 2R (eller startdagen) räknas stoppet först.
        # Från dagen efter start gäller öppningen om den ligger bortom nivån.
        hit_stop = lo[j] <= stop
        hit_target = h[j] >= target
        if hit_stop:
            exit_px = min(o[j], stop) if j > start else stop
            return {"outcome": "stopp", "net_pct": round((exit_px / entry - 1) * 100 - fee, 3),
                    "risk_pct": risk / entry * 100, "end": j}
        if hit_target:
            exit_px = max(o[j], target) if j > start else target
            return {"outcome": "2R", "net_pct": round((exit_px / entry - 1) * 100 - fee, 3),
                    "risk_pct": risk / entry * 100, "end": j}
    return {"outcome": "öppet", "net_pct": None, "risk_pct": None, "end": min(n - 1, start + th["max_days"] - 1)}


def summarize(cases: list[dict]) -> dict:
    """Antal per utfall, andelar av de avgjorda, och snittresultat efter courtage (procent och R)."""
    decided = [c for c in cases if c["outcome"] in ("2R", "stopp")]
    n2 = sum(c["outcome"] == "2R" for c in cases)
    ns = sum(c["outcome"] == "stopp" for c in cases)
    out = {"cases": len(cases), "decided": len(decided), "r2_first": n2, "stop_first": ns,
           "open": sum(c["outcome"] == "öppet" for c in cases),
           "not_triggered": sum(c["outcome"] == "ej utlöst" for c in cases),
           "r2_share": round(n2 / len(decided), 4) if decided else None,
           "stop_share": round(ns / len(decided), 4) if decided else None,
           "mean_net_pct": round(sum(c["net_pct"] for c in decided) / len(decided), 3) if decided else None,
           # R: summan av resultaten genom summan av avstånden till stoppet (ett fall med nästan noll avstånd ger
           # annars orimliga R-tal).
           "mean_net_r": round(sum(c["net_pct"] for c in decided) / sum(c["risk_pct"] for c in decided), 3)
           if decided else None}
    return out
