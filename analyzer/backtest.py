"""Evaluasi hasil sinyal pola (forward-outcome), bukan curve-fitting.

Untuk setiap sinyal: entry di open bar berikutnya, lalu jalan maju maksimal
`horizon` bar dengan SL = 1x ATR dan TP = 1.5x ATR (arah menyesuaikan).
Aturan konservatif: kalau TP dan SL tersentuh di bar yang sama, dihitung LOSS
(di dunia nyata kita tidak tahu yang kena lebih dulu — asumsi terburuk).
Sinyal yang tidak selesai dalam horizon = timeout, dicatat dengan R parsial.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_TP_ATR = 1.5
DEFAULT_SL_ATR = 1.0
DEFAULT_HORIZON = {"1h": 24, "4h": 12}  # bar


def evaluate(
    df: pd.DataFrame,
    signals: list[dict],
    horizon: int,
    tp_atr: float = DEFAULT_TP_ATR,
    sl_atr: float = DEFAULT_SL_ATR,
) -> list[dict]:
    """df harus sudah punya kolom atr14 (add_indicators)."""
    results = []
    for s in signals:
        i = s["index"]
        if i + 1 >= len(df):
            continue  # bar entry belum ada (sinyal di bar terakhir)
        entry_idx = i + 1
        entry = float(df["o"].iloc[entry_idx])
        atr = float(df["atr14"].iloc[i])
        if atr <= 0:
            continue
        d = s["dir"]
        tp = entry + d * tp_atr * atr
        sl = entry - d * sl_atr * atr

        outcome, r, bars = None, 0.0, 0
        end = min(entry_idx + horizon, len(df))
        for j in range(entry_idx, end):
            bar = df.iloc[j]
            hit_sl = bar["l"] <= sl if d == 1 else bar["h"] >= sl
            hit_tp = bar["h"] >= tp if d == 1 else bar["l"] <= tp
            bars = j - entry_idx + 1
            if hit_sl:  # cek SL dulu: TP+SL di bar sama -> loss (konservatif)
                outcome, r = "loss", -1.0
                break
            if hit_tp:
                outcome, r = "win", tp_atr / sl_atr
                break
        if outcome is None:
            last = df.iloc[end - 1]
            pnl = (float(last["c"]) - entry) * d
            outcome = "timeout"
            r = pnl / (sl_atr * atr)
        results.append({**s, "entry": entry, "outcome": outcome, "r": round(r, 3), "bars": bars})
    return results


def summarize(results: list[dict]) -> dict:
    """Agregat per (tf, pattern). win_rate dihitung atas sinyal yang resolved
    (TP atau SL tercapai) — timeout dilaporkan terpisah."""
    grouped: dict[tuple, dict] = {}
    for res in results:
        key = (res["tf"], res["pattern"])
        g = grouped.setdefault(key, {"wins": 0, "losses": 0, "timeouts": 0, "r_sum": 0.0})
        if res["outcome"] == "win":
            g["wins"] += 1
        elif res["outcome"] == "loss":
            g["losses"] += 1
        else:
            g["timeouts"] += 1
        g["r_sum"] += res["r"]

    out = []
    for (tf, pattern), g in sorted(grouped.items()):
        n = g["wins"] + g["losses"] + g["timeouts"]
        resolved = g["wins"] + g["losses"]
        out.append({
            "tf": tf,
            "pattern": pattern,
            "n": n,
            "resolved": resolved,
            "win_rate": round(g["wins"] / resolved, 3) if resolved else None,
            "timeout_pct": round(g["timeouts"] / n, 3) if n else None,
            "avg_r": round(g["r_sum"] / n, 3) if n else None,
        })
    return {"results": out}


def oos_split(results: list[dict], train_frac: float = 0.7) -> tuple[dict, dict]:
    """Bagi sinyal (urut waktu): 70% in-sample untuk statistik,
    30% terakhir out-of-sample untuk cek overfitting."""
    cut = int(len(results) * train_frac)
    return summarize(results[:cut])["results"], summarize(results[cut:])["results"]