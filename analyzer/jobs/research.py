"""Job riset: statistik historis tiap pola -> data/patterns.json.

Local:  python -m analyzer.jobs.research
GitHub: nanti dijadwalkan di workflow analisa harian (P5).

Output inilah "otak statistik" sistem: berapa persen sinyal pola X searah
trend mencapai TP 1.5xATR sebelum SL 1xATR, in-sample vs out-of-sample.
Rekomendasi harian (P5) akan mengambil angka-angka ini sebagai confidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .. import backtest, patterns, store
from ..backtest import DEFAULT_HORIZON
from ..config import INTERVALS

# Sinyal searah yang berada dalam N bar dari sinyal searah sebelumnya
# menghitung pergerakan yang sama berkali-kali (overlap). Untuk statistik
# research, sinyal tumpang tindih dibuang (purging sederhana) supaya n dan
# win-rate jujur. Deteksi pola untuk rekomendasi LIVE tidak didedup.
# 4 -> 2 bar (2026-09-21, audit frekuensi): diuji di data 3 tahun — dedup
# 2 bar menaikkan jumlah sinyal +27% dengan EV net OOS justru stabil/naik
# (inside_bar long +0.150R -> +0.182R, short +0.230R -> +0.186R); 1 bar
# sudah terlalu bocor (short merosot, DD -44R). Cooldown live daily.py
# mengikuti nilai ini (2 jam) supaya statistik gate = aturan live.
# SADAR-HASIL (batch 7, 2026-09-23, audit scripts/audit_cooldown_outcome.py):
# dedup DILEWATI kalau sinyal searah sebelumnya sudah mencapai TP1 sebelum
# bar sinyal baru — re-entry pasca-TP1 EV net +0.211R (n=41 aligned) dan
# live-nya juga dilewati (posisi searah terakhir win). Rantai while-running
# tetap dibuang (DD inside_bar short -58R -> -79R bila dilepas + live memang
# diblok aturan 1-posisi-aktif) — persis aturan live apply_cooldown.
COOLDOWN_BARS = 2


def _tag_h4_alignment(signals: list[dict], df1: pd.DataFrame) -> list[dict]:
    """Tandai tiap sinyal H1 dengan 'aligned_h4' — apakah trend H4 AS-OF
    searah sinyalnya, persis aturan live rekomendasi (pola H1 searah trend
    H4). As-of: bar H1 waktu T closes T+1j, jadi trend H4 yang sah = bar H4
    terakhir yang sudah CLOSE sebelum T+1j (t_h4 + 4j <= T + 1j). Tanpa
    filter ini statistik per arah mengukur kolam sinyal H1-saja, padahal
    live HANYA memperdagangkan subset aligned — bukti gate harus dari kolam
    yang sama dengan yang diperdagangkan (audit 2026-09-12: inside_bar long
    aligned +0.03R OOS vs unaligned -0.04R)."""
    bars4 = store.load("4h")
    if not bars4 or not signals:
        for s in signals:
            s.setdefault("aligned_h4", True)
        return signals
    df4 = patterns.add_indicators(bars_to_df(bars4))
    t4 = pd.to_datetime(df4.index, utc=True) + pd.Timedelta(hours=4)
    keys = list(t4)  # waktu close tiap bar H4
    tr4 = df4["trend"].values
    import bisect
    for s in signals:
        T = pd.to_datetime(s["t"], utc=True) + pd.Timedelta(hours=1)
        i = bisect.bisect_right(keys, T) - 1
        s["aligned_h4"] = (i >= 0 and int(tr4[i]) == s["dir"])
    return signals


def bars_to_df(bars: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]]


def _signal_hit_tp1(ind: pd.DataFrame, p: dict, s: dict) -> bool:
    """True kalau sinyal p (searah) mencapai TP1 SEBELUM SL pada bar entry p
    s.d. bar sinyal baru s (close bar s = momen analisa live). Persis rule
    live: entry = open bar berikutnya, TP 1.5xATR / SL 1xATR + lantai
    0.75x median ATR-100, SL dicek dulu per bar (konservatif, sama
    backtest.evaluate). Dipakai dedup sadar-hasil."""
    entry_idx = p["index"] + 1
    if entry_idx >= len(ind) or entry_idx > s["index"]:
        return False
    entry = float(ind["o"].iloc[entry_idx])
    atr = float(ind["atr14"].iloc[p["index"]])
    if atr <= 0 or atr != atr:  # 0 / NaN (data terlalu awal)
        return False
    d = p["dir"]
    sl_dist = backtest.DEFAULT_SL_ATR * atr
    med = ind["atr_med100"].iloc[p["index"]] if "atr_med100" in ind.columns else float("nan")
    med = float(med) if med == med else 0.0
    if med > 0:
        sl_dist = max(sl_dist, backtest.SL_FLOOR_MED_MULT * med)
    sl = entry - d * sl_dist
    tp = entry + d * backtest.DEFAULT_TP_ATR * atr
    for j in range(entry_idx, s["index"] + 1):
        bar = ind.iloc[j]
        hit_sl = (bar["l"] <= sl) if d == 1 else (bar["h"] >= sl)
        hit_tp = (bar["h"] >= tp) if d == 1 else (bar["l"] <= tp)
        if hit_sl:
            return False
        if hit_tp:
            return True
    return False


def dedup_signals(signals: list[dict], cooldown: int = COOLDOWN_BARS,
                  ind: pd.DataFrame | None = None) -> list[dict]:
    """Buang sinyal yang tumpang tindih dengan sinyal SEARAH sebelumnya dalam
    `cooldown` bar (apa pun polanya) — sinyal pertama tiap klaster yang
    dipakai. Hanya untuk statistik research, bukan sinyal live.

    ind diberikan -> dedup SADAR-HASIL (batch 7, 2026-09-23): sinyal searah
    < cooldown bar tetap DIPAKAI kalau sinyal sebelumnya sudah mencapai TP1
    sebelum bar sinyal baru (re-entry pasca-TP1, persis aturan live
    daily.apply_cooldown yang melewati cooldown setelah posisi searah
    terakhir win). Tanpa ind -> dedup tak bersyarat (backward compat)."""
    last: dict[int, dict] = {}
    out: list[dict] = []
    for s in signals:
        d = s["dir"]
        p = last.get(d)
        if p is not None and s["index"] - p["index"] <= cooldown:
            if ind is None or not _signal_hit_tp1(ind, p, s):
                continue  # sebelumnya belum TP1 (SL/timeout/masih jalan) -> buang
        last[d] = s
        out.append(s)
    return out


def run(tf: str, horizon: int, dedup: bool = True) -> list[dict]:
    bars = store.load(tf)
    if not bars:
        print(f"[research] {tf}: belum ada data, lewati (jalankan backfill/sync dulu)")
        return []
    df = bars_to_df(bars)
    ind = patterns.add_indicators(df)
    signals = patterns.detect_signals(df, tf)
    if dedup:
        signals = dedup_signals(signals, ind=ind)
    results = backtest.evaluate(ind, signals, horizon)
    print(f"[research] {tf}: {len(df)} bar, {len(signals)} sinyal, "
          f"{len(results)} terevaluasi")
    return results


def run_all(tf: str, horizon: int) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Evaluasi EMPAT rule sekaligus — (standar, mode aman) x (gross, net of
    cost) — dengan sinyal dedup + indikator yang dihitung SEKALI. Dipakai job
    daily supaya patterns.json punya win-rate jujur untuk semua kombinasi.

    Return (std, safe, std_cost, safe_cost)."""
    bars = store.load(tf)
    if not bars:
        print(f"[research] {tf}: belum ada data, lewati (jalankan backfill/sync dulu)")
        return [], [], [], []
    df = bars_to_df(bars)
    ind = patterns.add_indicators(df)
    signals = dedup_signals(patterns.detect_signals(df, tf), ind=ind)
    if tf == "1h":
        # statistik per arah dipakai gate sadar-arah -> wajib mengukur kolam
        # SEARAH H4 (aturan live), bukan semua sinyal H1-trend
        signals = _tag_h4_alignment(signals, df)
    std = backtest.evaluate(ind, signals, horizon)
    safe = backtest.evaluate(ind, signals, horizon,
                             tp_atr=backtest.SAFE_TP_ATR, sl_atr=backtest.SAFE_SL_ATR)
    std_cost = backtest.evaluate(ind, signals, horizon, cost_usd=backtest.COST_USD)
    safe_cost = backtest.evaluate(ind, signals, horizon,
                                  tp_atr=backtest.SAFE_TP_ATR,
                                  sl_atr=backtest.SAFE_SL_ATR,
                                  cost_usd=backtest.COST_USD)
    print(f"[research] {tf}: {len(df)} bar, {len(signals)} sinyal (dedup) — "
          f"std {len(std)} / aman {len(safe)} / std-net {len(std_cost)} / "
          f"aman-net {len(safe_cost)} terevaluasi")
    return std, safe, std_cost, safe_cost


