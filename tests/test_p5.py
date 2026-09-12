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
from analyzer.jobs import research  # noqa: E402


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
    """Isi data dir dengan H1/H4 uptrend berakhir engulfing. Return waktu 'now'.
    Bar terakhir sengaja CLOSED (H1 = now-1j, H4 = now-4j) — recommend
    membuang bar yang belum selesai, jadi seeding bar masa depan akan
    membuat sinyalnya terlihat 'hilang'."""
    base_h1 = now - timedelta(hours=202)
    store.save("1h", uptrend_bars(base_h1, 200, 1, 2000.0))
    base_h4 = now - timedelta(hours=808)
    store.save("4h", uptrend_bars(base_h4, 200, 4, 2000.0))
    return now


def test_entry_signal():
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)

    # gate kualitas fail-closed: pola searah trend TAPI tanpa statistik ->
    # tidak entry (tidak ada bukti, tidak ada trade)
    rec0 = recommend.build_recommendation(now=now)
    assert rec0["status"] == "netral", rec0
    assert rec0["gate"]["passed"] is False, rec0
    assert any("gate kualitas" in r for r in rec0["rationale"]), rec0["rationale"]
    print("ok: gate fail-closed — pola tanpa statistik tidak entry")

    # patterns.json -> lolos gate -> confidence terisi dari win-rate OOS
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "avg_r": 0.2},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    assert rec["bias"] == "bullish", rec
    assert rec["pattern"] == "bullish_engulfing", rec
    assert rec["h4_context"]["trend"] == "up", rec
    assert rec["gate"]["passed"] is True, rec
    lv = rec["levels"]
    assert lv["sl"] < lv["entry"] < lv["tp1"] < lv["tp2"], lv
    # TP1 = 1.5x (entry - SL), TP2 = 3x (ATR rounding: toleransi 0.05)
    assert abs((lv["tp1"] - lv["entry"]) - 1.5 * (lv["entry"] - lv["sl"])) < 0.05, lv
    assert abs((lv["tp2"] - lv["entry"]) - 3.0 * (lv["entry"] - lv["sl"])) < 0.05, lv
    assert rec["blackout"] is None
    assert any("engulfing" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: rekomendasi entry (pola searah trend, level ATR benar)")

    rec2 = recommend.build_recommendation(now=now)
    assert rec2["confidence"] == 0.58, rec2
    assert "OOS" in rec2["confidence_note"], rec2["confidence_note"]
    print("ok: confidence dari statistik pola (OOS diprioritaskan)")


def test_gate_quality_filters_bad_patterns():
    """Gate EV net: pola terbukti rugi (EV <= 0) atau n terlalu kecil
    tidak boleh jadi rekomendasi entry meski searah trend H4."""
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)

    # EV net negatif (angka nyata bearish/bullish engulfing produksi)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 380,
         "win_rate": 0.402, "cost_avg_r": -0.021},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "netral", rec
    assert rec["levels"] is None
    assert rec["gate"]["passed"] is False, rec
    assert "EV net" in rec["gate"]["reason"], rec["gate"]
    assert any("disaring gate kualitas" in r for r in rec["rationale"])
    # rec_log: slot non-entry bawa catatan gate (bukan terlihat kosong)
    from analyzer import reclog
    log = reclog.log_run(rec, now=now)
    slot = log["days"][0]["slots"][-1]
    assert slot["status"] == "netral" and "EV net" in slot["note"], slot
    print("ok: gate menyaring pola EV net negatif + catatan slot rec_log")

    # n di bawah ambang: bukti belum cukup, juga tersaring
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 4, "cost_avg_r": 0.5},
    ]})
    rec2 = recommend.build_recommendation(now=now)
    assert rec2["status"] == "netral", rec2
    assert "n=4" in rec2["gate"]["reason"], rec2["gate"]
    print("ok: gate menyaring pola dengan n < ambang bukti")


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
    store.save("4h", uptrend_bars(now - timedelta(hours=808), 200, 4, 2000.0))
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


