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


def build_payload(all_results: list[dict], horizon_map: dict) -> dict:
    """Rangkum hasil evaluasi jadi payload patterns.json (dipakai juga jobs.daily)."""
    full = backtest.summarize(all_results)["results"]
    _, oos = backtest.oos_split(all_results)
    oos_map = {(r["tf"], r["pattern"]): r for r in oos}

    merged = []
    for r in full:
        o = oos_map.get((r["tf"], r["pattern"]), {})
        merged.append({
            **r,
            "oos_n": o.get("resolved", 0),
            "oos_win_rate": o.get("win_rate"),
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
        },
        "note": ("win_rate = TP 1.5xATR tercapai sebelum SL 1xATR dalam horizon bar; "
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