def _direction_map(cost_results: list[dict] | None) -> dict[tuple, dict]:
    """Statistik net-of-cost PER ARAH sinyal, HANYA dari sinyal yang searah
    trend H4 as-of (aturan live): {tf,pattern} -> {n_long, win_rate_long,
    cost_avg_r_long, cost_oos_avg_r_long, ..._short}. Dipakai gate sadar-arah
    rekomendasi (keputusan user 2026-09-12): long dan short dinilai buktinya
    masing-masing — inside_bar long +0.12R vs short -0.13R adalah asimetri
    nyata (bull market 2023-26), arah itu informasi, bukan noise. Kolam
    harus sama dengan yang live diperdagangkan: sinyal tanpa aligned_h4
    (data lama) dianggap aligned supaya backward-compat. OOS 30% per arah,
    urut waktu."""
    if not cost_results:
        return {}
    grouped: dict[tuple, dict[int, list[dict]]] = {}
    for r in cost_results:
        if not r.get("aligned_h4", True):
            continue
        grouped.setdefault((r["tf"], r["pattern"]), {}).setdefault(
            r.get("dir", 0), []).append(r)
    out: dict[tuple, dict] = {}
    for key, by_dir in grouped.items():
        row: dict = {}
        for d, nm in ((1, "long"), (-1, "short")):
            rows = by_dir.get(d) or []
            if not rows:
                continue
            wins = sum(1 for r in rows if r["outcome"] == "win")
            losses = sum(1 for r in rows if r["outcome"] == "loss")
            resolved = wins + losses
            oos = rows[int(len(rows) * 0.7):]
            row[f"n_{nm}"] = len(rows)
            row[f"win_rate_{nm}"] = round(wins / resolved, 3) if resolved else None
            row[f"cost_avg_r_{nm}"] = round(sum(r["r"] for r in rows) / len(rows), 3)
            row[f"cost_oos_avg_r_{nm}"] = (
                round(sum(r["r"] for r in oos) / len(oos), 3) if oos else None)
        out[key] = row
    return out


