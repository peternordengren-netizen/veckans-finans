"""Strategin "Momentum-utbrott (VCP)" för fliken Swing: relativ styrka, trendmall, bas med krympande rekyler
(volatility contraction pattern), pivot, etiketter, nivåer, rangkriterier och historik ur dagsdata.

Ett informationsverktyg: etiketterna och nivåerna beskriver ett läge och vad som hänt tidigare i liknande lägen. De
är inga uppmaningar.

Priser: justerade som övriga swingmått (scripts/pullback.adjusted). Dag i är den senaste handelsdagen ("gårdagen" i
morgonens fil). Trösklarna står i THRESHOLDS och sparas i swingfilen.

1. Relativ styrka (RS): viktad utveckling 40 % de senaste 63 handelsdagarna (3 mån) och 20 % vardera 126, 189 och 252
   (6, 9, 12 mån), rangordnad som percentil 0–99 mot alla bolag i bevakningslistan samma dag (percentile: andelen bolag
   med lägre värde). Jämförelsen gäller listan, inte hela börsen. Krav: minst rs_min (70).
2. Trendmall: stängning > 50 MA > 150 MA > 200 MA, 200 MA högre än för ma200_days (21) handelsdagar sedan, stängningen
   minst above_low_pct (30 %) över 52-veckorslägsta (lägsta low 252 dagar) och högst below_high_pct (25 %) under
   52-veckorshögsta (högsta high 252 dagar). Alla delar krävs.
3. Bas: den tidigare toppen är det högsta high de base_max_days (60, 12 veckor) dagarna före dag i (dag i ingår inte);
   basen räknas från toppen till dag i och ska vara base_min_days–base_max_days (15–60 dagar, 3–12 veckor) lång.
   Pivot = basens högsta high (toppen). Rekylerna i basen: från toppen och varje senare vändpunkt uppåt (high högst
   bland ±swing dagar) till lägsta low före nästa sådan vändpunkt (den sista till dag i). Rekyldjup = (topp − botten) /
   topp. Krav: minst min_contractions (2) rekyler och varje rekyl grundare än den före ("15 % → 8 % → 4 %").
   Avtagande volym och ATR: snittet de senaste dry_days (10) dagarna mot snittet de senaste 50 (rangkriterier).
4. Etiketter: "vcp-utbrott" (dagens high över pivoten och omsättningen minst breakout_volume (1,5) gånger snittet för
   de 50 dagarna före), "vcp-nara-pivot" (stängningen högst near_pct (5 %) under pivoten, och inget utbrott). En
   stängning över pivoten utan volymkravet ger ingen etikett. Krav 1–3 och 5 gäller båda. Swingfiler före 2026-11-11
   saknar breakout_on i trösklarna och använde stängningen över pivoten (valideringen följer filens egna trösklar).
5. Nivåer: stopp = sista rekylens lägsta low, högst max_stop_pct (8 %) under pivoten (annars ingen etikett).
   Referens = pivoten vid "Nära pivot" (triggernivån); vid "Utbrott" priset där pivoten bröts: pivoten, eller
   öppningen om dagen öppnade över den. R = referens − stopp, samma R överallt; mål 3R = referens + 3R.
6. Rang (CRITERIA): en poäng per uppfyllt kriterium, flest först, vid lika poäng högst RS-percentil.
7. Historik (scripts/swing_history.py): varje dag med etiketten de senaste HISTORY_YEARS åren, ett fall i taget per
   bolag och etikett, så att ett pågående "Nära pivot"-fall inte spärrar ett utbrott i samma bolag (simulate):
   "Nära pivot" startar när dagens high når pivoten inom trigger_days dagar (vid pivoten, eller öppningen om den
   ligger över), "Utbrott" vid referensen på signaldagen, med stopp och mål från nästa dag (resten av signaldagen
   prövas inte). Avgjort när 3R eller stoppet nås (samma dag: stoppet först), högst max_days dagar. Andelen 3R först
   med 95 % konfidensintervall (Wilson). Dessutom utvecklingen mot index (OMX Stockholm PI) efter 5, 20 och 60 dagar
   från signaldagens stängning. Dagens bevakningslista: bolag som avnoterats under perioden saknas (överlevnadsbias).
"""

from __future__ import annotations

from bisect import bisect_left

