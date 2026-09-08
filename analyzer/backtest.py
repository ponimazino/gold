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

# Rule "mode aman": target lebih dekat + SL lebih ketat — target kecil tapi
# peluang historisnya lebih tinggi (dienvaluasi dengan rule yang sama,
# hasilnya jujur per pola, bukan asal dikali-kali).
SAFE_TP_ATR = 1.0
SAFE_SL_ATR = 0.75

# TP kedua rekomendasi (runner): 3x ATR.
TP2_ATR = 3.0

# Biaya transaksi (spread) flat per round-trip, USD/oz. Spread akun standar
# XAUUSD ~30-50 cent/oz (ECN 10-25); 0.35 = titik tengah yang konservatif.
# Model: entry long terjadi di ASK (di atas mid) — relatif terhadap harga mid
# di data, KEDUA barrier bergeser melawan trader: TP harus tercapai lebih
# jauh (+cost) dan SL tersentuh lebih cepat (−cost). R nominal per trade
# TIDAK berubah (biaya masuk ke probabilitas hit, bukan ke ukuran R).
# (Spread melebar saat rollover/berita tidak dimodelkan; grid rekomendasi
# memang berhenti berdekatan rilis 3-star.)
COST_USD = 0.35


def evaluate(
    df: pd.DataFrame,
    signals: list[dict],
    horizon: int,
    tp_atr: float = DEFAULT_TP_ATR,
    sl_atr: float = DEFAULT_SL_ATR,
    cost_usd: float = 0.0,
) -> list[dict]:
    """df harus sudah punya kolom atr14 (add_indicators).

    cost_usd > 0 -> evaluasi "net of cost": dalam harga mid, barrier TP
    bergeser lebih JAUH (+cost) dan barrier SL lebih DEKAT (−cost) —
    win-rate turun jujur, R nominal per trade tetap (biaya masuk ke
    probabilitas, bukan ukuran); R timeout dihitung setelah biaya."""
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
        tp_dist = tp_atr * atr
        sl_dist = sl_atr * atr
        r_win = tp_atr / sl_atr  # R nominal (biaya tidak mengubah ukuran R)
        if cost_usd:
            tp_dist += cost_usd
            sl_dist = max(sl_dist - cost_usd, 0.05 * atr)
        tp = entry + d * tp_dist
        sl = entry - d * sl_dist

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
                outcome, r = "win", r_win
                break
        if outcome is None:
            last = df.iloc[end - 1]
            pnl = (float(last["c"]) - entry) * d - cost_usd
            outcome = "timeout"
            r = pnl / sl_dist
        results.append({**s, "entry": entry, "outcome": outcome, "r": round(r, 3), "bars": bars})
    return results


