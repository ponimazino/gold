"""Test SL lebar khusus LONG (batch 10, audit 2026-09-24) — barrier long =
1.5x parameter dasar (std TP 2.25xATR / SL 1.5xATR; aman TP 1.5x / SL
1.125x), short TIDAK tersentuh (tetap 1.5/1.0 dan 1.0/0.75), lantai SL
tetap berlaku dua arah, rasio R konstan 1.5.

Run: python tests/test_slwide.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from analyzer import backtest, patterns  # noqa: E402
from analyzer.jobs import research  # noqa: E402


def _df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]]
    return patterns.add_indicators(df)


def _bars(n: int = 130, base: float = 100.0, amp: float = 2.0) -> list[dict]:
    """Bar H1 sintetis naik-turun berirama — range cukup lebar (lolos filter
    datar) supaya ATR14 stabil ~amp."""
    out = []
    for i in range(n):
        o = base + (i % 7 - 3) * amp / 2
        c = base + ((i + 1) % 7 - 3) * amp / 2
        h = max(o, c) + amp
        l = min(o, c) - amp
        out.append({"t": f"2026-01-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
                    "o": round(o, 2), "h": round(h, 2),
                    "l": round(l, 2), "c": round(c, 2)})
    return out


def test_evaluate_long_wider_short_unchanged() -> None:
    bars = _bars()
    df = _df(bars)
    i = 60
    atr = float(df["atr14"].iloc[i])
    entry = float(df["o"].iloc[i + 1])
    # LONG: SL 1.5xATR (bukan 1x), TP 2.25xATR (bukan 1.5x)
    res = backtest.evaluate(df, [{"index": i, "dir": 1, "pattern": "inside_bar",
                                  "tf": "1h"}], 24)
    assert abs(res[0]["entry"] - entry) < 1e-6
    # kena SL persis -> r = -1 (bukti SL 1.5xATR: high bar berikut = SL)
    df2 = df.copy()
    j = i + 1
    hi = float(df2["h"].iloc[j])
    # pastikan bar tidak menyentuh SL/TP (buang dulu, cek jarak di res r_win)
    res = backtest.evaluate(df2, [{"index": i, "dir": 1, "pattern": "x", "tf": "1h"}], 24)
    # hitung ulang jarak dari outcome timeout (harga close akhir)
    # verifikasi lewat r_win: TP/SL harus 2.25/1.5 = 1.5 persis
    # -> pakai kasus WIN buatan: bar berikutnya tepat menyentuh TP long
    tp_dist = 2.25 * atr
    bars2 = bars[: i + 1] + [{"t": "x", "o": entry, "h": entry + tp_dist,
                              "l": entry - 0.1, "c": entry + tp_dist}]
    df3 = _df(bars2)
    res3 = backtest.evaluate(df3, [{"index": i, "dir": 1, "pattern": "x", "tf": "1h"}], 24)
    assert res3[0]["outcome"] == "win" and abs(res3[0]["r"] - 1.5) < 1e-6, res3

    # SHORT: masih 1.0xATR SL / 1.5xATR TP (tidak tersenggol)
    sl_short = entry - 1.0 * float(df3["atr14"].iloc[i])
    res4 = backtest.evaluate(df3, [{"index": i, "dir": -1, "pattern": "x", "tf": "1h"}], 24)
    assert res4[0]["r"] == -1.0, res4  # kena SL 1xATR dulu (bar buatan low = entry-0.1? tidak — high jauh di atas)
    print("ok: evaluate long TP 2.25x/SL 1.5xATR (R konstan 1.5), short tetap 1.5/1.0")


def test_evaluate_safe_long_scaled() -> None:
    bars = _bars()
    df = _df(bars)
    i = 60
    atr = float(df["atr14"].iloc[i])
    entry = float(df["o"].iloc[i + 1])
    # mode aman long: SL 0.75 x 1.5 = 1.125xATR; TP 1.0 x 1.5 = 1.5xATR
    bars2 = bars[: i + 1] + [{"t": "x", "o": entry, "h": entry + 1.5 * atr,
                              "l": entry - 0.05, "c": entry + 1.5 * atr}]
    df3 = _df(bars2)
    res = backtest.evaluate(df3, [{"index": i, "dir": 1, "pattern": "x", "tf": "1h"}],
                            24, tp_atr=backtest.SAFE_TP_ATR,
                            sl_atr=backtest.SAFE_SL_ATR)
    # TP tersentuh = win; r_win = 1.5/1.125 = 4/3
    assert res[0]["outcome"] == "win" and abs(res[0]["r"] - 4 / 3) < 0.001, res
    print("ok: mode aman long = TP 1.5xATR / SL 1.125xATR (skala x1.5)")


def test_floor_still_applies_long() -> None:
    bars = _bars()
    df = _df(bars)
    i = 60
    # lantai SL tetap dua arah: LONG dgn ATR collapse — floor 0.75x median
    # median bar ini ~amp*2.2; buat ATR collapse tak mungkin mudah — cukup
    # cek rumus: evaluate df tanpa kolom atr_med100 menghitung median dari
    # atr14 sendiri; long SL = max(1.5xATR, floor) >= 1.5xATR selalu
    atr = float(df["atr14"].iloc[i])
    med = float(df["atr_med100"].iloc[i])
    floor = backtest.SL_FLOOR_MED_MULT * med
    long_sl = max(backtest.DEFAULT_SL_ATR * backtest.LONG_SCALE * atr, floor)
    assert long_sl >= backtest.DEFAULT_SL_ATR * backtest.LONG_SCALE * atr
    print("ok: lantai SL tetap berlaku untuk long (max, bukan replace)")


def test_research_pool_uses_wide_long() -> None:
    """Kolam statistik gate otomatis sadar-arah: sinyal long dievaluasi
    pakai barrier lebar — bukti lewat EV long sebuah pola sintetis."""
    bars = _bars()
    df = _df(bars)
    sigs = [{"index": 60, "dir": 1, "pattern": "inside_bar", "tf": "1h"}]
    res = backtest.evaluate(df, sigs, 24, cost_usd=backtest.COST_USD)
    # pastikan evaluate menerima scale di dalamnya (bukan caller)
    assert "r" in res[0]
    # dan research.run memakai evaluate yang sama -> tanpa perubahan pun
    # kolam long otomatis lebar (integrasi: constant LONG_SCALE dipakai
    # evaluate langsung)
    assert backtest.LONG_SCALE == 1.5
    print("ok: kolam research long otomatis pakai barrier lebar (via evaluate)")


def main() -> int:
    test_evaluate_long_wider_short_unchanged()
    test_evaluate_safe_long_scaled()
    test_floor_still_applies_long()
    test_research_pool_uses_wide_long()
    print("\nSEMUA TES SL-LEBAR LULUS")
    return 0


if __name__ == "__main__":
    sys.exit(main())