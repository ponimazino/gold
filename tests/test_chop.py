"""Test guard regime chop (keputusan user 2026-10-06, opsi 1 'blokir'):

Sinyal SHORT diblok saat regime chop aktif: trend H4 as-of == -1 DAN close
H1 < EMA50 H4 as-of DAN ATR14 < median ATR14 100 bar sebelumnya (atr_med100).
Blok = netral + gate.chop_block + rationale; note slot rec_log mengalir
otomatis dari gate.reason.

HONESTY NOTE: guard ini PREFERENSI RISIKO user, bukan bukti statistik —
kolam 3 tahun in-regime OOS +0.160 (per-tahun campur, 2024 -0.534); 2 win
28 Sep 2026 (+3R) terjadi in-regime dan akan ikut terblok. Dinamis: semua
kondisi dievaluasi as-of per run — regime berakhir -> lepas otomatis.

Run: python tests/test_chop.py
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import recommend, store  # noqa: E402


def _isolate() -> None:
    store.DATA_DIR = Path(tempfile.mkdtemp())


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)


def _gate_inside_bar_short_positive() -> None:
    # EV short POSITIF + n cukup — kalau status netral, itu PASTI guard
    # (bukan gate kualitas yang blok)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "inside_bar", "n": 502, "win_rate": 0.42,
         "oos_win_rate": 0.44, "cost_avg_r": 0.05, "n_short": 502,
         "win_rate_short": 0.45, "cost_oos_avg_r_short": 0.113,
         "cost_avg_r_short": 0.09}]})


def _h4_down(base: datetime) -> list:
    return [{"t": iso(base + timedelta(hours=4 * i)),
             "o": 2400 - i * 5 + 2, "h": 2400 - i * 5 + 4,
             "l": 2400 - i * 5 - 4, "c": 2400 - i * 5}
            for i in range(80)]


# ------------------------------------------------ 1. unit _regime_chop_blocked
def test_chop_unit():
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    mk1 = lambda atr, med, c: pd.DataFrame(
        {"o": [10] * 3, "h": [11.0] * 3, "l": [9.0] * 3, "c": [c, c, c],
         "atr14": [atr] * 3, "atr_med100": [med] * 3}, index=idx.astype(str))
    mk4 = lambda tr, e50: pd.DataFrame(
        {"trend": [tr] * 3, "ema50": [e50] * 3},
        index=[(idx[0] - pd.Timedelta(hours=8 + 4 * i)).strftime("%Y-%m-%dT%H:00:00Z")
               for i in range(3)][::-1])
    # tiga kondisi terpenuhi: H4 down, close < EMA50 H4, ATR < med100
    assert recommend._regime_chop_blocked(mk1(10.0, 14.0, 100.0), mk4(-1, 120.0), 2, -1) is True
    # ATR pulih >= median -> regime selesai -> tidak blok (dinamis)
    assert recommend._regime_chop_blocked(mk1(15.0, 14.0, 100.0), mk4(-1, 120.0), 2, -1) is False
    # harga close >= EMA50 H4 -> tidak blok
    assert recommend._regime_chop_blocked(mk1(10.0, 14.0, 120.0), mk4(-1, 120.0), 2, -1) is False
    # H4 bukan down -> tidak blok
    assert recommend._regime_chop_blocked(mk1(10.0, 14.0, 100.0), mk4(0, 120.0), 2, -1) is False
    assert recommend._regime_chop_blocked(mk1(10.0, 14.0, 100.0), mk4(1, 120.0), 2, -1) is False
    # LONG tidak diguard (anti-trend long sudah dibuang alignment sebelum guard)
    assert recommend._regime_chop_blocked(mk1(10.0, 14.0, 100.0), mk4(-1, 120.0), 2, 1) is False
    # fail-open: ATR/median NaN -> tidak blok (data terlalu awal)
    assert recommend._regime_chop_blocked(mk1(float("nan"), 14.0, 100.0), mk4(-1, 120.0), 2, -1) is False
    assert recommend._regime_chop_blocked(mk1(10.0, float("nan"), 100.0), mk4(-1, 120.0), 2, -1) is False
    print("ok: _regime_chop_blocked unit — blok hanya short, 3 kondisi, fail-open")


def _chop_fixture(base: datetime, tail_range: float, tail_n: int = 10) -> list:
    """83 bar downtrend range lebar (ATR tinggi), lalu tail_n bar range
    menyempit (ATR collapse di bawah median-100) + inside bar ketat di
    ujung (range masih >= 0.15xATR -> lolos guard squeeze)."""
    bars = []
    for i in range(84 - tail_n):
        c = 2200 - i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c + 2,
                     "h": c + 6, "l": c - 6, "c": c})
    c0 = bars[-1]["c"]
    for k in range(tail_n - 1):
        c = c0 - (k + 1) * 0.8
        bars.append({"t": iso(base + timedelta(hours=84 - tail_n + k)),
                     "o": c + tail_range, "h": c + tail_range,
                     "l": c - tail_range, "c": c})
    # inside bar KETAT vs bar sebelumnya (tail terakhir)
    p = bars[-1]
    bars.append({"t": iso(base + timedelta(hours=83)), "o": p["c"],
                 "h": p["h"] - 0.2, "l": p["l"] + 0.2, "c": p["c"]})
    return bars


# --------------------------- 2. short saat chop aktif -> disaring guard
def test_chop_guard_blocks_short_in_regime():
    _isolate()
    base = NOW - timedelta(hours=84)
    # tail sempit: ATR14 collapse < median-100, inside bar ketat tapi range
    # masih ~0.46xATR -> lolos guard squeeze, momentum masih turun (mom3<0)
    bars = _chop_fixture(base, tail_range=2.0)
    store.save("1h", bars)
    store.save("4h", _h4_down(base))
    _gate_inside_bar_short_positive()
    rec = recommend.build_recommendation(now=NOW)
    assert rec["status"] == "netral", rec
    gate = rec.get("gate") or {}
    assert gate.get("chop_block") is True, rec
    assert any("regime chop" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: short saat regime chop aktif (ATR < median-100) disaring guard")


# ------------------------ 3. ATR pulih (regime berakhir) -> tetap entry
def test_chop_guard_releases_when_volatility_returns():
    _isolate()
    base = NOW - timedelta(hours=84)
    # tail range LEBAR (8.0 x 20 bar): ATR14 pulih ke atas median-100 ->
    # regime chop tidak aktif, sinyal short sehat harus tetap jadi entry
    bars = _chop_fixture(base, tail_range=8.0, tail_n=20)
    store.save("1h", bars)
    store.save("4h", _h4_down(base))
    _gate_inside_bar_short_positive()
    rec = recommend.build_recommendation(now=NOW)
    assert rec["status"] == "entry", rec
    assert (rec.get("gate") or {}).get("chop_block") is None, rec
    print("ok: ATR pulih ke atas median-100 -> guard lepas, short tetap entry")


# --------------------------------- 4. harga balik atas EMA50 H4 -> lepas
def test_chop_guard_releases_when_price_above_ema50h4():
    _isolate()
    base = NOW - timedelta(hours=84)
    bars = _chop_fixture(base, tail_range=0.8)  # chop mungkin aktif di H1
    # tapi H4 naik tajam di ujung -> EMA50 H4 as-of di bawah close H1:
    # kondisi 2 regime (close >= EMA50 H4) pecah -> guard tidak boleh blok
    h4 = _h4_down(base)
    c0 = h4[-1]["c"]
    for k in range(1, 12):
        c = c0 + k * 25  # rally H4 agresif menjatuhkan EMA50 as-of
        h4.append({"t": iso(base + timedelta(hours=4 * (80 + k - 1) + 1)),
                   "o": c - 24, "h": c + 26, "l": c - 26, "c": c})
    store.save("1h", bars)
    store.save("4h", h4)
    _gate_inside_bar_short_positive()
    rec = recommend.build_recommendation(now=NOW)
    gate = rec.get("gate") or {}
    assert gate.get("chop_block") is None, rec  # tidak boleh chop-blok
    # (boleh netral karena pola anti-trend — H4 sudah up; intinya bukan chop)
    print("ok: harga di atas EMA50 H4 as-of -> guard chop lepas")


if __name__ == "__main__":
    test_chop_unit()
    test_chop_guard_blocks_short_in_regime()
    test_chop_guard_releases_when_volatility_returns()
    test_chop_guard_releases_when_price_above_ema50h4()
    print("test_chop: SEMUA LULUS")