THRESHOLDS = {
    "rs_weights": {"63": 0.4, "126": 0.2, "189": 0.2, "252": 0.2}, "rs_min": 70, "ma200_days": 21,
    "above_low_pct": 30.0, "below_high_pct": 25.0, "base_min_days": 15, "base_max_days": 60, "swing": 2,
    "min_contractions": 2, "dry_days": 10, "avg_days": 50, "breakout_volume": 1.5, "near_pct": 5.0,
    "max_stop_pct": 8.0, "target_r": 3.0, "trigger_days": 3, "max_days": 60,
    # Utbrott när dagens high passerar pivoten (sedan 2026-11-11; tidigare stängningen, "close").
    "breakout_on": "high",
}
LABELS = {"vcp-nara-pivot": "Nära pivot", "vcp-utbrott": "Utbrott"}
CRITERIA = {
    "volym_avtar": "Volymen senaste 10 dagarna lägre än 50-dagarssnittet",
    "atr_avtar": "ATR senaste 10 dagarna lägre än 50-dagarssnittet",
    "tre_rekyler": "Minst tre krympande rekyler i basen",
    "sista_rekyl": "Sista rekylen högst 10 %",
    "rs_80": "RS-percentil minst 80",
    "vid_pivot": "Stängningen inom 2 % från pivoten eller över den",
}
RANK_THRESHOLDS = {"last_contraction_pct": 10.0, "rs_strong": 80, "at_pivot_pct": 2.0, "top": 5}
HISTORY_YEARS = 3
MIN_BARS = 253  # 12 månaders utveckling och 52-veckorsfönstret


def rs_score(c: list[float], i: int, th: dict = THRESHOLDS) -> float | None:
    """Viktad utveckling (punkt 1) för dag i, eller None utan 252 dagars historik."""
    if i < 252:
        return None
    return sum(w * (c[i] / c[i - int(n)] - 1) for n, w in th["rs_weights"].items() if c[i - int(n)] > 0)


def percentile(score: float, scores_sorted: list[float]) -> int:
    """RS-percentil 0–99: andelen bolag (av alla med värde samma dag) med lägre viktad utveckling."""
    if not scores_sorted:
        return 0
    return min(99, int(100 * bisect_left(scores_sorted, score) / len(scores_sorted)))


def _sma(x: list[float], i: int, n: int) -> float:
    return sum(x[i - n + 1:i + 1]) / n


def true_ranges(h: list[float], lo: list[float], c: list[float]) -> list[float]:
    return [h[0] - lo[0]] + [max(h[j] - lo[j], abs(h[j] - c[j - 1]), abs(lo[j] - c[j - 1])) for j in range(1, len(c))]


def contractions(h: list[float], lo: list[float], k: int, i: int, swing: int) -> list[dict]:
    """Rekylerna i basen från toppen k till dag i (punkt 3): topp, botten och djup i procent, i tidsordning."""
    peaks = [k] + [j for j in range(k + swing, i - swing + 1)
                   if h[j] == max(h[j - swing:j + swing + 1]) and h[j] >= max(h[j - swing:j])]
    out = []
    for n, p in enumerate(peaks):
        end = peaks[n + 1] if n + 1 < len(peaks) else i + 1
        if end - p < 2:
            continue
        t = min(range(p + 1, end), key=lambda j: (lo[j], j))
        depth = (h[p] - lo[t]) / h[p] * 100 if h[p] > 0 else 0.0
        if depth > 0:
            out.append({"peak": round(h[p], 4), "trough": round(lo[t], 4), "depth_pct": round(depth, 2),
                        "peak_ago": i - p, "trough_ago": i - t})
    return out


def state(o: list[float], h: list[float], lo: list[float], c: list[float], v: list[float], i: int,
          rs_pct: int | None, th: dict = THRESHOLDS) -> dict | None:
    """Måtten för dag i som etiketterna avgörs av (sparas i swingfilen; None utan MIN_BARS dagar)."""
    if i < MIN_BARS - 1 or rs_pct is None:
        return None
    r = lambda x: round(x, 4)  # noqa: E731
    ma50, ma150, ma200 = _sma(c, i, 50), _sma(c, i, 150), _sma(c, i, 200)
    ma200_prev = _sma(c, i - th["ma200_days"], 200)
    low52, high52 = min(lo[i - 251:i + 1]), max(h[i - 251:i + 1])
    window = range(i - th["base_max_days"], i)
    k = max(window, key=lambda j: (h[j], j))
    tr = true_ranges(h[i - 60:i + 1], lo[i - 60:i + 1], c[i - 60:i + 1])
    dry, avg = th["dry_days"], th["avg_days"]
    base_v = v[i - avg:i]
    return {
        "open": r(o[i]), "close": r(c[i]), "high": r(h[i]), "low": r(lo[i]), "rs_pct": rs_pct,
        "ma50": r(ma50), "ma150": r(ma150), "ma200": r(ma200), "ma200_prev": r(ma200_prev),
        "low52": r(low52), "high52": r(high52),
        "base_days": i - k, "pivot": r(h[k]),
        "contractions": contractions(h, lo, k, i, th["swing"]),
        "volume": v[i], "volume_avg50": round(sum(base_v) / len(base_v), 1),
        "vol_dry": round(sum(v[i - dry + 1:i + 1]) / dry, 1), "vol_avg": round(sum(v[i - avg + 1:i + 1]) / avg, 1),
        "atr_dry": r(sum(tr[-dry:]) / dry), "atr_avg": r(sum(tr[-avg:]) / avg),
    }


