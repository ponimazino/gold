"""Test batch 5 (2026-09-22): LANTAI SL = max(sl_atr*ATR14, 0.75 x median
ATR14 100 bar sebelumnya). ATR collapse (squeeze) tidak boleh membuat SL ikut
menyempit — bukti: entry 22 Sep 2026 05:16 WIB (bar sinyal range $1.57, ATR
$13, bounce 2.17xATR menyapu SL). Konsisten membaik 4 tahun dua arah.

Run: python tests/test_slfloor.py
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from analyzer import recommend, store  # noqa: E402
from analyzer.backtest import evaluate, SL_FLOOR_MED_MULT  # noqa: E402
from analyzer.indicators import add_indicators  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mk_bars(n_normal: int, n_squeeze: int, range_norm: float,
             range_sq: float, base: datetime) -> list[dict]:
    """Uptrend konsisten; n_normal bar ber-range besar lalu n_squeeze bar
    ber-range kecil (ATR collapse). Close naik 0.5/bar di kedua fase."""
    bars = []
    for i in range(n_normal + n_squeeze):
        rng = range_norm if i < n_normal else range_sq
        c = 2000.0 + i * 0.5
        half = rng / 2
        t = base + timedelta(hours=i)
        bars.append({"t": iso(t), "o": c - 0.1, "h": c + half,
                     "l": c - half, "c": c})
    return bars


def test_backtest_floor_active():
    """ATR collapse -> SL digenjangkan ke 0.75x median ATR-100 (lebih lebar
    dari 1xATR); dip yang MENGHAPUS SL 1xATR tidak lagi menyapu posisi."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = _mk_bars(100, 30, 20.0, 2.0, base)
    df = add_indicators(pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]])
    i = len(df) - 1
    atr = float(df["atr14"].iloc[i])
    med = float(df["atr_med100"].iloc[i])
    assert atr < 5, atr            # ATR sudah collapse di fase squeeze
    assert med > 15, med           # median 100 bar masih volatilitas normal
    floor = SL_FLOOR_MED_MULT * med
    assert floor > atr, (floor, atr)

    # bar entry: dip low = entry - 0.7*floor (menembus SL 1xATR lama,
    # TIDAK menembus lantai), bar berikutnya rally ke TP 1.5xATR.
    entry = float(df["c"].iloc[i])
    t0 = pd.Timestamp(df.index[i])
    entry_t = (t0 + pd.Timedelta(hours=1)).isoformat()
    dip = {"t": entry_t, "o": entry, "h": entry + 0.5,
           "l": entry - 0.7 * floor, "c": entry - 0.5 * atr}
    rally = {"t": (t0 + pd.Timedelta(hours=2)).isoformat(), "o": entry, "h": entry + 3 * atr,
             "l": entry, "c": entry + 2 * atr}
    df2 = pd.concat([df, pd.DataFrame([dip, rally]).set_index("t")])
    sig = [{"t": df.index[i], "tf": "1h", "pattern": "inside_bar",
            "dir": 1, "index": i}]
    res = evaluate(df2, sig, 24)[0]
    assert res["outcome"] == "win", res
    # R nominal mengecil jujur: tp_dist/sl_dist, bukan 1.5 kaku.
    # LONG batch 10: TP = 2.25xATR (stop lebar), lantai tetap menggenjang SL
    assert abs(res["r"] - round(2.25 * atr / floor, 3)) < 0.01, res
    # konfirmasi lantai benar-benar lebih lebar dari SL 1.5xATR (long)
    assert entry - res["entry"] < 0.01  # entry = open bar berikutnya
    print("ok: lantai SL aktif saat ATR collapse — dip 0.7x lantai tak menyapu, "
          f"R jujur {res['r']} (= 2.25xATR/lantai)")


