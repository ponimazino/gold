"""Deteksi pola candlestick dengan konteks trend.

Filosofi P3: pola polos (pin bar di mana-mana) terlalu berisik untuk jadi
basis statistik. Setiap sinyal HANYA diambil bila searah dengan trend
(EMA regime), sehingga yang diukur adalah "pola kelanjutan dalam trend",
bukan noise. Hasilnya nanti diverifikasi out-of-sample di research.py.

Sinyal keluaran: {"t", "tf", "pattern", "dir", "index"} — index = posisi
bar sinyal; entry dilakukan di open bar berikutnya (dievaluasi research.py).
"""

from __future__ import annotations

import pandas as pd

from .indicators import add_indicators

# body/wick helpers per bar
def _body(r) -> float:
    return abs(r["c"] - r["o"])


def _upper(r) -> float:
    return r["h"] - max(r["c"], r["o"])


def _lower(r) -> float:
    return min(r["c"], r["o"]) - r["l"]


def _is_bull(r) -> bool:
    return r["c"] > r["o"]


def bullish_engulfing(df: pd.DataFrame, i: int) -> bool:
    """Green menelan body red sebelumnya. Non-strict (<=/>=) karena XAUUSD
    nyaris gapless: open bar hampir selalu == close bar sebelumnya."""
    prev, cur = df.iloc[i - 1], df.iloc[i]
    return (not _is_bull(prev)) and _is_bull(cur) \
        and cur["c"] >= prev["o"] and cur["o"] <= prev["c"]


def bearish_engulfing(df: pd.DataFrame, i: int) -> bool:
    prev, cur = df.iloc[i - 1], df.iloc[i]
    return _is_bull(prev) and (not _is_bull(cur)) \
        and cur["c"] <= prev["o"] and cur["o"] >= prev["c"]


def hammer(df: pd.DataFrame, i: int) -> bool:
    """Pin bar bullish: ekor bawah panjang, body kecil, ekor atas kecil.
    Konteks: RSI < 45 (bar terjadi saat tekanan jual terkuras)."""
    r = df.iloc[i]
    return _lower(r) >= 2 * max(_body(r), df["atr14"].iloc[i] * 0.1) \
        and _upper(r) <= _body(r) \
        and df["rsi14"].iloc[i] < 45


def shooting_star(df: pd.DataFrame, i: int) -> bool:
    """Pin bar bearish (cermin hammer). Konteks: RSI > 55."""
    r = df.iloc[i]
    return _upper(r) >= 2 * max(_body(r), df["atr14"].iloc[i] * 0.1) \
        and _lower(r) <= _body(r) \
        and df["rsi14"].iloc[i] > 55


def inside_bar(df: pd.DataFrame, i: int) -> bool:
    """Range bar saat ini sepenuhnya di dalam bar sebelumnya."""
    prev, cur = df.iloc[i - 1], df.iloc[i]
    return cur["h"] < prev["h"] and cur["l"] > prev["l"]


PATTERNS = {
    "bullish_engulfing": (bullish_engulfing, 1),
    "bearish_engulfing": (bearish_engulfing, -1),
    "hammer": (hammer, 1),
    "shooting_star": (shooting_star, -1),
}


def detect_signals(df: pd.DataFrame, tf: str) -> list[dict]:
    """Semua sinyal yang searah trend pada bar tsb.

    inside_bar tidak punya arah sendiri -> dihitung sebagai sinyal breakout
    searah trend (dir = trend saat itu).
    """
    df = add_indicators(df)
    signals: list[dict] = []
    for i in range(1, len(df)):
        trend = int(df["trend"].iloc[i])
        if trend == 0:
            continue  # hanya pola dalam trend
        for name, (fn, dir_) in PATTERNS.items():
            if fn(df, i) and dir_ == trend:
                signals.append({"t": df.index[i], "tf": tf, "pattern": name,
                                "dir": dir_, "index": i})
        if inside_bar(df, i):
            signals.append({"t": df.index[i], "tf": tf, "pattern": "inside_bar",
                            "dir": trend, "index": i})
    return signals