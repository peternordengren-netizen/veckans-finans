"""RSI(14) och Stoch RSI (3, 3, 14, 14) för strategivyerna i fliken Swing (Pullback i trend, Rekyl efter
Bollingerutbrott). Visas i kolumnen Ingång med en varning; påverkar inte rang eller krav.

- RSI(14): Wilders utjämning, som scripts/swing.rsi (första snittet = enkelt medel av de 14 första förändringarna).
- Stoch RSI (3, 3, 14, 14): RSI-längd 14, stokastisk längd 14, %K = 3 dagars glidande medel av
  (RSI − lägsta RSI de 14 senaste) / (högsta − lägsta) × 100, %D = 3 dagars glidande medel av %K. En dag där högsta
  och lägsta RSI är lika saknar värde (och därmed de %K/%D som behöver den dagen).
- Varning (warn): RSI(14) över RSI_HIGH (70) eller Stoch RSI %K över STOCH_HIGH (80).

Kurserna: justerade stängningar (som swingfilen). Vid Bollingerkontrollerna 09:20 och 13:20 är dagens kurs (fördröjd
ca 15 minuter) den sista, efter gårdagens och tidigare stängningar (scripts/bollinger.py).
"""

from __future__ import annotations

RSI_LEN = 14
STOCH_LEN = 14
K_LEN = 3
D_LEN = 3
RSI_HIGH = 70.0
STOCH_HIGH = 80.0
THRESHOLDS = {"rsi_len": RSI_LEN, "stoch_len": STOCH_LEN, "k": K_LEN, "d": D_LEN, "rsi_high": RSI_HIGH,
              "stoch_high": STOCH_HIGH}


def rsi_series(closes: list[float], n: int = RSI_LEN) -> list[float | None]:
    """RSI(n) per dag med Wilders utjämning; None tills n förändringar finns."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    ups = [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    downs = [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]
    au, ad = sum(ups[:n]) / n, sum(downs[:n]) / n
    for j in range(n, len(closes)):
        if j > n:
            au = (au * (n - 1) + ups[j - 1]) / n
            ad = (ad * (n - 1) + downs[j - 1]) / n
        out[j] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def _sma(values: list[float | None], n: int) -> list[float | None]:
    return [sum(values[j - n + 1:j + 1]) / n if j >= n - 1 and all(v is not None for v in values[j - n + 1:j + 1])
            else None for j in range(len(values))]


def stoch_rsi(closes: list[float]) -> tuple[float | None, float | None]:
    """(%K, %D) för den sista dagen (punkt 2 i modulbeskrivningen)."""
    r = rsi_series(closes)
    raw: list[float | None] = []
    for j in range(len(r)):
        w = r[j - STOCH_LEN + 1:j + 1] if j >= STOCH_LEN - 1 else []
        if len(w) < STOCH_LEN or any(v is None for v in w) or max(w) == min(w):
            raw.append(None)
        else:
            raw.append((r[j] - min(w)) / (max(w) - min(w)) * 100)
    k = _sma(raw, K_LEN)
    d = _sma(k, D_LEN)
    return (k[-1] if k else None), (d[-1] if d else None)


def snapshot(closes: list[float]) -> dict:
    """{"rsi14", "stoch_k", "stoch_d", "warn"} för den sista dagen i closes (avrundat till en decimal)."""
    r = rsi_series(closes)
    k, d = stoch_rsi(closes)
    rsi = r[-1] if r else None
    rnd = lambda x: round(x, 1) if x is not None else None  # noqa: E731
    return {"rsi14": rnd(rsi), "stoch_k": rnd(k), "stoch_d": rnd(d), "warn": warn(rnd(rsi), rnd(k))}


def warn(rsi: float | None, k: float | None) -> bool:
    """Varning: RSI(14) över 70 eller Stoch RSI %K över 80."""
    return (rsi is not None and rsi > RSI_HIGH) or (k is not None and k > STOCH_HIGH)
