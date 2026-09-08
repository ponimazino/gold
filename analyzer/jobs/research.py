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
        "win_rate": "cost_win_rate", "oos_win_rate": "cost_oos_win_rate"})
    safe_cost_map = _rule_map(safe_cost_results, {
        "win_rate": "safe_cost_win_rate",
        "oos_win_rate": "safe_cost_oos_win_rate"})

    merged = []
    for r in full:
        o = oos_map.get((r["tf"], r["pattern"]), {})
        merged.append({
            **r,
            "oos_n": o.get("resolved", 0),
            "oos_win_rate": o.get("win_rate"),
            **safe_map.get((r["tf"], r["pattern"]), {}),
            **cost_map.get((r["tf"], r["pattern"]), {}),
            **safe_cost_map.get((r["tf"], r["pattern"]), {}),
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
                 "TP+SL di bar yang sama dihitung LOSS (konservatif); "
                 "oos_win_rate = 30% data terakhir (cek overfitting); "
                 f"sinyal tumpang tindih dalam {COOLDOWN_BARS} bar didedup (n jujur)"),
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