def test_outcome_narrative_and_eod():
    """Saat resolve, rec dapat metrik perjalanan + narasi mengapa; history
    teragregasi jadi log akhir hari per tanggal WIB."""
    _isolate_data_dir()
    t0 = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    # win: TP di bar ke-2, sempat tertekan $1 (dari jarak SL $5 — tidak signifikan)
    win = _active_rec("2026-09-04", t0, 1)
    # loss: SL di bar ke-1, ADA event 3★ di jendela posisi
    loss = _active_rec("2026-09-05", t0 + timedelta(hours=10), 1)
    loss["pattern"] = "bullish_engulfing"

    bars = (
        _bars_from(t0, [(1, 103, 99), (2, 106, 100)])          # blok win
        + _bars_from(t0 + timedelta(hours=10), [(1, 102, 94)])  # blok loss
    )
    store.save("1h", bars)
    store.write_json("calendar.json", {"events": [
        {"title": "Nonfarm Payrolls", "t_utc": iso(t0 + timedelta(hours=10, minutes=30)),
         "importance": 3},
        # di luar jendela mana pun — tidak boleh ikut
        {"title": "CPI y/y", "t_utc": iso(t0 + timedelta(hours=50)), "importance": 3},
    ]})

    tracking = {"stats": {}, "history": [win, loss]}
    track.resolve_pending(tracking, now=t0 + timedelta(hours=100))

    assert win["status"] == "win" and loss["status"] == "loss"
    o = win["outcome"]
    assert o["bars_held"] == 2 and o["mfe_usd"] == 6.0 and o["mae_usd"] == 1.0, o
    assert o["events"] == [] and "TP1 kena 2 jam" in o["why"], o
    assert "tanpa event 3★" in o["why"], o
    assert o["resolved_at_wib"].endswith("WIB"), o

    o2 = loss["outcome"]
    assert o2["bars_held"] == 1 and o2["mfe_usd"] == 2.0 and o2["mae_usd"] == 6.0, o2
    assert o2["events"] == ["Nonfarm Payrolls"], o2
    assert "Salah" in o2["why"] and "Nonfarm Payrolls" in o2["why"], o2
    assert "CPI" not in o2["why"], o2

    # log akhir hari: per tanggal WIB, urut terbaru dulu — kini lengkap
    # sampai hari ini (now = t0+100j → 2026-09-08 WIB), hari tanpa entry
    # ditandai "skip"
    eod = tracking["eod"]
    assert [d["date"] for d in eod] == [
        "2026-09-08", "2026-09-07", "2026-09-06", "2026-09-05", "2026-09-04",
    ], eod
    assert all(d["status"] == "skip" for d in eod[:3]), eod  # hari kosong
    assert eod[3]["entries"][0]["status"] == "loss"
    assert eod[3]["entries"][0]["why"] == o2["why"]
    assert eod[4]["entries"][0]["pattern"] is None  # rec tanpa pattern tetap aman
    print("ok: narasi outcome (mfe/mae/event/mengapa) + log EOD per tanggal WIB")


