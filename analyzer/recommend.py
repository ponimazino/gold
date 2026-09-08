"""Mesin rekomendasi harian (P5) — menggabungkan:

- P3: sinyal pola H1 terbaru yang searah trend H4 + statistik historis pola
- P4: aturan jeda entry di sekitar event bintang-3 + event terdekat

Output bukan "pasti naik/turun": status + bias + confidence berbasis
probabilitas historis pola (win-rate OOS), plus level entry/SL/TP dari ATR.
Semua reasoning ditulis eksplisit di 'rationale' supaya bisa diaudit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from . import calendar as cal
from . import patterns, store
from .backtest import (COST_USD, DEFAULT_SL_ATR, DEFAULT_TP_ATR,
                       SAFE_SL_ATR, SAFE_TP_ATR)

WIB = ZoneInfo("Asia/Jakarta")
RECENT_BARS = 2          # sinyal H1 dihitung valid jika muncul di N bar terakhir
TP2_ATR = 3.0            # target kedua: 3x ATR (di luar TP1 1,5x ATR)


def _recent_signals(df: pd.DataFrame, tf: str, n: int = RECENT_BARS) -> list[dict]:
    sigs = patterns.detect_signals(df, tf)
    if not sigs:
        return []
    cutoff = len(df) - n
    return [s for s in sigs if s["index"] >= cutoff]


def _pattern_stats(tf: str, pattern: str) -> dict | None:
    path = store.DATA_DIR / "patterns.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for r in data.get("results", []):
        if r["tf"] == tf and r["pattern"] == pattern:
            return r
    return None


def _load_calendar_events() -> list[dict]:
    path = store.DATA_DIR / "calendar.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("events", [])
    except json.JSONDecodeError:
        return []


def _trend_name(t: int) -> str:
    return {1: "up", -1: "down", 0: "sideways"}[t]


def build_recommendation(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rec = {
        "id": now.astimezone(WIB).strftime("%Y-%m-%d"),
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at_wib": now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB"),
        "status": "netral",
        "bias": "netral",
        "confidence": None,
        "confidence_note": None,
        "levels": None,
        "levels_safe": None,       # mode aman: TP 1xATR / SL 0.75xATR
        "confidence_safe": None,   # win-rate historis rule mode aman
        "pattern": None,
        "h4_context": None,
        "blackout": None,
        "next_event": None,
        "rationale": [],
        "params": {"tp1_atr": DEFAULT_TP_ATR, "sl_atr": DEFAULT_SL_ATR, "tp2_atr": TP2_ATR,
                   "safe_tp1_atr": SAFE_TP_ATR, "safe_sl_atr": SAFE_SL_ATR,
                   "cost_usd": COST_USD},
    }

    h1_bars = store.load("1h")
    h4_bars = store.load("4h")
    if not h1_bars or not h4_bars:
        rec["rationale"] = ["data H1/H4 belum tersedia — jalankan backfill/sync dulu"]
        return rec

    df1 = patterns.add_indicators(pd.DataFrame(h1_bars).set_index("t")[["o", "h", "l", "c"]])
    df4 = patterns.add_indicators(pd.DataFrame(h4_bars).set_index("t")[["o", "h", "l", "c"]])

    t4 = int(df4["trend"].iloc[-1])
    rsi4 = round(float(df4["rsi14"].iloc[-1]), 1)
    rec["h4_context"] = {"trend": _trend_name(t4), "rsi14": rsi4}
    rec["rationale"].append(f"H4 trend: {_trend_name(t4)}, RSI(14) {rsi4}")

    sigs1 = _recent_signals(df1, "1h")
    if sigs1:
        names = ", ".join(sorted({s["pattern"] for s in sigs1}))
        rec["rationale"].append(f"pola H1 di {RECENT_BARS} bar terakhir: {names}")
    else:
        rec["rationale"].append(f"tidak ada pola H1 di {RECENT_BARS} bar terakhir")

    # --- lapisan fundamental (P4) ---
    events = _load_calendar_events()
    blk = cal.active_blackout(events, now=now)
    nxt = cal.upcoming(events, hours=24, now=now)
    if blk:
        rec["blackout"] = {
            "title": blk["event"]["title"],
            "t_utc": blk["event"]["t_utc"],
            "minutes_to_event": blk["minutes_to_event"],
        }
        rec["rationale"].append(
            f"JEDA ENTRY: {blk['event']['title']} rilis dalam "
            f"{blk['minutes_to_event']:+d} menit — rekomendasi ditunda")
    if nxt:
        rec["next_event"] = {"title": nxt[0]["title"], "t_utc": nxt[0]["t_utc"]}
    else:
        rec["rationale"].append("tidak ada event bintang-3 US dalam 24 jam")

    if blk:
        rec["status"] = "tunggu"
        rec["bias"] = "netral" if t4 == 0 else _trend_name(t4)
        return rec

    # --- lapisan teknikal (P3) ---
    matched = [s for s in sigs1 if s["dir"] == t4] if t4 != 0 else []
    if not matched:
        if t4 == 0:
            rec["rationale"].append("H4 sideways — tanpa arah, tidak ada dasar entry")
        elif sigs1:
            rec["rationale"].append("pola H1 terakhir tidak searah trend H4 — dilewati")
        return rec  # netral

    sig = matched[-1]
    atr = float(df1["atr14"].iloc[-1])
    entry = float(df1["c"].iloc[-1])
    d = sig["dir"]
    levels = {
        "entry": round(entry, 2),
        "sl": round(entry - d * DEFAULT_SL_ATR * atr, 2),
        "tp1": round(entry + d * DEFAULT_TP_ATR * atr, 2),
        "tp2": round(entry + d * TP2_ATR * atr, 2),
        "atr14": round(atr, 2),
    }
    # mode aman: target 1xATR (lebih dekat), SL 0.75xATR (lebih ketat) —
    # entry sama, hanya jarak yang berbeda; win-rate-nya dinilai terpisah
    # oleh research (safe_win_rate di patterns.json), bukan dari rule standar.
    levels_safe = {
        "entry": levels["entry"],
        "sl": round(entry - d * SAFE_SL_ATR * atr, 2),
        "tp1": round(entry + d * SAFE_TP_ATR * atr, 2),
        "atr14": round(atr, 2),
    }
    rec.update({
        "status": "entry",
        "bias": "bullish" if d == 1 else "bearish",
        "pattern": sig["pattern"],
        "direction": d,
        "levels": levels,
        "levels_safe": levels_safe,
    })

    stats = _pattern_stats("1h", sig["pattern"])
    if stats:
        conf = stats.get("oos_win_rate") or stats.get("win_rate")
        rec["confidence"] = conf
        conf_safe = stats.get("safe_oos_win_rate") or stats.get("safe_win_rate")
        if conf_safe:
            rec["confidence_safe"] = conf_safe
            rec["rationale"].append(
                f"mode aman: TP1 {SAFE_TP_ATR}xATR / SL {SAFE_SL_ATR}xATR — "
                f"win-rate OOS {(conf_safe or 0) * 100:.0f}% vs standar "
                f"{(conf or 0) * 100:.0f}% (dinilai backtest terpisah)")
        # net of cost: win-rate setelah spread (gross -> net, dua-duanya)
        net_std = stats.get("cost_oos_win_rate") or stats.get("cost_win_rate")
        net_safe = (stats.get("safe_cost_oos_win_rate")
                    or stats.get("safe_cost_win_rate"))
        if net_std or net_safe:
            rec["rationale"].append(
                f"net biaya (spread ~${COST_USD:.2f}/oz): standar "
                f"{(conf or 0) * 100:.0f}% -> {(net_std or 0) * 100:.0f}%, "
                f"aman {(conf_safe or 0) * 100:.0f}% -> "
                f"{(net_safe or 0) * 100:.0f}%")
        # interval kepercayaan Wilson 95% untuk win-rate full-sample
        lo, hi = stats.get("win_rate_lo"), stats.get("win_rate_hi")
        ci_txt = (f", rentang 95% {round(lo * 100)}-{round(hi * 100)}%"
                  if lo is not None and hi is not None else "")
        rec["confidence_note"] = (
            f"win-rate historis pola '{sig['pattern']}' H1: "
            f"{(stats.get('win_rate') or 0) * 100:.0f}% (n={stats.get('n')}, "
            f"OOS {(stats.get('oos_win_rate') or 0) * 100:.0f}%{ci_txt}) — "
            f"probabilitas, bukan jaminan")
    else:
        rec["confidence_note"] = "statistik pola belum tersedia (jalankan research)"
    rec["rationale"].append(
        f"SL {DEFAULT_SL_ATR}xATR, TP1 {DEFAULT_TP_ATR}xATR, TP2 {TP2_ATR}xATR "
        f"(ATR14 H1 = {levels['atr14']})")
    return rec