def trend(s: dict, th: dict = THRESHOLDS) -> dict[str, bool]:
    """Trendmallen (punkt 2), del för del."""
    return {
        "ma_ordning": s["close"] > s["ma50"] > s["ma150"] > s["ma200"],
        "ma200_stigande": s["ma200"] > s["ma200_prev"],
        "over_lagsta": s["close"] >= s["low52"] * (1 + th["above_low_pct"] / 100),
        "nara_hogsta": s["close"] >= s["high52"] * (1 - th["below_high_pct"] / 100),
    }


def shrinking(s: dict, th: dict = THRESHOLDS) -> bool:
    """Minst min_contractions rekyler och varje rekyl grundare än den före."""
    d = [x["depth_pct"] for x in s["contractions"]]
    return len(d) >= th["min_contractions"] and all(b < a for a, b in zip(d, d[1:]))


def stop_of(s: dict) -> float | None:
    """Sista rekylens lägsta low (stoppet), eller None utan rekyler."""
    return s["contractions"][-1]["trough"] if s["contractions"] else None


def labels(s: dict | None, th: dict = THRESHOLDS) -> list[str]:
    """[], ["vcp-nara-pivot"] eller ["vcp-utbrott"] (används även av historiken och valideringen)."""
    if not s or s["rs_pct"] < th["rs_min"] or not all(trend(s, th).values()):
        return []
    if not th["base_min_days"] <= s["base_days"] <= th["base_max_days"] or not shrinking(s, th):
        return []
    stop = stop_of(s)
    if stop is None or (s["pivot"] - stop) / s["pivot"] * 100 > th["max_stop_pct"]:
        return []
    volume_ok = s["volume_avg50"] > 0 and s["volume"] >= th["breakout_volume"] * s["volume_avg50"]
    broke = s["high"] > s["pivot"] if th.get("breakout_on") == "high" else s["close"] > s["pivot"]
    if broke and volume_ok:
        return ["vcp-utbrott"]
    if s["close"] > s["pivot"]:
        return []  # stängning över pivoten utan volymkravet
    if s["close"] >= s["pivot"] * (1 - th["near_pct"] / 100):
        return ["vcp-nara-pivot"]
    return []


