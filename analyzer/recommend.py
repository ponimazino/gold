"""Mesin rekomendasi harian (P5) — menggabungkan:

- P3: sinyal pola H1 terbaru yang searah trend H4 + statistik historis pola
- P4: aturan jeda entry di sekitar event bintang-3 + event terdekat

Output bukan "pasti naik/turun": status + bias + confidence berbasis
probabilitas historis pola (win-rate OOS), plus level entry/SL/TP dari ATR.
Semua reasoning ditulis eksplisit di 'rationale' supaya bisa diaudit.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from . import calendar as cal
from . import patterns, store
from .backtest import (COST_USD, DEFAULT_SL_ATR, DEFAULT_TP_ATR,
                       SAFE_SL_ATR, SAFE_TP_ATR)

WIB = ZoneInfo("Asia/Jakarta")
RECENT_BARS = 2          # sinyal H1 dihitung valid jika muncul di N bar terakhir
STALE_HOURS = 3.0        # bar closed terakhir lebih tua dari ini = data basi
# TP2 (runner 3xATR) DIHAPUS (keputusan user 2026-09-20): sistem TP1-only —
# level yang tampil = level yang dinilai feedback loop.

# Gate kualitas (2026-09-10, tahap 1 "moderat"): pola yang searah trend pun
# TIDAK otomatis layak entry — backtest-nya harus membuktikan EV positif
# setelah biaya. Fail-closed: tanpa statistik = tidak ada bukti = tidak entry.
GATE_MIN_N = 30      # pola dengan n dedup < ini belum punya bukti bermakna
GATE_MIN_EV_R = 0.0  # EV per sinyal (R, termasuk timeout) harus di atas ini


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


def _gate_ev(stats: dict, direction: int | None = None) -> float | None:
    """EV per sinyal dalam R (termasuk timeout). Prioritas estimat paling
    jujur: PER ARAH sinyal (OOS net -> full net) -> keseluruhan -> gross.
    Statistik per arah (_long/_short) dihitung research._direction_map —
    long dan short punya bukti berbeda (audit 2026-09-12: inside_bar long
    +0.12R vs short -0.13R di bull market 2023-26)."""
    if direction is not None:
        nm = "long" if direction == 1 else "short"
        for k in (f"cost_oos_avg_r_{nm}", f"cost_avg_r_{nm}"):
            v = stats.get(k)
            if v is not None:
                return float(v)
    for k in ("cost_avg_r", "cost_oos_avg_r", "avg_r"):
        v = stats.get(k)
        if v is not None:
            return float(v)
    return None


def _gate_n(stats: dict, direction: int | None = None) -> int:
    """n jujur (dedup) — per arah kalau tersedia, else keseluruhan."""
    if direction is not None:
        nm = "long" if direction == 1 else "short"
        n = stats.get(f"n_{nm}")
        if n is not None:
            return int(n)
    return int(stats.get("n") or 0)


def _experiment_eligible(stats: dict | None, direction: int | None) -> bool:
    """Eksperimen short DIHENTIKAN (keputusan user 2026-09-20 setelah audit
    live 19 entry: 13 eksperimen → hit-rate ~23%, dan sisa kolam eksperimen
    pasca-fix pun tetap negatif). Fungsi ini dipertahankan sebagai dokumentasi
    keputusan — SELALU False: arah EV-negatif kini fail-closed netral,
    hanya entry lolos gate yang dieksekusi. Histori entry eksperimen lama
    tetap terpisah di tracking.stats['experiment']."""
    return False


def _closed_only(bars: list[dict], step_h: int, now: datetime) -> list[dict]:
    """Buang bar terakhir yang belum selesai (partial). Sync menyimpan candle
    yang sedang berjalan — bar itu tidak boleh dipakai analisa: pola bisa
    'batal' di menit-menit akhir jamnya dan level entry-nya cuma snapshot
    harga live, sedangkan statistik gate (n, EV) dihitung murni dari candle
    yang sudah close. Konsisten backtest <-> live (audit 2026-09-12)."""
    if bars:
        last = store.parse(bars[-1]["t"])
        if (now - last).total_seconds() < step_h * 3600:
            return bars[:-1]
    return bars


def _counter_momentum(df: pd.DataFrame, i: int, d: int) -> bool:
    """True kalau momentum H1 3 bar terakhir LAWAN arah sinyal di bar i
    (dalam satuan ATR14). Guard khusus SHORT (audit 2026-09-20, data 3 tahun
    net of cost, dedup, searah H4): short saat momentum naik (bounce) adalah
    subset TERBURUK — inside_bar short EV -0.167R vs -0.045R momentum
    searah; bearish_engulfing short -0.500R (win-rate 20%) vs -0.057R.
    9 dari 13 entry eksperimen live 14-17 Sep (termasuk 3 loss beruntun
    after-Fed 17 Sep) muncul dalam kondisi ini. LONG sengaja TIDAK diguard:
    cermin data-nya terbalik — inside_bar long saat momentum turun (beli
    dip) justru subset TERBAIK (+0.188R)."""
    if d != -1 or i < 3:
        return False
    atr = float(df["atr14"].iloc[i])
    if not atr or atr != atr:  # 0 / NaN (data terlalu awal)
        return False
    mom3 = (float(df["c"].iloc[i]) - float(df["c"].iloc[i - 3])) / atr
    return mom3 > 0


def _gate_check(stats: dict | None,
                direction: int | None = None) -> tuple[bool, str, dict]:
    """Kembalikan (lolos, alasan, meta) untuk satu pola + arahnya. Meta dipakai
    untuk field rec["gate"] supaya keputusan gate bisa diaudit dari UI/log.
    Sadar-arah (2026-09-12): long dan short dinilai EV arahnya sendiri
    (*_long/*_short dari research._direction_map) — kalau per-arah tidak
    tersedia, fallback ke statistik keseluruhan."""
    if not stats:
        return False, "statistik pola belum tersedia — tanpa bukti, tanpa entry", {}
    nm = f" {'long' if direction == 1 else 'short'}" if direction is not None else ""
    n = _gate_n(stats, direction)
    ev = _gate_ev(stats, direction)
    meta = {"n": n, "ev_net_r": ev, "direction": direction}
    if n < GATE_MIN_N:
        return False, f"n{nm}={n} (< {GATE_MIN_N}) — bukti statistik belum cukup", meta
    if ev is None:
        return False, "EV net belum bisa dihitung — tanpa bukti, tanpa entry", meta
    if ev <= GATE_MIN_EV_R:
        return False, f"EV net{nm} {ev:+.3f}R — backtest negatif setelah biaya", meta
    return True, f"lolos gate kualitas: EV net{nm} {ev:+.3f}R, n{nm}={n}", meta


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
        "params": {"tp1_atr": DEFAULT_TP_ATR, "sl_atr": DEFAULT_SL_ATR,
                   "safe_tp1_atr": SAFE_TP_ATR, "safe_sl_atr": SAFE_SL_ATR,
                   "cost_usd": COST_USD},
    }

    h1_bars = _closed_only(store.load("1h"), 1, now)
    h4_bars = _closed_only(store.load("4h"), 4, now)
    if not h1_bars or not h4_bars:
        rec["rationale"] = ["data H1/H4 belum tersedia — jalankan backfill/sync dulu"]
        return rec

    # --- guard data basi (audit 2026-09-20): sync gagal / pasar memang tutup
    # -> bar closed terakhir jauh di belakang jam dinding. Analisa di harga
    # basi pernah mencatat entry phantom (8 Sep: entry di harga 3 hari lalu,
    # "menang" instan saat data segar datang). Lebih baik netral jujur.
    age_h = (now - store.parse(h1_bars[-1]["t"])).total_seconds() / 3600 - 1.0
    if age_h > STALE_HOURS:
        rec["stale"] = True
        rec["rationale"].append(
            f"data belum segar — candle H1 closed terakhir {age_h:.0f} jam lalu "
            f"(sync tertunda atau pasar tutup) — analisa ditunda, tidak ada entry")
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
    # --- guard momentum-lawan, khusus SHORT (bukti 3 tahun, lihat
    # _counter_momentum): short saat bounce H1 = subset EV terburuk.
    momentum_blocked: list[dict] = []
    if t4 == -1:
        kept = []
        for s in matched:
            if _counter_momentum(df1, s["index"], s["dir"]):
                momentum_blocked.append(s)
            else:
                kept.append(s)
        matched = kept
        if momentum_blocked:
            names = ", ".join(sorted({s["pattern"] for s in momentum_blocked}))
            rec["rationale"].append(
                f"pola {names} searah trend TAPI disaring guard momentum-lawan: "
                f"harga H1 3 bar terakhir justru naik (bounce) — short saat "
                f"bounce terbukti EV terburuk di data 3 tahun")
    if not matched:
        if t4 == 0:
            rec["rationale"].append("H4 sideways — tanpa arah, tidak ada dasar entry")
        elif momentum_blocked:
            last = momentum_blocked[-1]
            rec["gate"] = {"pattern": last["pattern"], "passed": False,
                           "momentum_block": True,
                           "reason": "guard momentum-lawan: momentum H1 3-bar "
                                     "naik (bounce) — short saat bounce terbukti "
                                     "EV terburuk di data 3 tahun"}
        elif sigs1:
            rec["rationale"].append("pola H1 terakhir tidak searah trend H4 — dilewati")
        return rec  # netral

    # --- gate kualitas sadar-arah: EV dinilai per arah sinyal (long/short).
    # EKSPERIMEN DIHENTIKAN (keputusan user 2026-09-20): arah dengan bukti
    # bermakna tapi EV negatif kini fail-closed netral — TIDAK lagi jadi
    # entry eksperimen. Hanya arah dengan EV net positif yang dieksekusi.
    qualified: list[tuple[dict, dict, str, dict]] = []
    for s in matched:
        st = _pattern_stats("1h", s["pattern"])
        ok, why, meta = _gate_check(st, s["dir"])
        if ok:
            qualified.append((s, st, why, meta))
        else:
            rec["rationale"].append(
                f"pola '{s['pattern']}' searah trend TAPI disaring gate "
                f"kualitas: {why} — tidak jadi entry (eksperimen dihentikan)")
    if not qualified:
        last = matched[-1]
        _, last_why, last_meta = _gate_check(
            _pattern_stats("1h", last["pattern"]), last["dir"])
        rec["gate"] = {"pattern": last["pattern"], "passed": False,
                       "reason": last_why, **last_meta}
        return rec  # netral — ada pola searah, tapi tidak ada yang lolos gate

    sig, stats, why_ok, meta_ok = qualified[-1]
    rec["gate"] = {"pattern": sig["pattern"], "passed": True,
                   "reason": why_ok, **meta_ok}
    rec["rationale"].append(why_ok)
    atr = float(df1["atr14"].iloc[-1])
    entry = float(df1["c"].iloc[-1])
    d = sig["dir"]
    levels = {
        "entry": round(entry, 2),
        "sl": round(entry - d * DEFAULT_SL_ATR * atr, 2),
        "tp1": round(entry + d * DEFAULT_TP_ATR * atr, 2),
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
        # bar closed terakhir = basis level entry (close bar ini). Feedback
        # loop memakai ini supaya penilaian TP/SL mulai dari bar SETELAH bar
        # sinyal, persis backtest (audit 2026-09-20: filter created_at
        # membuat bar pertama posisi terlewati dari pengecekan).
        "signal_bar_t": h1_bars[-1]["t"],
    })

    # stats sudah diambil saat cek gate (qualified/eksperimen) — pasti ada
    if stats:
        nm = "long" if d == 1 else "short"
        conf = (stats.get(f"win_rate_{nm}") or stats.get("oos_win_rate")
                or stats.get("win_rate"))
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
        dir_wr = stats.get(f"win_rate_{nm}")
        dir_n = stats.get(f"n_{nm}")
        dir_txt = (f", arah {nm}: {(dir_wr or 0) * 100:.0f}% (n={dir_n})"
                   if dir_wr is not None else "")
        rec["confidence_note"] = (
            f"win-rate historis pola '{sig['pattern']}' H1: "
            f"{(stats.get('win_rate') or 0) * 100:.0f}% (n={stats.get('n')}, "
            f"OOS {(stats.get('oos_win_rate') or 0) * 100:.0f}%{ci_txt}"
            f"{dir_txt}) — probabilitas, bukan jaminan")
    else:
        rec["confidence_note"] = "statistik pola belum tersedia (jalankan research)"
    rec["rationale"].append(
        f"SL {DEFAULT_SL_ATR}xATR, TP1 {DEFAULT_TP_ATR}xATR "
        f"(ATR14 H1 = {levels['atr14']})")
    return rec