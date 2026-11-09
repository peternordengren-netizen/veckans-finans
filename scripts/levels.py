"""Stöd- och motståndsnivåer för fliken Swing, ur dagsdata (justerade kurser som övriga swingmått).

Ett informationsverktyg: nivåerna beskriver var kursen tidigare vänt och var medelvärdena ligger. De är inga
uppmaningar.

Allt gäller den senaste handelsdagen i serien (dag i, "gårdagen" i morgonens fil):

1. Vändpunkter (pivots): de senaste LOOKBACK handelsdagarna fram till dag i. Dag j är en botten om dess low är
   lägst bland dagarna j−PIVOT … j+PIVOT, och en topp om dess high är högst bland samma dagar. De PIVOT sista
   dagarna kan alltså inte bli vändpunkter (efterföljande dagar saknas).
2. Zoner (zones): alla vändpunkter, bottnar och toppar, sorteras efter pris. En zon börjar vid den lägsta, och
   nästa punkt tas med så länge den ligger högst MERGE_ATR × ATR(14) över zonens nedre kant; annars börjar en ny
   zon. Zonen har nedre och övre kant (lägsta och högsta punkten), mitt (medelvärdet) och antal vändningar
   (antalet punkter, bottnar och toppar för sig).
3. Stöd och motstånd: en zon helt under dag i:s stängning är stöd, helt över är motstånd. En zon som stängningen
   ligger i är stöd om mitten ligger under eller på stängningen, annars motstånd. Så blir en bruten topp stöd och
   en bruten botten motstånd. 20 dagars EMA, 50 och 200 dagars MA är stöd om de ligger under eller på stängningen,
   annars motstånd.
4. Per bolag (summary): närmaste stöd (högsta övre kant under stängningen; en zon som stängningen ligger i har
   avståndet 0) och närmaste motstånd (lägsta nedre kant över), med avstånd i procent och i ATR. Triggernivå =
   dag i:s high. Stopp under stödet = stödets nedre kant − STOP_BUFFER_ATR × ATR: en alternativ stoppnivå, som
   sidan visar i procent. Något eget R räknas inte här: fliken Swing har ett enda R, strategins (referens minus
   strategins stopp, scripts/pullback.py), och motståndet i R räknas med det (scripts/ranking.py, room_r).
   Ligger stängningen inne i motståndszonen ("inside") räknas motståndet till zonens övre kant
   (resistance_edge): kursen är redan i zonen, och det är där den tar slut.
"""

from __future__ import annotations

LOOKBACK = 120
PIVOT = 3
MERGE_ATR = 1.0
STOP_BUFFER_ATR = 0.1
THRESHOLDS = {"lookback": LOOKBACK, "pivot": PIVOT, "merge_atr": MERGE_ATR, "stop_buffer_atr": STOP_BUFFER_ATR}


def pivots(h: list[float], lo: list[float], i: int, lookback: int = LOOKBACK, k: int = PIVOT) -> list[tuple[float, str]]:
    """(pris, "botten" | "topp") för vändpunkterna de senaste lookback dagarna t.o.m. dag i."""
    out = []
    for j in range(max(k, i - lookback + 1), i - k + 1):
        if lo[j] == min(lo[j - k:j + k + 1]):
            out.append((lo[j], "botten"))
        if h[j] == max(h[j - k:j + k + 1]):
            out.append((h[j], "topp"))
    return out


def zones(points: list[tuple[float, str]], atr: float, merge_atr: float = MERGE_ATR) -> list[dict]:
    """Vändpunkterna sammanslagna till zoner, högst merge_atr × ATR breda, lägst först."""
    out: list[dict] = []
    cur: list[tuple[float, str]] = []
    for p in sorted(points):
        if cur and p[0] - cur[0][0] > merge_atr * atr:
            out.append(_zone(cur))
            cur = []
        cur.append(p)
    if cur:
        out.append(_zone(cur))
    return out


