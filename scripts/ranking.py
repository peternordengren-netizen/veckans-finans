"""Rangordning av kandidaterna i strategin "Pullback i trend" (fliken Swing): en poäng per uppfyllt kriterium.

Ett informationsverktyg: rangen säger hur många av kriterierna nedan en kandidat uppfyller, inte att den är
bättre eller ett råd. Sidan kallar det "flest kriterier".

Kriterierna (CRITERIA, trösklar i THRESHOLDS), alla på dagens (gårdagens) stängning:
1. "vandsignal": gårdagens candle är en hammer eller bullish engulfing (scripts/pullback.py).
2. "motstand_2r": närmaste motstånd (zoner och medelvärden, scripts/levels.py) minst 2R över referensen, eller
   inget motstånd inom 120 dagar. room_r = (motståndets kant − referens) / R, R = referens − stopp; kanten är
   den nedre, eller den övre när stängningen ligger inne i motståndszonen (levels.resistance_edge).
3. "stodzon": stängningen i en stödzon eller högst 1 ATR över dess övre kant (bara zoner av tidigare vändpunkter,
   inte medelvärdena: rekylen till 20 EMA eller 50 MA ger annars alltid ett stöd nära).
4. "bollinger_1r": minst 1R kvar från stängningen till övre Bollingerbandet (scripts/bollinger.position).
5. "stopp_atr": stoppet högst 1,5 ATR(14) från referensen.
6. "ma50_stigande": 50 dagars MA högre än för 20 handelsdagar sedan.

Rang: flest uppfyllda kriterier först; vid lika poäng mest utrymme till motståndet i R (inget motstånd först).
"""

from __future__ import annotations

CRITERIA = {
    "vandsignal": "Vändsignal",
    "motstand_2r": "Närmaste motstånd minst 2R bort",
    "stodzon": "Kursen i eller högst 1 ATR över en stödzon",
    "bollinger_1r": "Minst 1R kvar till övre Bollingerbandet",
    "stopp_atr": "Stoppet högst 1,5 ATR bort",
    "ma50_stigande": "50 MA stigande senaste 20 dagarna",
}
THRESHOLDS = {"resistance_r": 2.0, "support_atr": 1.0, "bollinger_r": 1.0, "stop_atr": 1.5, "ma50_days": 20, "top": 5}


def measures(signal: bool, ref: float, stop: float, resistance_edge: float | None, support_dist_atr: float | None,
             upper_r: float | None, risk_atr: float | None, c: list[float], i: int,
             th: dict = THRESHOLDS) -> dict:
    """Mätvärdena som kriterierna prövas mot (sparas i swingfilen, så att valideringen kan räkna om)."""
    risk = ref - stop
    n = th["ma50_days"]
    ma50 = lambda j: sum(c[j - 49:j + 1]) / 50  # noqa: E731
    return {
        "signal": signal,
        # None = inget motstånd inom 120 dagar (räknas som uppfyllt, och som mest utrymme).
        "room_r": round((resistance_edge - ref) / risk, 2) if resistance_edge is not None and risk > 0 else None,
        "has_resistance": resistance_edge is not None,
        "support_atr": support_dist_atr,
        "upper_r": upper_r,
        "stop_atr": risk_atr,
        "ma50_change_pct": round((ma50(i) / ma50(i - n) - 1) * 100, 2) if i - n >= 49 else None,
    }


def criteria(m: dict, th: dict = THRESHOLDS) -> dict[str, bool]:
    """Vilka kriterier som är uppfyllda (punkt 1–6 i modulbeskrivningen)."""
    return {
        "vandsignal": bool(m["signal"]),
        "motstand_2r": not m["has_resistance"] or (m["room_r"] is not None and m["room_r"] >= th["resistance_r"]),
        "stodzon": m["support_atr"] is not None and m["support_atr"] <= th["support_atr"],
        "bollinger_1r": m["upper_r"] is not None and m["upper_r"] >= th["bollinger_r"],
        "stopp_atr": m["stop_atr"] is not None and m["stop_atr"] <= th["stop_atr"],
        "ma50_stigande": m["ma50_change_pct"] is not None and m["ma50_change_pct"] > 0,
    }


def sort_key(score: int, m: dict) -> tuple:
    """Flest kriterier först; vid lika poäng mest utrymme till motståndet (inget motstånd först)."""
    room = float("inf") if not m["has_resistance"] else (m["room_r"] if m["room_r"] is not None else float("-inf"))
    return (-score, -room)


def rank(entries: list[dict]) -> None:
    """entries: {"score", "measures", ...}; sätter "rank" 1..n i ordning (sort_key, sedan namnet)."""
    for n, e in enumerate(sorted(entries, key=lambda e: (*sort_key(e["score"], e["measures"]), e.get("name", ""))),
                          start=1):
        e["rank"] = n
