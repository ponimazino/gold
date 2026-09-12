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
COOLDOWN_BARS = 4


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


def dedup_signals(signals: list[dict], cooldown: int = COOLDOWN_BARS) -> list[dict]:
    """Buang sinyal yang tumpang tindih dengan sinyal SEARAH sebelumnya dalam
    `cooldown` bar (apa pun polanya) — sinyal pertama tiap klaster yang
    dipakai. Hanya untuk statistik research, bukan sinyal live."""
    last_idx: dict[int, int] = {}
    out: list[dict] = []
    for s in signals:
        d = s["dir"]
        if d in last_idx and s["index"] - last_idx[d] <= cooldown:
            continue
        last_idx[d] = s["index"]
        out.append(s)
    return out


def run(tf: str, horizon: int, dedup: bool = True) -> list[dict]:
    bars = store.load(tf)
    if not bars:
        print(f"[research] {tf}: belum ada data, lewati (jalankan backfill/sync dulu)")
        return []
    df = bars_to_df(bars)
    signals = patterns.detect_signals(df, tf)
    if dedup:
        signals = dedup_signals(signals)
    ind = patterns.add_indicators(df)
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
    signals = dedup_signals(patterns.detect_signals(df, tf))
    if tf == "1h":
        # statistik per arah dipakai gate sadar-arah -> wajib mengukur kolam
        # SEARAH H4 (aturan live), bukan semua sinyal H1-trend
        signals = _tag_h4_alignment(signals, df)
    ind = patterns.add_indicators(df)
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
            "safe_tp_atr": backtest.SAFE_TP_ATR,
            "safe_sl_atr": backtest.SAFE_SL_ATR,
            "cost_usd": backtest.COST_USD,
            "cooldown_bars": COOLDOWN_BARS,
        },
        "note": ("win_rate = TP 1.5xATR tercapai sebelum SL 1xATR dalam horizon bar "
                 "(interval kepercayaan 95%: win_rate_lo/hi); "
                 "safe_win_rate = rule mode aman TP 1xATR / SL 0.75xATR; "
                 "cost_*/safe_cost_* = NET of cost (spread "
                 f"${backtest.COST_USD:.2f}/oz: barrier TP lebih jauh, SL lebih "
                 "dekat dalam harga mid; R nominal tetap); "
                 "cost_avg_r = EV per sinyal NET (rata-rata R, timeout ikut "
                 "dihitung) — dipakai gate kualitas rekomendasi; "
                 "TP+SL di bar yang sama dihitung LOSS (konservatif); "
                 "oos_win_rate = 30% data terakhir (cek overfitting); "
                 f"sinyal tumpang tindih dalam {COOLDOWN_BARS} bar didedup (n jujur); "
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