def test_safe_mode_levels_and_stats():
    """Mode aman: level TP 1xATR / SL 0.75xATR + win-rate dari evaluasi
    rule-nya sendiri (bukan dikali-kali dari rule standar)."""
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "avg_r": 0.2,
         "safe_win_rate": 0.72, "safe_oos_win_rate": 0.70},
    ]})

    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    lv, ls = rec["levels"], rec["levels_safe"]
    assert ls["entry"] == lv["entry"], (lv, ls)
    atr = lv["atr14"]
    assert abs((ls["tp1"] - ls["entry"]) - 1.0 * atr) < 0.05, ls
    assert abs((ls["entry"] - ls["sl"]) - 0.75 * atr) < 0.05, ls
    assert rec["confidence"] == 0.58 and rec["confidence_safe"] == 0.70, rec
    assert any("mode aman" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: level mode aman (TP 1xATR / SL 0.75xATR) + win-rate terpisah")


def test_research_payload_safe_stats():
    """build_payload menggabungkan statistik rule aman per pola, dan tetap
    kompatibel kalau evaluasi aman tidak diberikan."""
    _isolate_data_dir()
    sig = {"dir": 1, "index": 10, "entry": 100.0}
    std = [
        {**sig, "tf": "1h", "pattern": "bullish_engulfing", "outcome": "win", "r": 1.5, "bars": 3},
        {**sig, "tf": "1h", "pattern": "bullish_engulfing", "index": 40, "entry": 101.0,
         "outcome": "loss", "r": -1.0, "bars": 2},
        {**sig, "tf": "1h", "pattern": "bearish_engulfing", "dir": -1, "outcome": "win",
         "r": 1.5, "bars": 4},
    ]
    safe = [
        {**sig, "tf": "1h", "pattern": "bullish_engulfing", "outcome": "win", "r": 1.33, "bars": 2},
        {**sig, "tf": "1h", "pattern": "bullish_engulfing", "index": 40, "entry": 101.0,
         "outcome": "win", "r": 1.33, "bars": 3},
        {**sig, "tf": "1h", "pattern": "bearish_engulfing", "dir": -1, "outcome": "win",
         "r": 1.33, "bars": 3},
    ]
    payload = research.build_payload(std, {"1h": 24}, safe_results=safe)
    assert payload["params"]["safe_tp_atr"] == 1.0, payload["params"]
    assert payload["params"]["safe_sl_atr"] == 0.75, payload["params"]
    row = next(r for r in payload["results"] if r["pattern"] == "bullish_engulfing")
    assert row["win_rate"] == 0.5, row
    assert row["safe_win_rate"] == 1.0, row          # rule aman = 2/2 win
    assert row["safe_oos_n"] is not None, row
    assert "safe_win_rate" in payload["note"]
    # tanpa safe_results -> field aman tidak muncul (backward compat)
    p2 = research.build_payload(std, {"1h": 24})
    assert all("safe_win_rate" not in r for r in p2["results"])
    print("ok: payload research menggabungkan statistik mode aman per pola")


def test_record_entry_one_active_position():
    """Aturan 2026-09-10: entry baru hanya dicatat kalau posisi terakhir
    sudah selesai — boleh re-entry sehari yang sama, yang dilarang hanya
    posisi overlap."""
    from analyzer.jobs.daily import record_entry

    def _entry(rid):
        return {"id": rid, "status": "entry", "rationale": [],
                "levels": {"entry": 100.0, "sl": 95.0, "tp1": 110.0, "tp2": 130.0}}

    # status bukan entry -> tidak dicatat
    t = {"history": []}
    assert record_entry({"status": "netral", "rationale": []}, t) is False
    assert t["history"] == []

    # entry + history kosong -> dicatat
    r1 = _entry("2026-09-10")
    assert record_entry(r1, t) is True
    assert t["history"] == [r1]

    # entry baru saat r1 masih 'entry'/'active' -> TIDAK dicatat + catatan
    # rationale menjelaskan kenapa (tampil di UI)
    r2 = _entry("2026-09-10")
    assert record_entry(r2, t) is False
    assert t["history"] == [r1]
    assert "masih berjalan" in r2["rationale"][-1], r2["rationale"]
    r1["status"] = "active"
    assert record_entry(_entry("2026-09-10"), t) is False

    # posisi selesai (win) -> re-entry SEHARI YANG SAMA boleh dicatat
    r1["status"] = "win"
    r3 = _entry("2026-09-10")
    assert record_entry(r3, t) is True
    assert t["history"] == [r1, r3]
    print("ok: record_entry — 1 posisi aktif, re-entry sehari sama setelah selesai")


def test_partial_bar_dropped():
    """Audit 2026-09-12: sync menyimpan candle yang sedang berjalan —
    analisa TIDAK boleh memakainya (pola bisa batal di menit akhir,
    level entry-nya cuma snapshot live). Sinyal + level harus murni dari
    candle yang sudah close, konsisten dengan backtest."""
    _isolate_data_dir()
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    _seed(now)
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 92,
         "win_rate": 0.61, "oos_win_rate": 0.58, "cost_avg_r": 0.05},
    ]})
    ref = recommend.build_recommendation(now=now)
    assert ref["status"] == "entry", ref
    entry_ref = ref["levels"]["entry"]

    # bar PARTIMAL: t = now (belum genap 1 jam) — harga melompat jauh.
    # Tanpa fix, level entry tergeser ke snapshot harga live bar ini.
    bars = store.load("1h")
    bars.append({"t": iso(now), "o": entry_ref + 5.0, "h": entry_ref + 9.0,
                 "l": entry_ref + 4.0, "c": entry_ref + 8.0})
    store.save("1h", bars)
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    assert abs(rec["levels"]["entry"] - entry_ref) < 0.01, (rec["levels"], entry_ref)
    print("ok: bar partial diabaikan — level dari candle yang sudah close")

    # H4 partial juga dibuang: bar 20:00Z saat run 21:20Z (belum genap 4 jam)
    h4 = store.load("4h")
    last4 = h4[-1]
    h4.append({"t": iso(now - timedelta(hours=3)), "o": last4["c"] + 5.0,
               "h": last4["c"] + 9.0, "l": last4["c"] + 4.0, "c": last4["c"] + 8.0})
    store.save("4h", h4)
    rec4 = recommend.build_recommendation(now=now)
    # trend H4 tetap dibaca dari bar closed — level entry tidak berubah
    assert abs(rec4["levels"]["entry"] - entry_ref) < 0.01, rec4["levels"]
    print("ok: bar H4 partial juga diabaikan")


def test_stats_counts_entry_as_active():
    """Audit 2026-09-12: posisi baru dicatat berstatus 'entry' (belum
    dinormalisasi 'active') — compute_stats tetap harus menghitungnya
    sebagai posisi berjalan, jangan sampai tile UI 'Berjalan' tampil 0."""
    tracking = {"history": [
        {"status": "entry"},   # baru dicatat job daily
        {"status": "active"},  # sudah dinormalisasi
        {"status": "win"},
        {"status": "loss"},
    ]}
    track.compute_stats(tracking)
    s = tracking["stats"]
    assert s["active"] == 2, s
    assert s["wins"] == 1 and s["losses"] == 1 and s["total"] == 4, s
    assert s["hit_rate"] == 0.5, s
    print("ok: compute_stats menghitung status entry + active sebagai berjalan")


def main() -> int:
    test_entry_signal()
    test_partial_bar_dropped()
    test_blackout_blocks_entry()
    test_neutral_when_no_signal()
    test_feedback_loop()
    test_feedback_loop_resolves_entry_status()
    test_stats_counts_entry_as_active()
    test_tracking_persistence()
    test_outcome_narrative_and_eod()
    test_safe_mode_levels_and_stats()
    test_research_payload_safe_stats()
    test_record_entry_one_active_position()
    print("\nALL P5 TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())