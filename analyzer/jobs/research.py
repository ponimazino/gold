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


def bars_to_df(bars: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]]


def run(tf: str, horizon: int) -> list[dict]:
    bars = store.load(tf)
    if not bars:
        print(f"[research] {tf}: belum ada data, lewati (jalankan backfill/sync dulu)")
        return []
    df = bars_to_df(bars)
    signals = patterns.detect_signals(df, tf)
    ind = patterns.add_indicators(df)
    results = backtest.evaluate(ind, signals, horizon)
    print(f"[research] {tf}: {len(df)} bar, {len(signals)} sinyal, "
          f"{len(results)} terevaluasi")
    return results


def run_pair(tf: str, horizon: int) -> tuple[list[dict], list[dict]]:
    """Evaluasi dua rule sekaligus (standar + mode aman) dengan sinyal dan
    indikator yang dihitung SEKALI — dipakai job daily supaya patterns.json
    punya win-rate jujur untuk kedua set level."""
    bars = store.load(tf)
    if not bars:
        print(f"[research] {tf}: belum ada data, lewati (jalankan backfill/sync dulu)")
        return [], []
    df = bars_to_df(bars)
    signals = patterns.detect_signals(df, tf)
    ind = patterns.add_indicators(df)
    std = backtest.evaluate(ind, signals, horizon)
    safe = backtest.evaluate(ind, signals, horizon,
                             tp_atr=backtest.SAFE_TP_ATR, sl_atr=backtest.SAFE_SL_ATR)
    print(f"[research] {tf}: {len(df)} bar, {len(signals)} sinyal, "
          f"std {len(std)} / aman {len(safe)} terevaluasi")
    return std, safe


def build_payload(all_results: list[dict], horizon_map: dict,
                  safe_results: list[dict] | None = None) -> dict:
    """Rangkum hasil evaluasi jadi payload patterns.json (dipakai juga jobs.daily).
    safe_results = evaluasi rule mode aman (TP 1xATR / SL 0.75xATR) —
    digabung per pola jadi safe_n / safe_win_rate / safe_oos_win_rate."""
    full = backtest.summarize(all_results)["results"]
    _, oos = backtest.oos_split(all_results)
    oos_map = {(r["tf"], r["pattern"]): r for r in oos}
    safe_map: dict[tuple, dict] = {}
    if safe_results:
        safe_full = backtest.summarize(safe_results)["results"]
        _, safe_oos = backtest.oos_split(safe_results)
        safe_oos_map = {(r["tf"], r["pattern"]): r for r in safe_oos}
        safe_map = {
            (r["tf"], r["pattern"]): {
                "safe_n": r["n"],
                "safe_win_rate": r["win_rate"],
                "safe_oos_n": safe_oos_map.get((r["tf"], r["pattern"]), {}).get("resolved", 0),
                "safe_oos_win_rate": safe_oos_map.get((r["tf"], r["pattern"]), {}).get("win_rate"),
            }
            for r in safe_full
        }

    merged = []
    for r in full:
        o = oos_map.get((r["tf"], r["pattern"]), {})
        merged.append({
            **r,
            "oos_n": o.get("resolved", 0),
            "oos_win_rate": o.get("win_rate"),
            **safe_map.get((r["tf"], r["pattern"]), {}),
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
        },
        "note": ("win_rate = TP 1.5xATR tercapai sebelum SL 1xATR dalam horizon bar; "
                 "safe_win_rate = rule mode aman TP 1xATR / SL 0.75xATR; "
                 "TP+SL di bar yang sama dihitung LOSS (konservatif); "
                 "oos_win_rate = 30% data terakhir (cek overfitting)"),
        "results": merged,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Statistik pola historis")
    parser.add_argument("--horizon", type=int, default=0,
                        help="bar lookahead (default: 24 untuk 1h, 12 untuk 4h)")
    args = parser.parse_args()

    all_results: list[dict] = []
    horizon_map = {}
    for tf in INTERVALS:
        horizon = args.horizon or DEFAULT_HORIZON[tf]
        horizon_map[tf] = horizon
        all_results.extend(run(tf, horizon))

    if not all_results:
        print("[research] tidak ada data — tidak ada output")
        return 1

    payload = build_payload(all_results, horizon_map)
    store.write_json("patterns.json", payload)
    print(f"[research] ditulis: data/patterns.json ({len(merged)} pola)")
    for r in merged:
        print(f"[research]   {r['tf']:>2} {r['pattern']:<18} n={r['n']:>4} "
              f"win={r['win_rate']} oos={r['oos_win_rate']} avgR={r['avg_r']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())