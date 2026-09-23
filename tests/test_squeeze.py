"""Test guard squeeze (audit 2026-09-24, batch 8):

Sinyal SHORT dengan bar sinyal terlalu sempit (range < 0.15 x ATR14 = pasar
terjepit) disaring — subset terburuk data 3 tahun (EV full -0.135R / OOS
-0.688R) dan 3 dari 3 loss live terakhir (15/16/22 Sep 2026) kena profil
ini. LONG sengaja TIDAK diguard (kompresi long sehat, asimetri seperti
guard momentum). Analisa tetap tampil: blok = netral + gate.squeeze_block
+ rationale, note slot rec_log mengalir otomatis dari gate.reason.

Run: python tests/test_squeeze.py
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


NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


def _gate_inside_bar_short_positive() -> None:
    # EV short POSITIF + n cukup — kalau status netral, itu PASTI guard
    # squeeze (bukan gate kualitas yang blok)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "inside_bar", "n": 481, "win_rate": 0.42,
         "oos_win_rate": 0.44, "cost_avg_r": 0.05, "n_short": 481,
         "win_rate_short": 0.45, "cost_oos_avg_r_short": 0.154,
         "cost_avg_r_short": 0.09}]})


def _h4_down(base: datetime) -> list:
    return [{"t": iso(base + timedelta(hours=4 * i)),
             "o": 2400 - i * 5 + 2, "h": 2400 - i * 5 + 4,
             "l": 2400 - i * 5 - 4, "c": 2400 - i * 5}
            for i in range(80)]


def _h4_up(base: datetime) -> list:
    return [{"t": iso(base + timedelta(hours=4 * i)),
             "o": 2000 + i * 2 - 1, "h": 2000 + i * 2 + 1,
             "l": 2000 + i * 2 - 2, "c": 2000 + i * 2}
            for i in range(80)]


# ---------------------------------------------------- 1. unit _squeeze_blocked
def test_squeeze_unit():
    assert recommend.SQUEEZE_RATIO_MAX == 0.15
    mk = lambda h, l, atr: pd.DataFrame(
        {"o": [10] * 3, "h": [10.5, 11, h], "l": [9.5, 9, l], "c": [10] * 3,
         "atr14": [atr] * 3})
    # range 0.3 / ATR 10 = 0.03 < 0.15 -> short diblok
    assert recommend._squeeze_blocked(mk(10.2, 9.9, 10.0), 2, -1) is True
    # range 2.0 / ATR 10 = 0.2 >= 0.15 -> lolos
    assert recommend._squeeze_blocked(mk(11.0, 9.0, 10.0), 2, -1) is False
    # LONG bebas walau bar sempit (asimetri, sama seperti guard momentum)
    assert recommend._squeeze_blocked(mk(10.2, 9.9, 10.0), 2, 1) is False
    # ATR NaN / 0 -> tidak blok (fail-open, data terlalu awal)
    assert recommend._squeeze_blocked(mk(10.2, 9.9, float("nan")), 2, -1) is False
    assert recommend._squeeze_blocked(mk(10.2, 9.9, 0.0), 2, -1) is False
    print("ok: _squeeze_blocked unit — blok hanya short range < 0.15xATR")


# ------------------------------- 2. short kompresi ekstrem -> disaring guard
def test_squeeze_guard_blocks_compressed_short():
    _isolate()
    base = NOW - timedelta(hours=84)
    # 83 bar downtrend (momentum TURUN — guard momentum tidak ikut memblok),
    # lalu inside bar super-sempit (range 0.8 vs ATR ~8 = 0.1xATR)
    bars = []
    for i in range(83):
        c = 2200 - i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c + 2,
                     "h": c + 3, "l": c - 3, "c": c})
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 1790.0,
                 "h": 1790.4, "l": 1789.6, "c": 1790.0})
    store.save("1h", bars)
    store.save("4h", _h4_down(base))
    _gate_inside_bar_short_positive()
    rec = recommend.build_recommendation(now=NOW)
    assert rec["status"] == "netral", rec
    gate = rec.get("gate") or {}
    assert gate.get("squeeze_block") is True, rec
    assert any("squeeze" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: short saat bar sinyal terjepit (0.1xATR) disaring guard squeeze")


# ------------------------- 3. short bar normal -> tetap entry (tak terblok)
def test_squeeze_guard_allows_normal_short():
    _isolate()
    base = NOW - timedelta(hours=84)
    bars = []
    for i in range(83):
        c = 2200 - i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c + 2,
                     "h": c + 3, "l": c - 3, "c": c})
    # inside bar range 3.0 (0.375xATR) — sehat, harus tetap jadi entry
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 1790.0,
                 "h": 1791.5, "l": 1788.5, "c": 1790.5})
    store.save("1h", bars)
    store.save("4h", _h4_down(base))
    _gate_inside_bar_short_positive()
    rec = recommend.build_recommendation(now=NOW)
    assert rec["status"] == "entry", rec
    assert (rec.get("gate") or {}).get("squeeze_block") is None, rec
    print("ok: short bar sinyal normal (0.375xATR) tetap entry")


# --------------------------- 4. long bebas walau kompresi (asimetri guard)
def test_squeeze_guard_skips_long():
    _isolate()
    base = NOW - timedelta(hours=84)
    bars = []
    for i in range(83):
        c = 1800 + i * 4
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c - 2,
                     "h": c + 3, "l": c - 3, "c": c})
    # inside bar super-sempit di puncak uptrend — long TIDAK diguard
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 2129.0,
                 "h": 2130.4, "l": 2129.6, "c": 2130.0})
    store.save("1h", bars)
    store.save("4h", _h4_up(base))
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "inside_bar", "n": 807, "win_rate": 0.45,
         "oos_win_rate": 0.47, "cost_avg_r": 0.10, "n_long": 807,
         "win_rate_long": 0.46, "cost_oos_avg_r_long": 0.192,
         "cost_avg_r_long": 0.12}]})
    rec = recommend.build_recommendation(now=NOW)
    assert rec["status"] == "entry", rec
    assert (rec.get("gate") or {}).get("squeeze_block") is None, rec
    print("ok: long saat kompresi TIDAK kena guard squeeze (khusus short)")


if __name__ == "__main__":
    test_squeeze_unit()
    test_squeeze_guard_blocks_compressed_short()
    test_squeeze_guard_allows_normal_short()
    test_squeeze_guard_skips_long()
    print("test_squeeze: SEMUA LULUS")