"""Indicators teknikal dasar di atas pandas DataFrame candle.

Semua fungsi menerima/mereturn DataFrame dengan kolom lowercase: o, h, l, c
(timeseries index = integer posisi). Hanya indikator yang benar-benar
dipakai mesin pola — bukan seluruh library indikator.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI Wilder. Bar awal / flat dianggap netral (50)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss  # loss=0 -> inf -> RSI 100 (pandas arith handles it)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR Wilder: True Range di-smoothing."""
    prev_close = df["c"].shift(1)
    tr = pd.concat(
        [df["h"] - df["l"], (df["h"] - prev_close).abs(), (df["l"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return df baru dengan kolom indikator: ema20, ema50, rsi14, atr14,
    atr_med100, trend.

    atr_med100: median ATR14 dari 100 bar SEBELUM bar saat ini (shift 1) —
    basis "lantai SL" (batch 5, audit 2026-09-22): SL tidak boleh ikut
    menyusut di bawah 0.75x median itu saat ATR14 collapse (squeeze) —
    konsisten membaik 4 tahun dua arah (lihat backtest.SL_FLOOR_MED_MULT).

    trend:  +1 uptight  (ema20 > ema50 dan ema50 naik >= 3 bar terakhir)
            -1 downtrend (cermin dari itu)
             0 sideways  (sinyal dengan trend==0 diabaikan mesin pola)
    """
    out = df.copy()
    out["ema20"] = ema(out["c"], 20)
    out["ema50"] = ema(out["c"], 50)
    out["rsi14"] = rsi(out["c"], 14)
    out["atr14"] = atr(out, 14)
    out["atr_med100"] = out["atr14"].shift(1).rolling(100, min_periods=1).median()

    rising = out["ema50"] > out["ema50"].shift(3)
    falling = out["ema50"] < out["ema50"].shift(3)
    out["trend"] = 0
    out.loc[(out["ema20"] > out["ema50"]) & rising, "trend"] = 1
    out.loc[(out["ema20"] < out["ema50"]) & falling, "trend"] = -1
    return out