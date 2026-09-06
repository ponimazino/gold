"""Test P5: mesin rekomendasi + feedback loop (tanpa API key).

Run: python tests/test_p5.py
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import recommend, store, track  # noqa: E402


def _isolate_data_dir() -> None:
    store.DATA_DIR = Path(tempfile.mkdtemp())


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def uptrend_bars(base: datetime, n: int, step_h: int, close0: float) -> list[dict]:
    """n bar naik konsisten + 2 bar akhir: red kecil lalu engulfing green."""
    bars = []
    for i in range(n):
        c = close0 + i * 0.5
        t = base + timedelta(hours=i * step_h)
        bars.append({"t": iso(t), "o": c - 0.1, "h": c + 0.4, "l": c - 0.5, "c": c})
    # bar n: red (close di bawah open & body sebelumnya)
    t_red = base + timedelta(hours=n * step_h)
    top = close0 + n * 0.5
    bars.append({"t": iso(t_red), "o": top + 0.2, "h": top + 0.4,
                 "l": top - 0.5, "c": top - 0.3})
    # bar n+1: engulfing green menelan bar red
    t_eng = base + timedelta(hours=(n + 1) * step_h)
    bars.append({"t": iso(t_eng), "o": top - 0.3, "h": top + 2.4,
                 "l": top - 0.5, "c": top + 2.0})
    return bars


def _seed(now: datetime) -> datetime:
    """Isi data dir dengan H1/H4 uptrend berakhir engulfing. Return waktu 'now'."""
    base_h1 = now - timedelta(hours=200)
    store.save("1h", uptrend_bars(base_h1, 200, 1, 2000.0))
    base_h4 = now - timedelta(hours=800)
    store.save("4h", uptrend_bars(base_h4, 200, 4, 2000.0))
    return now


def test_entry_signal():
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)

    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    assert rec["bias"] == "bullish", rec
    assert rec["pattern"] == "bullish_engulfing", rec
    assert rec["h4_context"]["trend"] == "up", rec
    lv = rec["levels"]
    assert lv["sl"] < lv["entry"] < lv["tp1"] < lv["tp2"], lv
    # TP1 = 1.5x (entry - SL), TP2 = 3x (ATR rounding: toleransi 0.05)
    assert abs((lv["tp1"] - lv["entry"]) - 1.5 * (lv["entry"] - lv["sl"])) < 0.05, lv
    assert abs((lv["tp2"] - lv["entry"]) - 3.0 * (lv["entry"] - lv["sl"])) < 0.05, lv
    assert rec["blackout"] is None
    assert any("engulfing" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: rekomendasi entry (pola searah trend, level ATR benar)")

    # patterns.json -> confidence terisi dari win-rate OOS
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "avg_r": 0.2},
    ]})
    rec2 = recommend.build_recommendation(now=now)
    assert rec2["confidence"] == 0.58, rec2
    assert "OOS" in rec2["confidence_note"], rec2["confidence_note"]
    print("ok: confidence dari statistik pola (OOS diprioritaskan)")


def test_blackout_blocks_entry():
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)
    store.write_json("calendar.json", {
        "source": "tradingeconomics",
        "events": [{"title": "Nonfarm Payrolls", "t_utc": iso(now + timedelta(minutes=90))}],
    })
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "tunggu", rec
    assert rec["bias"] == "up", rec            # konteks trend tetap diberikan
    assert rec["levels"] is None               # tapi tidak ada level entry
    assert rec["blackout"]["title"] == "Nonfarm Payrolls"
    assert any("JEDA ENTRY" in r for r in rec["rationale"])
    print("ok: event bintang-3 dalam jendela -> status tunggu, tanpa level")


def test_neutral_when_no_signal():
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    # uptrend polos tanpa pola di bar terakhir
    base = now - timedelta(hours=200)
    bars = [{"t": iso(base + timedelta(hours=i)), "o": 2000 + i * 0.1,
             "h": 2000 + i * 0.1 + 0.4, "l": 2000 + i * 0.1 - 0.5,
             "c": 2000 + i * 0.1 + 0.05} for i in range(200)]
    store.save("1h", bars)
    store.save("4h", uptrend_bars(now - timedelta(hours=800), 200, 4, 2000.0))
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "netral", rec
    assert rec["levels"] is None
    assert any("tidak ada pola" in r for r in rec["rationale"])
    print("ok: tanpa pola terbaru -> netral dengan alasan eksplisit")


def _active_rec(rec_id: str, created: datetime, direction: int) -> dict:
    """Levels konsisten dengan arah: long SL di bawah / TP di atas; short kebalikannya."""
    if direction == 1:
        levels = {"entry": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0}
    else:
        levels = {"entry": 100.0, "sl": 105.0, "tp1": 95.0, "tp2": 90.0}
    return {
        "id": rec_id, "created_at": iso(created), "status": "active",
        "bias": "bullish" if direction == 1 else "bearish", "direction": direction,
        "levels": levels,
    }


def _bars_from(base: datetime, rows: list[tuple]) -> list[dict]:
    """rows: (offset_h, h, l) relatif terhadap base."""
    return [{"t": iso(base + timedelta(hours=o)), "o": 100, "h": h, "l": lo, "c": 100}
            for (o, h, lo) in rows]


def test_feedback_loop():
    _isolate_data_dir()
    t0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    # Tiap rec di-resolve terhadap timeline H1 PENUH setelah created_at-nya,
    # jadi tiap case diberi blok waktunya sendiri. Bar case yang lebih awal
    # tidak boleh menghalangi case yang lebih lambat: bar "berbahaya" (menyentuh
    # SL/TP) hanya muncul sebelum rec berikutnya dibuat.
    cases = [
        # long win: TP tersentuh di bar ke-2
        ("r-win", 1, t0, [(1, 103, 99), (2, 106, 100)], "win"),
        # long loss: SL duluan
        ("r-loss", 1, t0 + timedelta(hours=10), [(1, 102, 94)], "loss"),
        # konservatif: TP & SL di bar sama -> loss
        ("r-both", 1, t0 + timedelta(hours=20), [(1, 106, 94)], "loss"),
        # timeout: 24 bar tanpa sentuh apa pun
        ("r-timeout", 1, t0 + timedelta(hours=30),
         [(i, 101, 99) for i in range(1, 25)], "timeout"),
        # short win: harga turun ke TP (bukan SL)
        ("r-short", -1, t0 + timedelta(hours=70),
         [(1, 104, 96), (2, 103, 94)], "win"),
        # masih berjalan: rec paling akhir, hanya 3 bar setelahnya
        ("r-active", 1, t0 + timedelta(hours=100),
         [(1, 101, 99), (2, 101, 99), (3, 101, 99)], "active"),
    ]

    recs = [_active_rec(cid, created, d) for cid, d, created, _, _ in cases]
    all_bars = []
    for (_, _, created, rows, _) in cases:
        all_bars.extend(_bars_from(created, rows))
    store.save("1h", all_bars)

    tracking = {"stats": {}, "history": recs}
    track.resolve_pending(tracking, now=t0 + timedelta(hours=600))

    for rec, (_, _, _, _, expected) in zip(recs, cases):
        assert rec["status"] == expected, (rec["id"], rec["status"], expected)

    track.compute_stats(tracking)
    s = tracking["stats"]
    assert s["total"] == 6 and s["wins"] == 2 and s["losses"] == 2
    assert s["timeouts"] == 1 and s["active"] == 1 and s["resolved"] == 5
    assert s["hit_rate"] == 0.4, s
    print("ok: feedback loop (win/loss konservatif/timeout/active + hit-rate)")


def test_feedback_loop_resolves_entry_status():
    """Regression: job harian mencatat status 'entry', bukan 'active'.
    _resolve_one harus menormalisasi supaya rekomendasi tetap dinilai."""
    _isolate_data_dir()
    t0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    rec = _active_rec("r-entry", t0, 1)
    rec["status"] = "entry"  # seperti yang ditulis job daily.py
    bars = _bars_from(t0, [(1, 103, 99), (2, 106, 100)])  # TP tersentuh -> win
    store.save("1h", bars)
    tracking = {"stats": {}, "history": [rec]}
    track.resolve_pending(tracking, now=t0 + timedelta(hours=24))
    assert rec["status"] == "win", rec
    track.compute_stats(tracking)
    assert tracking["stats"]["wins"] == 1
    print("ok: rekomendasi berstatus 'entry' dinormalisasi lalu dinilai (regression)")


def test_tracking_persistence():
    _isolate_data_dir()
    p = store.DATA_DIR / "tracking.json"
    assert track.load_tracking() == {"updated_at": None, "stats": {}, "history": []}
    store.write_json("tracking.json", {"updated_at": "x", "stats": {"total": 1},
                                       "history": [{"id": "a"}]})
    t = track.load_tracking()
    assert t["history"][0]["id"] == "a" and t["stats"]["total"] == 1
    print("ok: tracking.json load/save round-trip")


def main() -> int:
    test_entry_signal()
    test_blackout_blocks_entry()
    test_neutral_when_no_signal()
    test_feedback_loop()
    test_feedback_loop_resolves_entry_status()
    test_tracking_persistence()
    print("\nALL P5 TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())