def _zone(pts: list[tuple[float, str]]) -> dict:
    prices = [p for p, _ in pts]
    return {"kind": "zon", "low": round(min(prices), 4), "high": round(max(prices), 4),
            "mid": round(sum(prices) / len(prices), 4), "touches": len(pts),
            "bottoms": sum(k == "botten" for _, k in pts), "tops": sum(k == "topp" for _, k in pts)}


def classify(levels: list[dict], close: float) -> tuple[list[dict], list[dict]]:
    """(stöd, motstånd) enligt punkt 3 i modulbeskrivningen."""
    support, resistance = [], []
    for z in levels:
        if z["high"] < close or (z["low"] <= close <= z["high"] and z["mid"] <= close):
            support.append(z)
        else:
            resistance.append(z)
    return support, resistance


def ma_levels(ema20: float | None, ma50: float | None, ma200: float | None) -> list[dict]:
    out = []
    for name, v in (("20 EMA", ema20), ("50 MA", ma50), ("200 MA", ma200)):
        if v is not None:
            out.append({"kind": name, "low": round(v, 4), "high": round(v, 4), "mid": round(v, 4), "touches": None,
                        "bottoms": None, "tops": None})
    return out


def nearest(zone_levels: list[dict], mas: list[dict], close: float) -> tuple[dict | None, dict | None]:
    """(närmaste stöd, närmaste motstånd) av zonerna och medelvärdena. Ett medelvärde inne i en zon är ingen
    egen nivå: zonen täcker redan det prisområdet. Annars kunde 20 EMA under stängningen bli stöd inne i en
    motståndszon som stängningen ligger i (Beijer Alma 2026-11-05: stöd 303,21 över motståndets nedre kant
    302,49). Därmed ligger stödets övre kant aldrig över motståndets nedre."""
    lv = zone_levels + [m for m in mas if not any(z["low"] <= m["mid"] <= z["high"] for z in zone_levels)]
    support, resistance = classify(lv, close)
    sup = max(support, key=lambda z: (z["high"], z["low"])) if support else None
    res = min(resistance, key=lambda z: (z["low"], z["high"])) if resistance else None
    return sup, res


def _dist(level: dict, close: float, atr: float, side: str) -> dict:
    edge = level["high"] if side == "support" else level["low"]
    gap = max(0.0, close - edge) if side == "support" else max(0.0, edge - close)
    # Stängningen inne i zonen (bara zoner; ett medelvärde är en enda nivå).
    inside = level["kind"] == "zon" and level["low"] <= close <= level["high"]
    return {**level, "dist_pct": round(gap / close * 100, 2) if close else None,
            "dist_atr": round(gap / atr, 2) if atr else None, "inside": inside}


def summary(h: list[float], lo: list[float], close: float, i: int, atr: float | None, ema20: float | None,
            ma50: float | None, ma200: float | None, th: dict = THRESHOLDS, zones_only: bool = False) -> dict | None:
    """Närmaste stöd och motstånd, triggernivå och stopp under stödet (punkt 4). None utan ATR."""
    if not atr:
        return None
    zs = zones(pivots(h, lo, i, th["lookback"], th["pivot"]), atr, th["merge_atr"])
    sup, res = nearest(zs, [] if zones_only else ma_levels(ema20, ma50, ma200), close)
    trigger = h[i]
    stop = round(sup["low"] - th["stop_buffer_atr"] * atr, 4) if sup else None
    return {
        "support": _dist(sup, close, atr, "support") if sup else None,
        "resistance": _dist(res, close, atr, "resistance") if res else None,
        "trigger": round(trigger, 4),
        "stop": stop,
    }


def resistance_edge(res: dict) -> float:
    """Kanten som motståndet i R räknas till (ranking.measures): övre kanten när stängningen ligger inne i zonen,
    annars nedre."""
    return res["high"] if res.get("inside") else res["low"]