def breakout_ref(s: dict, th: dict = THRESHOLDS) -> float:
    """Referensen vid "Utbrott": priset där pivoten bröts (pivoten, eller öppningen om dagen öppnade över den); i
    äldre filer (stängningen över pivoten) stängningen."""
    if th.get("breakout_on") == "high":
        return max(s["pivot"], s["open"])
    return s["close"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % konfidensintervall (Wilson) för andelen k av n, eller None utan fall."""
    if n == 0:
        return None
    p = k / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)


def levels(s: dict, label: str, atr: float | None, th: dict = THRESHOLDS) -> dict:
    """Nivåerna för ett bolag med etikett (punkt 5)."""
    stop = stop_of(s)
    ref = breakout_ref(s, th) if label == "vcp-utbrott" else s["pivot"]
    risk = ref - stop
    r = lambda x: round(x, 4)  # noqa: E731
    return {
        "ref": r(ref), "trigger": r(s["pivot"]) if label == "vcp-nara-pivot" else None, "pivot": r(s["pivot"]),
        "stop": r(stop), "stop_under_pivot_pct": round((s["pivot"] - stop) / s["pivot"] * 100, 2),
        "risk_pct": round(risk / ref * 100, 2) if ref else None, "risk_atr": round(risk / atr, 2) if atr else None,
        "target": r(ref + th["target_r"] * risk),
        "pivot_dist_pct": round((s["close"] / s["pivot"] - 1) * 100, 2),
        "volume_ratio": round(s["volume"] / s["volume_avg50"], 2) if s["volume_avg50"] else None,
        "depths": [x["depth_pct"] for x in s["contractions"]],
        "base_weeks": round(s["base_days"] / 5, 1),
    }


def criteria(s: dict, th: dict = RANK_THRESHOLDS) -> dict[str, bool]:
    """Rangkriterierna (CRITERIA), ur måtten."""
    d = [x["depth_pct"] for x in s["contractions"]]
    return {
        "volym_avtar": s["vol_dry"] < s["vol_avg"],
        "atr_avtar": s["atr_dry"] < s["atr_avg"],
        "tre_rekyler": len(d) >= 3,
        "sista_rekyl": bool(d) and d[-1] <= th["last_contraction_pct"],
        "rs_80": s["rs_pct"] >= th["rs_strong"],
        "vid_pivot": s["close"] >= s["pivot"] * (1 - th["at_pivot_pct"] / 100),
    }


def rank(entries: list[dict]) -> None:
    """entries: {"score", "rs_pct", "name"}; sätter "rank" 1..n: flest kriterier, sedan högst RS, sedan namnet."""
    for n, e in enumerate(sorted(entries, key=lambda e: (-e["score"], -e["rs_pct"], e.get("name", ""))), start=1):
        e["rank"] = n


def simulate(o: list[float], h: list[float], lo: list[float], i: int, label: str, lv: dict,
             th: dict = THRESHOLDS) -> dict:
    """Ett historiskt fall från dag i (punkt 7): {"outcome": "3R" | "stopp" | "öppet" | "ej utlöst", "net_pct",
    "risk_pct", "end"}. Samma regler för en dag som pullback.simulate, med målet target_r."""
    n = len(o)
    stop = lv["stop"]
    same_day = False  # ingången skedde dagen före start (utbrott under signaldagen)
    if label == "vcp-utbrott" and th.get("breakout_on") == "high":
        if i + 1 >= n:
            return {"outcome": "öppet", "net_pct": None, "risk_pct": None, "end": n - 1}
        start, entry, same_day = i + 1, lv["ref"], True
    elif label == "vcp-nara-pivot":
        start = entry = None
        for j in range(i + 1, min(n, i + 1 + th["trigger_days"])):
            if h[j] >= lv["pivot"]:
                start, entry = j, max(lv["pivot"], o[j])
                break
        if start is None:
            return {"outcome": "ej utlöst", "net_pct": None, "risk_pct": None, "end": min(n - 1, i + th["trigger_days"])}
    else:
        if i + 1 >= n:
            return {"outcome": "öppet", "net_pct": None, "risk_pct": None, "end": n - 1}
        start, entry = i + 1, o[i + 1]
    risk = entry - stop
    if risk <= 0:
        return {"outcome": "ej utlöst", "net_pct": None, "risk_pct": None, "end": start}
    target = entry + th["target_r"] * risk
    for j in range(start, min(n, start + th["max_days"])):
        if lo[j] <= stop:
            px = min(o[j], stop) if j > start or same_day else stop
            return {"outcome": "stopp", "net_pct": round((px / entry - 1) * 100, 3), "risk_pct": risk / entry * 100, "end": j}
        if h[j] >= target:
            px = max(o[j], target) if j > start or same_day else target
            return {"outcome": "3R", "net_pct": round((px / entry - 1) * 100, 3), "risk_pct": risk / entry * 100, "end": j}
    return {"outcome": "öppet", "net_pct": None, "risk_pct": None, "end": min(n - 1, start + th["max_days"] - 1)}


def summarize(cases: list[dict]) -> dict:
    """Antal per utfall, andelar av de avgjorda och snittresultat (procent och R), utan courtage."""
    decided = [c for c in cases if c["outcome"] in ("3R", "stopp")]
    n3 = sum(c["outcome"] == "3R" for c in cases)
    ns = sum(c["outcome"] == "stopp" for c in cases)
    return {"cases": len(cases), "decided": len(decided), "r3_first": n3, "stop_first": ns,
            "open": sum(c["outcome"] == "öppet" for c in cases),
            "not_triggered": sum(c["outcome"] == "ej utlöst" for c in cases),
            "r3_share": round(n3 / len(decided), 4) if decided else None,
            # 95 % konfidensintervall för andelen 3R först (Wilson), [låg, hög], null utan avgjorda fall.
            "r3_ci": list(wilson(n3, len(decided))) if decided else None,
            "mean_net_pct": round(sum(c["net_pct"] for c in decided) / len(decided), 3) if decided else None,
            "mean_net_r": round(sum(c["net_pct"] for c in decided) / sum(c["risk_pct"] for c in decided), 3)
            if decided else None}
