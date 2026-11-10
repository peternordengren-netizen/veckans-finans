"""Tester för RSI(14) och Stoch RSI (scripts/momentum.py), med påhittade kurser."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import momentum  # noqa: E402
import swing  # noqa: E402


def zigzag(n, start=100.0):
    """Påhittad serie med upp- och nedgångar av olika storlek."""
    out, x = [], start
    for j in range(n):
        x += (1.3 if j % 3 else -0.9) + (0.4 if j % 7 == 0 else 0.0) - (1.5 if j % 11 == 0 else 0.0)
        out.append(round(x, 2))
    return out


def test_rsi_som_swingfilen():
    closes = zigzag(120)
    assert momentum.rsi_series(closes) == swing.rsi(closes, 14)
    assert momentum.rsi_series(closes[:14]) == [None] * 14


def test_stoch_rsi_for_hand():
    closes = zigzag(80)
    r = momentum.rsi_series(closes)
    # Räknat för hand ur RSI-serien: rå stokastisk RSI, %K = 3 dagars medel, %D = 3 dagars medel av %K.
    def raw(j):
        w = r[j - 13:j + 1]
        return (r[j] - min(w)) / (max(w) - min(w)) * 100
    k = lambda j: (raw(j) + raw(j - 1) + raw(j - 2)) / 3  # noqa: E731
    n = len(closes) - 1
    kk, dd = momentum.stoch_rsi(closes)
    assert abs(kk - k(n)) < 1e-9 and abs(dd - (k(n) + k(n - 1) + k(n - 2)) / 3) < 1e-9
    # Första RSI dag 15, första råa värdet dag 28, %K dag 30 och %D dag 32.
    assert momentum.stoch_rsi(closes[:29]) == (None, None)
    k30, d30 = momentum.stoch_rsi(closes[:30])
    assert k30 is not None and d30 is None
    assert None not in momentum.stoch_rsi(closes[:32])


def test_varning():
    assert momentum.warn(70.1, 10) and momentum.warn(50, 80.1)
    assert not momentum.warn(70.0, 80.0)  # "över" gränsen
    assert not momentum.warn(None, None)
    # Stadig uppgång: RSI 100 och ingen variation i RSI (Stoch RSI saknas), varning på RSI.
    up = [100 + j for j in range(60)]
    s = momentum.snapshot(up)
    assert s["rsi14"] == 100.0 and s["stoch_k"] is None and s["warn"] is True
    s = momentum.snapshot(zigzag(80))
    assert set(s) == {"rsi14", "stoch_k", "stoch_d", "warn"} and 0 <= s["rsi14"] <= 100