def build_payload(all_results: list[dict], horizon_map: dict,
                  safe_results: list[dict] | None = None,
                  cost_results: list[dict] | None = None,
                  safe_cost_results: list[dict] | None = None) -> dict:
    """Rangkum hasil evaluasi jadi payload patterns.json (dipakai juga jobs.daily).
    safe_results   = rule mode aman (TP 1xATR / SL 0.75xATR) -> safe_*.
    cost_results   = rule standar net of cost (spread) -> cost_*.
    safe_cost_results = mode aman net of cost -> safe_cost_*.
    Semua argumen tambahan opsional (backward compat)."""
    full = backtest.summarize(all_results)["results"]
    _, oos = backtest.oos_split(all_results)
    oos_map = {(r["tf"], r["pattern"]): r for r in oos}

    def _rule_map(results: list[dict] | None, fields: dict[str, str]) -> dict[tuple, dict]:
        """{kolom summarize/oos -> nama field tujuan} untuk satu rule ekstra."""
        if not results:
            return {}
        r_full = backtest.summarize(results)["results"]
        _, r_oos = backtest.oos_split(results)
        r_oos_map = {(r["tf"], r["pattern"]): r for r in r_oos}
        out: dict[tuple, dict] = {}
        for r in r_full:
            key = (r["tf"], r["pattern"])
            o = r_oos_map.get(key, {})
            row = {}
            for src, dst in fields.items():
                if src == "oos_win_rate":
                    row[dst] = o.get("win_rate")
                elif src == "oos_avg_r":
                    row[dst] = o.get("avg_r")
                elif src == "oos_n":
                    row[dst] = o.get("resolved", 0)
                else:
                    row[dst] = r.get(src)
            out[key] = row
        return out

    safe_map = _rule_map(safe_results, {
        "n": "safe_n", "win_rate": "safe_win_rate",
        "oos_n": "safe_oos_n", "oos_win_rate": "safe_oos_win_rate"})
    cost_map = _rule_map(cost_results, {
        "win_rate": "cost_win_rate", "oos_win_rate": "cost_oos_win_rate",
        "avg_r": "cost_avg_r", "oos_avg_r": "cost_oos_avg_r"})
    safe_cost_map = _rule_map(safe_cost_results, {
        "win_rate": "safe_cost_win_rate",
        "oos_win_rate": "safe_cost_oos_win_rate",
        "avg_r": "safe_cost_avg_r", "oos_avg_r": "safe_cost_oos_avg_r"})

    merged = []
    dir_map = _direction_map(cost_results)
    for r in full:
        o = oos_map.get((r["tf"], r["pattern"]), {})
        merged.append({
            **r,
            "oos_n": o.get("resolved", 0),
            "oos_win_rate": o.get("win_rate"),
            **safe_map.get((r["tf"], r["pattern"]), {}),
            **cost_map.get((r["tf"], r["pattern"]), {}),
            **safe_cost_map.get((r["tf"], r["pattern"]), {}),
            **dir_map.get((r["tf"], r["pattern"]), {}),
        })
    merged.sort(key=lambda r: -r["n"])

    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_wib": now.astimezone(ZoneInfo("Asia/Jakarta"))
            .strftime("%Y-%m-%d %H:%M WIB"),
        "params": {
            "horizon": horizon_map,
            "tp_atr": backtest.DEFAULT_TP_ATR,
            "sl_atr": backtest.DEFAULT_SL_ATR,
            "long_scale": backtest.LONG_SCALE,
            "sl_floor_med": backtest.SL_FLOOR_MED_MULT,
            "safe_tp_atr": backtest.SAFE_TP_ATR,
            "safe_sl_atr": backtest.SAFE_SL_ATR,
            "cost_usd": backtest.COST_USD,
            "cooldown_bars": COOLDOWN_BARS,
            "cooldown_outcome_aware": True,
        },
        "note": ("win_rate = TP 1.5xATR tercapai sebelum SL 1xATR dalam horizon bar "
                 "(interval kepercayaan 95%: win_rate_lo/hi); "
                 "LONG memakai stop lebar: barrier long = 1.5x parameter "
                 "(TP 2.25xATR / SL 1.5xATR; aman 1.5x/1.125x) — audit 2026-09-24; "
                 "safe_win_rate = rule mode aman TP 1xATR / SL 0.75xATR; "
                 "cost_*/safe_cost_* = NET of cost (spread "
                 f"${backtest.COST_USD:.2f}/oz: barrier TP lebih jauh, SL lebih "
                 "dekat dalam harga mid; R nominal tetap); "
                 "cost_avg_r = EV per sinyal NET (rata-rata R, timeout ikut "
                 "dihitung) — dipakai gate kualitas rekomendasi; "
                 "TP+SL di bar yang sama dihitung LOSS (konservatif); "
                 "oos_win_rate = 30% data terakhir (cek overfitting); "
                 f"sinyal tumpang tindih dalam {COOLDOWN_BARS} bar didedup "
                 "(n jujur; sadar-hasil: re-entry searah tetap dihitung "
                 "kalau sinyal sebelumnya sudah kena TP1 — persis aturan "
                 "live, audit 2026-09-23); "
                 "*_long/*_short = statistik PER ARAH sinyal yang SEARAH "
                 "trend H4 as-of (aturan live, net of cost, OOS 30% per "
                 "arah) — dipakai gate sadar-arah: sinyal searah trend pun "
                 "dinilai EV arahnya sendiri"),
        "results": merged,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Statistik pola historis")
    parser.add_argument("--horizon", type=int, default=0,
                        help="bar lookahead (default: 24 untuk 1h, 12 untuk 4h)")
    args = parser.parse_args()

    all_results: list[dict] = []
    safe_results: list[dict] = []
    cost_results: list[dict] = []
    safe_cost_results: list[dict] = []
    horizon_map = {}
    for tf in INTERVALS:
        horizon = args.horizon or DEFAULT_HORIZON[tf]
        horizon_map[tf] = horizon
        std, safe, cost, safe_cost = run_all(tf, horizon)
        all_results.extend(std)
        safe_results.extend(safe)
        cost_results.extend(cost)
        safe_cost_results.extend(safe_cost)

    if not all_results:
        print("[research] tidak ada data — tidak ada output")
        return 1

    payload = build_payload(all_results, horizon_map,
                            safe_results=safe_results,
                            cost_results=cost_results,
                            safe_cost_results=safe_cost_results)
    store.write_json("patterns.json", payload)
    merged = payload["results"]
    print(f"[research] ditulis: data/patterns.json ({len(merged)} pola)")
    for r in merged:
        print(f"[research]   {r['tf']:>2} {r['pattern']:<18} n={r['n']:>4} "
              f"win={r['win_rate']} oos={r['oos_win_rate']} "
              f"net={r.get('cost_oos_win_rate')} avgR={r['avg_r']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())