def test_backtest_floor_inactive():
    """ATR normal -> lantai tidak mengubah SL 1xATR; SL tetap tersapu."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = _mk_bars(130, 0, 20.0, 2.0, base)
    df = add_indicators(pd.DataFrame(bars).set_index("t")[["o", "h", "l", "c"]])
    i = len(df) - 1
    atr = float(df["atr14"].iloc[i])
    med = float(df["atr_med100"].iloc[i])
    assert SL_FLOOR_MED_MULT * med < atr, (med, atr)  # lantai di bawah 1xATR

    entry = float(df["c"].iloc[i])
    t0 = pd.Timestamp(df.index[i])
    entry_t = (t0 + pd.Timedelta(hours=1)).isoformat()
    hit = {"t": entry_t, "o": entry, "h": entry + 0.5,
           "l": entry - 1.5 * atr - 1.0, "c": entry - 1.5 * atr}
    df2 = pd.concat([df, pd.DataFrame([hit]).set_index("t")])
    sig = [{"t": df.index[i], "tf": "1h", "pattern": "inside_bar",
            "dir": 1, "index": i}]
    res = evaluate(df2, sig, 24)[0]
    assert res["outcome"] == "loss", res
    assert res["r"] == -1.0, res
    print("ok: ATR normal -> lantai diam, SL 1.5xATR (long) bekerja seperti biasa")


def _seed_squeeze(now: datetime) -> None:
    """H1/H4 uptrend; H1 berakhir 20 bar squeeze (range $2) + setup engulfing
    green. H4 ber-range normal (hanya untuk trend alignment)."""
    def up_squeeze(base, n, step_h):
        bars = []
        for i in range(n):
            c = 2000.0 + i * 0.5
            rng = 2.0 if i >= n - 20 else 20.0
            half = rng / 2
            t = base + timedelta(hours=i * step_h)
            bars.append({"t": iso(t), "o": c - 0.1, "h": c + half,
                         "l": c - half, "c": c})
        # bar n: red kecil, bar n+1: engulfing green (di dalam fase squeeze)
        t_red = base + timedelta(hours=n * step_h)
        top = 2000.0 + n * 0.5
        bars.append({"t": iso(t_red), "o": top + 0.2, "h": top + 0.9,
                     "l": top - 0.9, "c": top - 0.3})
        t_eng = base + timedelta(hours=(n + 1) * step_h)
        bars.append({"t": iso(t_eng), "o": top - 0.3, "h": top + 1.4,
                     "l": top - 0.9, "c": top + 1.0})
        return bars

    store.save("1h", up_squeeze(now - timedelta(hours=202), 200, 1))
    # H4: uptrend ber-range normal, bar terakhir closed (now-4j)
    bars4 = []
    for i in range(60):
        c = 2000.0 + i * 0.5
        t = now - timedelta(hours=240) + timedelta(hours=i * 4)
        bars4.append({"t": iso(t), "o": c - 0.1, "h": c + 10, "l": c - 10, "c": c})
    store.save("4h", bars4)


def test_recommend_sl_floor():
    """Level SL rekomendasi LIVE harus pakai rumus yang sama dengan
    backtest (kolam statistik = aturan live): squeeze -> SL = lantai."""
    store.DATA_DIR = Path(tempfile.mkdtemp())
    now = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    _seed_squeeze(now)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "avg_r": 0.2},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    lv = rec["levels"]
    entry, sl = lv["entry"], lv["sl"]
    dist = entry - sl
    # ATR14 collapse ~ $2, median ATR ~ $20 -> lantai 0.75*20 = $15
    assert 10 < dist < 20, lv
    # LONG batch 10: TP = 2.25xATR (stop lebar), SL digenjangkan lantai
    assert abs((lv["tp1"] - entry) - 2.25 * lv["atr14"]) < 0.05, lv
    assert any("digenjangkan" in r for r in rec["rationale"]), rec["rationale"]
    assert rec["params"]["sl_floor_med"] == SL_FLOOR_MED_MULT, rec["params"]
    print(f"ok: rekomendasi live — SL digenjangkan ${dist:.2f} saat squeeze "
          f"(1.5xATR long cuma ~${1.5 * lv['atr14']:.2f}), rationale menyebut lantai")


def test_recommend_floor_inert_normal_atr():
    """ATR normal -> SL = 1xATR, tidak ada note lantai."""
    store.DATA_DIR = Path(tempfile.mkdtemp())
    now = datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(202):
        c = 2000.0 + i * 0.5
        t = now - timedelta(hours=202) + timedelta(hours=i)
        bars.append({"t": iso(t), "o": c - 0.1, "h": c + 10, "l": c - 10, "c": c})
    top = 2000.0 + 202 * 0.5
    bars.append({"t": iso(now - timedelta(hours=2)), "o": top + 0.2, "h": top + 10,
                 "l": top - 10, "c": top - 0.3})
    bars.append({"t": iso(now - timedelta(hours=1)), "o": top - 0.3, "h": top + 10.5,
                 "l": top - 10, "c": top + 2.0})
    # 2 bar terakhir range kecil tak cukup collapse median — floor tetap di
    # bawah 1xATR (median ~$20). Simpan juga H4 searah.
    store.save("1h", bars)
    bars4 = []
    for i in range(60):
        c = 2000.0 + i * 0.5
        t = now - timedelta(hours=240) + timedelta(hours=i * 4)
        bars4.append({"t": iso(t), "o": c - 0.1, "h": c + 10, "l": c - 10, "c": c})
    store.save("4h", bars4)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "avg_r": 0.2},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    lv = rec["levels"]
    atr = lv["atr14"]
    # ATR belum collapse -> SL long ~ 1.5xATR (lantai 0.75x median di bawahnya)
    assert abs((lv["entry"] - lv["sl"]) - 1.5 * atr) < 3, lv
    assert not any("digenjangkan" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: ATR normal -> SL long 1.5xATR, lantai diam di rekomendasi live")


if __name__ == "__main__":
    test_backtest_floor_active()
    test_backtest_floor_inactive()
    test_recommend_sl_floor()
    test_recommend_floor_inert_normal_atr()
    print("\nSEMUA TES LANTAI SL LULUS")