def evaluate_tp2(
    df: pd.DataFrame,
    signals: list[dict],
    horizon: int,
    tp1_atr: float = DEFAULT_TP_ATR,
    tp2_atr: float = TP2_ATR,
    sl_atr: float = DEFAULT_SL_ATR,
) -> list[dict]:
    """Skenario manajemen posisi "runner" untuk TP2 rekomendasi:

    entry & SL standar (SL 1xATR); begitu TP1 (1.5xATR) tercapai, SL dipindah
    ke breakeven (level entry) dan posisi sisa mengejar TP2 (3xATR).

    Outcome per sinyal:
      "stopped" — SL kena sebelum TP1
      "no_tp1"  — horizon habis tanpa TP1
      "tp2"     — TP2 tercapai setelah TP1 (SL sudah di breakeven)
      "be"      — setelah TP1, harga kembali ke entry (exit breakeven)
      "timeout" — setelah TP1, horizon habis sebelum TP2/BE
    """
    results = []
    for s in signals:
        i = s["index"]
        if i + 1 >= len(df):
            continue
        entry_idx = i + 1
        entry = float(df["o"].iloc[entry_idx])
        atr = float(df["atr14"].iloc[i])
        if atr <= 0:
            continue
        d = s["dir"]
        tp1 = entry + d * tp1_atr * atr
        tp2 = entry + d * tp2_atr * atr
        sl = entry - d * sl_atr * atr

        outcome, bars = None, 0
        seeking_tp1 = True
        end = min(entry_idx + horizon, len(df))
        for j in range(entry_idx, end):
            bar = df.iloc[j]
            bars = j - entry_idx + 1
            hit_sl = bar["l"] <= sl if d == 1 else bar["h"] >= sl
            hit_tp1 = bar["h"] >= tp1 if d == 1 else bar["l"] <= tp1
            hit_tp2 = bar["h"] >= tp2 if d == 1 else bar["l"] <= tp2
            hit_be = bar["l"] <= entry if d == 1 else bar["h"] >= entry
            if seeking_tp1:
                if hit_sl:  # konservatif: SL dicek dulu
                    outcome = "stopped"
                    break
                if hit_tp1:
                    seeking_tp1 = False
                    sl = entry  # SL naik ke breakeven
                    if hit_tp2:  # bar besar: TP1 & TP2 di bar yang sama
                        outcome = "tp2"
                        break
            else:
                if hit_be:  # konservatif: BE dicek dulu
                    outcome = "be"
                    break
                if hit_tp2:
                    outcome = "tp2"
                    break
        if outcome is None:
            outcome = "no_tp1" if seeking_tp1 else "timeout"
        results.append({**s, "entry": entry, "outcome": outcome, "bars": bars})
    return results


def risk_stats(results: list[dict]) -> dict[tuple, dict]:
    """Statistik risiko per (tf, pattern) dari urutan sinyal kronologis:
    max drawdown ekuitas kumulatif (dalam R, peak-to-trough) dan panjang
    kekalahan beruntun maksimum. results harus urut waktu."""
    grouped: dict[tuple, list[float]] = {}
    for res in results:
        grouped.setdefault((res["tf"], res["pattern"]), []).append(res["r"])
    out: dict[tuple, dict] = {}
    for key, rs in grouped.items():
        cum = peak = 0.0
        max_dd = 0.0
        streak = max_streak = 0
        for r in rs:
            cum += r
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)
            if r < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        out[key] = {"max_dd_r": round(max_dd, 2), "max_loss_streak": max_streak}
    return out


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Interval kepercayaan Wilson (default 95%) untuk proporsi k/n.
    Lebih tepat untuk win-rate daripada pendekatan normal biasa, termasuk
    saat n kecil atau p ekstrem. Return (lo, hi) atau (None, None) jika n=0."""
    if n <= 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(center - half, 0.0), 3), round(min(center + half, 1.0), 3)


def summarize(results: list[dict]) -> dict:
    """Agregat per (tf, pattern). win_rate dihitung atas sinyal yang resolved
    (TP atau SL tercapai) — timeout dilaporkan terpisah, dan win_rate
    dibubuhi interval kepercayaan Wilson 95% (win_rate_lo/hi)."""
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
        lo, hi = wilson_ci(g["wins"], resolved)
        out.append({
            "tf": tf,
            "pattern": pattern,
            "n": n,
            "resolved": resolved,
            "win_rate": round(g["wins"] / resolved, 3) if resolved else None,
            "win_rate_lo": lo,
            "win_rate_hi": hi,
            "timeout_pct": round(g["timeouts"] / n, 3) if n else None,
            "avg_r": round(g["r_sum"] / n, 3) if n else None,
        })
    return {"results": out}


def oos_split(results: list[dict], train_frac: float = 0.7) -> tuple[dict, dict]:
    """Bagi sinyal (urut waktu): 70% in-sample untuk statistik,
    30% terakhir out-of-sample untuk cek overfitting."""
    cut = int(len(results) * train_frac)
    return summarize(results[:cut])["results"], summarize(results[cut:])["results"]