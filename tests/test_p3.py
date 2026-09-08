"""Test P3: indikator, deteksi pola, matematika backtest, OOS split.

Run: python tests/test_p3.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from analyzer import backtest, patterns, store  # noqa: E402
from analyzer.indicators import add_indicators, atr, rsi  # noqa: E402


def df_of(rows):
    """rows: list of (o, h, l, c) -> df indexed by string timestamps."""
    bars = [{"t": f"2026-01-{i//24+1:02d}T{i%24:02d}:00:00Z",
             "o": o, "h": h, "l": l, "c": c} for i, (o, h, l, c) in enumerate(rows)]
    return pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]]


def test_indicators():
    # 20 bar turun konsisten -> RSI rendah, trend -1
    rows = [(100 - i, 100.5 - i, 99.5 - i, 100 - i - 0.4) for i in range(60)]
    df = add_indicators(df_of(rows))
    assert df["rsi14"].iloc[-1] < 30, df["rsi14"].iloc[-1]
    assert df["trend"].iloc[-1] == -1
    assert (df["atr14"] > 0).all()
    # 20 bar naik -> trend +1, RSI tinggi
    rows = [(100 + i, 100.5 + i, 99.5 + i, 100 + i + 0.4) for i in range(60)]
    df = add_indicators(df_of(rows))
    assert df["trend"].iloc[-1] == 1
    assert df["rsi14"].iloc[-1] > 70
    print("ok: indikator (RSI, ATR, trend)")


def test_patterns():
    rows = [(100 + i * 0.5, 100.5 + i * 0.5, 99.5 + i * 0.5, 100.4 + i * 0.5)
            for i in range(60)]  # uptrend
    # bar 60: engulfing bullish (red kecil lalu green besar menelan)
    rows.append((130.0, 130.2, 129.6, 129.8))          # red
    rows.append((129.7, 132.0, 129.6, 131.5))          # engulfing green
    df = df_of(rows)
    df = patterns.add_indicators(df)
    sigs = patterns.detect_signals(df, "1h")
    engulf = [s for s in sigs if s["pattern"] == "bullish_engulfing" and s["index"] == 61]
    assert engulf, sigs
    assert engulf[0]["dir"] == 1
    # inside bar di bar terakhir (range kecil di dalam bar 61)
    rows.append((131.0, 131.3, 130.8, 131.1))
    df = patterns.add_indicators(df_of(rows))
    sigs = patterns.detect_signals(df, "1h")
    ib = [s for s in sigs if s["pattern"] == "inside_bar" and s["index"] == 62]
    assert ib, sigs
    print("ok: deteksi pola (bullish engulfing, inside bar) dengan konteks trend")


def test_backtest_math():
    # df dengan atr stabil ~10: bar-bar normal lalu sinyal manual
    rows = [(100, 110, 90, 100)] * 40
    df = patterns.add_indicators(df_of(rows))
    atr_val = float(df["atr14"].iloc[10])
    assert 18 < atr_val < 21, atr_val  # TR=20

    # entry idx 11, open=100, ATR~20 -> tp=+1.5*atr, sl=-1.0*atr
    sig = {"t": df.index[10], "tf": "1h", "pattern": "x", "dir": 1, "index": 10}

    # case 1: bar entry menusuk SL saja -> loss, r=-1
    rows[11] = (100, 105, 60, 95)  # low 60 << sl, high < tp
    res = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "loss" and res[0]["r"] == -1.0, res

    # case 2: menusuk TP saja -> win, r=1.5
    rows[11] = (100, 140, 99, 135)
    res = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "win" and res[0]["r"] == 1.5, res

    # case 3: TP dan SL di bar sama -> konservatif: LOSS
    rows[11] = (100, 140, 60, 95)
    res = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "loss", res

    # case 4: tidak kena apa pun -> timeout dengan R parsial (close bar entry 110)
    rows[11] = (100, 110, 95, 110)
    res = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 1)  # horizon 1 bar
    assert res[0]["outcome"] == "timeout", res
    assert 0 < res[0]["r"] < 1.5, res  # (110-100)/atr ≈ 0.5
    print("ok: matematika backtest (win/loss/timeout, aturan konservatif, R parsial)")


def test_oos_split():
    results = ([{"tf": "1h", "pattern": "p", "outcome": "win", "r": 1.5}] * 7 +
              [{"tf": "1h", "pattern": "p", "outcome": "loss", "r": -1.0}] * 3)
    ins, oos = backtest.oos_split(results)
    assert ins[0]["n"] == 7 and oos[0]["n"] == 3, (ins, oos)
    print("ok: OOS split 70/30")


def test_cost_evaluation():
    """cost_usd menggeser barrier melawan trader: TP harus tercapai lebih
    JAUH (+cost) dan SL kena lebih DEKAT (−cost) — win-rate turun jujur,
    R nominal per trade tetap (biaya masuk ke probabilitas, bukan ukuran)."""
    rows = [(100, 110, 90, 100)] * 40
    df = patterns.add_indicators(df_of(rows))
    sig = {"t": df.index[10], "tf": "1h", "pattern": "x", "dir": 1, "index": 10}

    # gross: tp=+30 (1.5*atr~20), sl=-20; net (cost 5): tp=+35, sl=-15
    # bar high 131: gross KENA TP (r=1.5), net TIDAK kena TP (butuh 135)
    rows[11] = (100, 131, 99, 125)
    res_gross = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5)
    res_net = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5,
                                cost_usd=5.0)
    assert res_gross[0]["outcome"] == "win" and res_gross[0]["r"] == 1.5, res_gross
    assert res_net[0]["outcome"] != "win", res_net  # TP net di 135, high 131 kurang

    # bar low 82: gross TIDAK kena SL (butuh <=80), net KENA SL (85)
    rows[11] = (100, 105, 82, 95)
    res_gross = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5)
    res_net = backtest.evaluate(patterns.add_indicators(df_of(rows)), [sig], 5,
                                cost_usd=5.0)
    assert res_gross[0]["outcome"] != "loss", res_gross  # SL gross di 80, low 82 lolos
    assert res_net[0]["outcome"] == "loss" and res_net[0]["r"] == -1.0, res_net
    print("ok: evaluasi net of cost (TP lebih jauh, SL lebih dekat, R tetap)")


def test_wilson_ci():
    lo, hi = backtest.wilson_ci(0, 0)
    assert lo is None and hi is None
    lo, hi = backtest.wilson_ci(10, 10)
    assert hi == 1.0 and lo > 0.7, (lo, hi)
    lo, hi = backtest.wilson_ci(0, 10)
    assert lo == 0.0 and hi < 0.35, (lo, hi)
    # interval menyempit saat n membesar (p tetap 0.5)
    _, hi50 = backtest.wilson_ci(25, 50)
    _, hi400 = backtest.wilson_ci(200, 400)
    assert hi400 < hi50, (hi50, hi400)
    # summarize membawa CI
    results = ([{"tf": "1h", "pattern": "p", "outcome": "win", "r": 1.5}] * 7 +
              [{"tf": "1h", "pattern": "p", "outcome": "loss", "r": -1.0}] * 3)
    row = backtest.summarize(results)["results"][0]
    assert row["win_rate_lo"] < 0.7 < row["win_rate_hi"], row
    print("ok: interval kepercayaan Wilson 95% (bound, penyempitan, summarize)")


def test_dedup_signals():
    from analyzer.jobs import research
    sigs = [
        {"dir": 1, "index": 10, "pattern": "a"},
        {"dir": 1, "index": 12, "pattern": "b"},   # overlap dgn idx 10 -> drop
        {"dir": 1, "index": 15, "pattern": "c"},   # jarak 5 dari 10 -> keep
        {"dir": -1, "index": 16, "pattern": "a"},  # arah beda -> keep
        {"dir": 1, "index": 18, "pattern": "d"},   # jarak 3 dari 15 -> drop
    ]
    out = research.dedup_signals(sigs, cooldown=4)
    assert [s["index"] for s in out] == [10, 15, 16], out
    # tanpa dedup tak berubah
    assert research.dedup_signals(sigs, cooldown=0) == sigs
    print("ok: dedup sinyal overlap (kluster searah, keep first)")


def test_tp2_and_risk_stats():
    rows = [(100, 110, 90, 100)] * 40  # atr ~20
    df = patterns.add_indicators(df_of(rows))
    sig = {"t": df.index[10], "tf": "1h", "pattern": "x", "dir": 1, "index": 10}

    # TP1 (+30) kena, lalu bar besar menusuk TP2 (+60) -> "tp2"
    rows[11] = (100, 165, 95, 160)
    res = backtest.evaluate_tp2(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "tp2", res

    # TP1 kena lalu harga kembali ke entry -> "be"
    rows[11] = (100, 135, 99, 101)
    rows[12] = (101, 110, 99, 105)
    res = backtest.evaluate_tp2(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "be", res

    # SL duluan sebelum TP1 -> "stopped"
    rows[11] = (100, 105, 60, 95)
    res = backtest.evaluate_tp2(patterns.add_indicators(df_of(rows)), [sig], 5)
    assert res[0]["outcome"] == "stopped", res

    # risk_stats: drawdown & streak dari seri R
    results = [
        {"tf": "1h", "pattern": "x", "r": 1.5},
        {"tf": "1h", "pattern": "x", "r": -1.0},
        {"tf": "1h", "pattern": "x", "r": -1.0},
        {"tf": "1h", "pattern": "x", "r": -1.0},
        {"tf": "1h", "pattern": "x", "r": 1.5},
    ]
    rk = backtest.risk_stats(results)[("1h", "x")]
    assert rk["max_loss_streak"] == 3, rk
    assert rk["max_dd_r"] == -3.0, rk  # ekuitas: +1.5 -> -1.5 (puncak 1.5, DD -3)
    print("ok: TP2 runner (tp2/be/stopped) + risk stats (DD, loss streak)")


def main() -> int:
    test_indicators()
    test_patterns()
    test_backtest_math()
    test_oos_split()
    test_cost_evaluation()
    test_wilson_ci()
    test_dedup_signals()
    test_tp2_and_risk_stats()
    print("\nALL P3 TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())