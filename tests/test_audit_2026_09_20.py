"""Test audit 2026-09-20 (batch enhance #1-#4):

1. Filter bar datar (pasar tutup) di store load/save + gap weekend normal
2. Guard data basi (stale) di recommend
3. Guard momentum-lawan khusus SHORT di recommend
4. Cooldown re-entry searah 4 jam (konsisten dedup research) di daily
5. Window resolusi feedback loop mulai dari bar SETELAH bar sinyal
6. rec_log: flag 'recorded' (posisi vs rekomendasi saja) + dedup slot
   duplikat dalam 1 jam

Run: python tests/test_audit_2026_09_20.py
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import reclog, recommend, store, track  # noqa: E402
from analyzer.jobs import daily  # noqa: E402


def _isolate() -> None:
    store.DATA_DIR = Path(tempfile.mkdtemp())


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 1. flat bar
def test_flat_bar_filter():
    _isolate()
    real = {"t": "2026-09-04T19:00:00Z", "o": 4424.0, "h": 4438.5,
            "l": 4423.2, "c": 4435.0}
    pin = {"t": "2026-09-06T06:00:00Z", "o": 4428.95, "h": 4429.15,
           "l": 4428.88, "c": 4428.92}  # range 0.27 = feed pasar tutup
    store.save("1h", [real, pin])
    loaded = store.load("1h")
    assert len(loaded) == 1 and loaded[0]["t"] == real["t"], loaded
    # file di disk juga terpurge (merge lewat load yang sudah difilter)
    raw = json.loads((store.DATA_DIR / "xauusd_1h.json").read_text("utf-8"))
    assert len(raw["bars"]) == 1, raw["bars"]
    print("ok: bar datar dibuang di save + load + purge file")


def test_gap_report_weekend_bridge():
    _isolate()
    fri = {"t": "2026-09-04T20:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1}
    sun = {"t": "2026-09-06T22:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1}
    mon = {"t": "2026-09-07T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1}
    tue = {"t": "2026-09-08T13:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1}
    mon2 = {"t": "2026-09-07T06:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1}
    assert store.gap_report([fri, sun], "1h") == []      # Jumat -> Minggu buka
    assert store.gap_report([sun, mon], "1h") == []      # Minggu -> Senin
    assert store.gap_report([mon, mon2], "1h") == []     # bar per jam normal
    # gap dalam minggu tetap terdeteksi (collector gagal) — Senin 06->11
    holes = [mon, mon2,
             {"t": "2026-09-07T11:00:00Z", "o": 1, "h": 2, "l": .5, "c": 1}]
    assert store.gap_report(holes, "1h"), "gap mencurigakan harus terdeteksi"
    print("ok: gap weekend (tanpa bar datar) normal, gap minggu tetap terdeteksi")


# -------------------------------------------------- 2. stale guard (recommend)
def _seed_bars(bars1: list, bars4: list) -> None:
    store.save("1h", bars1)
    store.save("4h", bars4)


def _h4_up(base: datetime, n: int = 80) -> list:
    return [{"t": iso(base + timedelta(hours=4 * i)),
             "o": 2000 + i * 2 - 1, "h": 2000 + i * 2 + 1,
             "l": 2000 + i * 2 - 2, "c": 2000 + i * 2} for i in range(n)]


def test_stale_guard():
    _isolate()
    now = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)
    # bar terakhir 5 jam lalu (sinkronisasi gagal / pasar tutup lama)
    base = now - timedelta(hours=62)
    bars1 = [{"t": iso(base + timedelta(hours=i)),
              "o": 2000 + i, "h": 2000 + i + 1, "l": 2000 + i - 1,
              "c": 2000 + i} for i in range(58)]
    _seed_bars(bars1, _h4_up(now - timedelta(hours=400)))
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "netral", rec
    assert rec.get("stale") is True, rec
    assert any("belum segar" in r for r in rec["rationale"]), rec["rationale"]
    # bar segar (1 jam lalu) TIDAK kena guard
    fresh = [{"t": iso(now - timedelta(hours=i)),
              "o": 2000 + i, "h": 2000 + i + 1, "l": 2000 + i - 1,
              "c": 2000 + i} for i in range(60, 0, -1)]
    _seed_bars(fresh, _h4_up(now - timedelta(hours=400)))
    rec2 = recommend.build_recommendation(now=now)
    assert rec2.get("stale") is None, rec2
    print("ok: data basi > 3 jam -> netral jujur; data segar tetap dianalisa")


# ------------------------------------- 3. guard momentum-lawan khusus short
def _h1_short_bounce(base: datetime) -> list:
    """Downtrend panjang, lalu 3 bar NAIK (bounce), diakhiri engulfing red.
    Sinyal short muncul (H1 trend masih turun) TAPI momentum 3-bar naik —
    harus disaring guard momentum-lawan."""
    bars = []
    for i in range(80):
        c = 2200 - i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c + 2,
                     "h": c + 3, "l": c - 3, "c": c})
    # bounce: 3 bar hijau naik dari dasar 1800
    for j, c in enumerate((1806, 1812, 1818)):
        bars.append({"t": iso(base + timedelta(hours=80 + j)), "o": c - 3,
                     "h": c + 2, "l": c - 4, "c": c})
    # engulfing red: menelan body hijau terakhir, close 1810 (> 1806 3-bar lalu)
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 1820,
                 "h": 1822, "l": 1808, "c": 1810})
    return bars


def _patterns_short_experiment() -> None:
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bearish_engulfing", "n": 134,
         "win_rate": 0.33, "oos_win_rate": 0.32, "cost_avg_r": -0.10,
         "n_short": 134, "win_rate_short": 0.34,
         "cost_oos_avg_r_short": -0.146, "cost_avg_r_short": -0.187},
    ]})


def test_momentum_guard_blocks_short_bounce():
    _isolate()
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    base = now - timedelta(hours=84)
    _seed_bars(_h1_short_bounce(base),
               [{"t": iso(base + timedelta(hours=4 * i)),
                 "o": 2400 - i * 5 + 2, "h": 2400 - i * 5 + 4,
                 "l": 2400 - i * 5 - 4, "c": 2400 - i * 5}
                for i in range(80)])
    _patterns_short_experiment()
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "netral", rec
    gate = rec.get("gate") or {}
    assert gate.get("momentum_block") is True, rec
    assert any("momentum-lawan" in r for r in rec["rationale"]), rec["rationale"]
    print("ok: short saat bounce H1 disaring guard momentum-lawan")


def test_momentum_guard_allows_short_continuation():
    _isolate()
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    base = now - timedelta(hours=85)
    bars = []
    for i in range(83):
        c = 2200 - i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c + 2,
                     "h": c + 3, "l": c - 3, "c": c})
    # green kecil lalu engulfing red — momentum 3-bar tetap TURUN
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 1790,
                 "h": 1793, "l": 1786, "c": 1791})
    bars.append({"t": iso(base + timedelta(hours=84)), "o": 1792,
                 "h": 1794, "l": 1770, "c": 1772})
    _seed_bars(bars, [{"t": iso(base + timedelta(hours=4 * i)),
                       "o": 2400 - i * 5 + 2, "h": 2400 - i * 5 + 4,
                       "l": 2400 - i * 5 - 4, "c": 2400 - i * 5}
                      for i in range(80)])
    # short EV POSITIF (bukan eksperimen — eksperimen dihentikan 2026-09-20):
    # momentum-searah harus lolos guard dan jadi entry qualified
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bearish_engulfing", "n": 134,
         "win_rate": 0.33, "oos_win_rate": 0.32, "cost_avg_r": -0.10,
         "n_short": 134, "win_rate_short": 0.52,
         "cost_oos_avg_r_short": 0.08, "cost_avg_r_short": 0.11},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    assert rec.get("experiment") is None, rec
    assert rec["levels"]["sl"] > rec["levels"]["entry"], rec["levels"]
    assert "signal_bar_t" in rec, rec
    print("ok: short momentum-searah + EV positif tetap jadi entry")


def test_momentum_guard_skips_long():
    _isolate()
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    base = now - timedelta(hours=84)
    bars = []
    for i in range(80):
        c = 2000 + i * 5
        bars.append({"t": iso(base + timedelta(hours=i)), "o": c - 2,
                     "h": c + 3, "l": c - 3, "c": c})
    # dip 3 bar turun lalu engulfing green: momentum LAWAN long — TIDAK
    # boleh disaring (data 3 tahun: beli dip justru subset terbaik)
    for j, c in enumerate((2394, 2388, 2382)):
        bars.append({"t": iso(base + timedelta(hours=80 + j)), "o": c + 2,
                     "h": c + 3, "l": c - 3, "c": c})
    bars.append({"t": iso(base + timedelta(hours=83)), "o": 2380,
                 "h": 2412, "l": 2378, "c": 2410})
    _seed_bars(bars, _h4_up(base))
    store.write_json("patterns.json", {"results": [
        {"tf": "1h", "pattern": "bullish_engulfing", "n": 287,
         "win_rate": 0.50, "oos_win_rate": 0.49, "cost_avg_r": 0.02,
         "n_long": 287, "win_rate_long": 0.51,
         "cost_oos_avg_r_long": 0.091, "cost_avg_r_long": 0.054},
    ]})
    rec = recommend.build_recommendation(now=now)
    assert rec["status"] == "entry", rec
    assert rec["bias"] == "bullish", rec
    print("ok: long beli dip TIDAK kena guard momentum (khusus short)")


# ---------------------------------------------------- 4. cooldown re-entry
def test_cooldown_blocks_and_updates():
    _isolate()
    now = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)
    rec = {"status": "entry", "direction": -1,
           "created_at": iso(now), "rationale": []}
    tracking = {"history": [], "last_signal": {"d": -1, "t": iso(now - timedelta(hours=1))}}
    assert daily.apply_cooldown(rec, tracking, now) is True
    assert rec["status"] == "tunggu", rec
    assert rec.get("cooldown") is True, rec
    # sinyal tertunda TIDAK memperbarui last_signal
    assert tracking["last_signal"]["t"] == iso(now - timedelta(hours=1))

    # > 2 jam (threshold baru 2026-09-21): lolos, last_signal diperbarui
    rec2 = {"status": "entry", "direction": -1,
            "created_at": iso(now), "rationale": []}
    tracking2 = {"history": [], "last_signal": {"d": -1, "t": iso(now - timedelta(hours=2, minutes=30))}}
    assert daily.apply_cooldown(rec2, tracking2, now) is False
    assert rec2["status"] == "entry", rec2

    # tepat di bawah 2 jam: masih diblok
    rec2b = {"status": "entry", "direction": -1,
             "created_at": iso(now), "rationale": []}
    tracking2b = {"history": [], "last_signal": {"d": -1, "t": iso(now - timedelta(hours=1, minutes=59))}}
    assert daily.apply_cooldown(rec2b, tracking2b, now) is True
    assert rec2b["status"] == "tunggu", rec2b

    # arah beda: tidak diblokir
    rec3 = {"status": "entry", "direction": 1,
            "created_at": iso(now), "rationale": []}
    tracking3 = {"history": [], "last_signal": {"d": -1, "t": iso(now - timedelta(minutes=30))}}
    assert daily.apply_cooldown(rec3, tracking3, now) is False

    # non-entry: tidak diproses
    assert daily.apply_cooldown({"status": "netral"}, tracking, now) is False
    print("ok: cooldown 2 jam searah — blok, update last_signal, arah bebas")


def test_cooldown_outcome_aware():
    """Batch 7 (2026-09-23): cooldown DILEWATI kalau posisi searah terakhir
    resolve TP1 (win); tetap diblok setelah loss/timeout/saat masih aktif."""
    _isolate()
    now = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)

    def mk(history):
        return {"history": history,
                "last_signal": {"d": -1, "t": iso(now - timedelta(hours=1))}}

    def rec():
        return {"status": "entry", "direction": -1,
                "created_at": iso(now), "rationale": []}

    win_hist = [{"direction": -1, "status": "win",
                 "created_at": iso(now - timedelta(hours=2))}]
    r = rec()
    assert daily.apply_cooldown(r, mk(win_hist), now) is False
    assert r["status"] == "entry", r
    assert any("DILEWATI" in x for x in r["rationale"]), r["rationale"]
    print("ok: cooldown dilewati — posisi searah terakhir TP1 (re-entry boleh)")

    for st in ("loss", "timeout", "active"):
        r = rec()
        hist = [{"direction": -1, "status": st,
                 "created_at": iso(now - timedelta(hours=2))}]
        assert daily.apply_cooldown(r, mk(hist), now) is True, st
        assert r["status"] == "tunggu", (st, r)
        assert r.get("cooldown") is True, (st, r)
    print("ok: cooldown tetap blok setelah SL / timeout / saat masih berjalan")

    # posisi WIN tapi arah beda -> bukan bukti, cooldown tetap berlaku
    r = rec()
    hist = [{"direction": 1, "status": "win",
             "created_at": iso(now - timedelta(hours=2))}]
    assert daily.apply_cooldown(r, mk(hist), now) is True
    # entry lama tanpa field direction (pra gate sadar-arah) -> konservatif blok
    r = rec()
    hist = [{"status": "win", "created_at": iso(now - timedelta(hours=2))}]
    assert daily.apply_cooldown(r, mk(hist), now) is True
    # posisi searah terakhir win TAPI ada yang lebih baru kena SL ->
    # yang dinilai = posisi TERBARU searah
    r = rec()
    hist = [{"direction": -1, "status": "win",
             "created_at": iso(now - timedelta(hours=5))},
            {"direction": -1, "status": "loss",
             "created_at": iso(now - timedelta(hours=2))}]
    assert daily.apply_cooldown(r, mk(hist), now) is True
    print("ok: bukti win harus dari posisi searah TERBARU (arah beda / SL lebih baru tetap blok)")


# ------------------------------------------- 5. window resolusi track.py
def test_resolve_starts_after_signal_bar():
    """SL kena di bar PERTAMA posisi (bar yang sedang berjalan saat run) —
    kode lama (filter created_at) melewatkannya; kode baru resolve di bar
    itu juga, persis backtest."""
    _isolate()
    bars = [
        {"t": "2026-09-16T22:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100},
        {"t": "2026-09-16T23:00:00Z", "o": 100, "h": 100.5, "l": 99.5, "c": 100},
        # bar pertama posisi: run terjadi 00:54Z, bar 00:00Z inilah yang
        # dulu terlewati (t 00:00 <= created_at 00:54)
        {"t": "2026-09-17T00:00:00Z", "o": 100, "h": 102.5, "l": 99.8, "c": 102},
        {"t": "2026-09-17T01:00:00Z", "o": 102, "h": 103, "l": 101, "c": 102.8},
    ]
    store.save("1h", bars)
    rec = {"id": "2026-09-17", "status": "active", "direction": -1,
           "created_at": "2026-09-17T00:54:22Z",
           "created_at_wib": "2026-09-17 07:54 WIB", "bias": "bearish",
           "levels": {"entry": 100.0, "sl": 101.0, "tp1": 95.0, "tp2": 90.0},
           "pattern": "inside_bar", "signal_bar_t": "2026-09-16T23:00:00Z"}
    tracking = {"history": [rec]}
    track.resolve_pending(tracking, now=datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc))
    assert rec["status"] == "loss", rec
    assert rec["resolved_at"] == "2026-09-17T00:00:00Z", rec["resolved_at"]
    assert rec["outcome"]["bars_held"] == 1, rec["outcome"]
    print("ok: resolusi mulai dari bar SETELAH bar sinyal (bar pertama posisi dicek)")


def test_timeout_needs_closed_horizon():
    _isolate()
    base = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
    mk = {"id": "t", "status": "active", "direction": -1,
          "created_at": iso(base), "created_at_wib": "x",
          "bias": "bearish",
          "levels": {"entry": 100.0, "sl": 103.0, "tp1": 97.0, "tp2": 95.0},
          "pattern": "p", "signal_bar_t": iso(base)}
    tracking = {"history": [dict(mk)]}
    # saat now = base+25h: file baru sampai bar t=base+24h (yang persis
    # selesai) -> 24 bar setelah bar sinyal, belum ada bar ke-25 = ke-24
    # dianggap masih berjalan -> BELUM timeout
    store.save("1h", [{"t": iso(base + timedelta(hours=i)), "o": 100,
                       "h": 100.4, "l": 99.6, "c": 100} for i in range(25)])
    track.resolve_pending(tracking, now=base + timedelta(hours=25))
    assert tracking["history"][0]["status"] == "active"
    # bar ke-25 muncul (t=base+25h) -> bar ke-24 pasti closed -> timeout
    store.save("1h", [{"t": iso(base + timedelta(hours=i)), "o": 100,
                       "h": 100.4, "l": 99.6, "c": 100} for i in range(26)])
    track.resolve_pending(tracking, now=base + timedelta(hours=26))
    assert tracking["history"][0]["status"] == "timeout"
    assert tracking["history"][0]["outcome"]["bars_held"] == 24
    print("ok: timeout menunggu bar ke-24 benar-benar selesai")


# ------------------------------------------------------- 6. rec_log
def _entry_rec(now: datetime, entry: float = 4300.0, exp: bool = True) -> dict:
    return {"status": "entry", "pattern": "inside_bar", "bias": "bearish",
            "confidence": 0.34, "experiment": exp,
            "levels": {"entry": entry, "sl": entry + 15, "tp1": entry - 25,
                       "tp2": entry - 45},
            "levels_safe": {"entry": entry, "sl": entry + 10, "tp1": entry - 18}}


def test_reclog_recorded_flag_and_note():
    _isolate()
    now = datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc)
    reclog.log_run(_entry_rec(now), now=now, entry_recorded=True)
    log = reclog.log_run(_entry_rec(now), now=now + timedelta(minutes=30),
                         entry_recorded=False)
    slots = log["days"][0]["slots"]
    assert len(slots) == 2, slots
    # panggilan pertama = posisi nyata, kedua = "rekomendasi saja"
    assert slots[0]["recorded"] is True and "note" not in slots[0], slots[0]
    assert slots[1]["recorded"] is False and slots[1]["note"], slots[1]
    print("ok: slot entry diberi flag recorded + note jujur")


def test_reclog_dedup_duplicate_within_hour():
    _isolate()
    now = datetime(2026, 9, 17, 6, 10, tzinfo=timezone.utc)
    reclog.log_run(_entry_rec(now), now=now, entry_recorded=False)
    # jam 06:47 & 06:54: sinyal sama persis (watch loop + slot cadangan)
    log = reclog.log_run(_entry_rec(now), now=now + timedelta(minutes=37),
                         entry_recorded=False)
    log = reclog.log_run(_entry_rec(now), now=now + timedelta(minutes=44),
                         entry_recorded=False)
    assert len(log["days"][0]["slots"]) == 1, log["days"][0]["slots"]
    # level beda (sinyal baru) -> slot baru
    log = reclog.log_run(_entry_rec(now, entry=4310.0),
                         now=now + timedelta(minutes=50),
                         entry_recorded=False)
    assert len(log["days"][0]["slots"]) == 2
    # > 1 jam: sinyal sama pun dicatat (riwayat per jam tetap lengkap)
    log = reclog.log_run(_entry_rec(now, entry=4310.0),
                         now=now + timedelta(minutes=110),
                         entry_recorded=False)
    assert len(log["days"][0]["slots"]) == 3
    print("ok: slot duplikat identik < 1 jam didedup, sinyal beda tetap dicatat")


def test_reclog_cooldown_and_stale_notes():
    _isolate()
    now = datetime(2026, 9, 17, 7, 0, tzinfo=timezone.utc)
    log = reclog.log_run({"status": "tunggu", "cooldown": True}, now=now)
    slot = log["days"][0]["slots"][0]
    assert slot["note"] and slot["note"].startswith("cooldown"), slot
    # batch 7: slot cooldown membawa analisa lengkap (pencatatan di UI)
    log = reclog.log_run({"status": "tunggu", "cooldown": True,
                          "pattern": "inside_bar", "bias": "bearish",
                          "confidence": 0.55,
                          "levels": {"entry": 4342.28, "sl": 4329.0,
                                     "tp1": 4362.0, "atr14": 13.0}},
                         now=now + timedelta(minutes=61))
    slot = log["days"][0]["slots"][-1]
    assert slot["pattern"] == "inside_bar", slot
    assert slot["bias"] == "bearish", slot
    assert slot["recorded"] is False, slot
    assert slot["levels"] == {"entry": 4342.28, "sl": 4329.0, "tp1": 4362.0}, slot
    assert slot["confidence"] == 0.55, slot
    log = reclog.log_run({"status": "netral", "stale": True},
                         now=now + timedelta(minutes=122))
    slot = log["days"][0]["slots"][-1]
    assert slot["note"] and "belum segar" in slot["note"], slot
    print("ok: slot cooldown diberi note + analisa lengkap (pencatatan UI); data basi diberi note khusus")


if __name__ == "__main__":
    test_flat_bar_filter()
    test_gap_report_weekend_bridge()
    test_stale_guard()
    test_momentum_guard_blocks_short_bounce()
    test_momentum_guard_allows_short_continuation()
    test_momentum_guard_skips_long()
    test_cooldown_blocks_and_updates()
    test_cooldown_outcome_aware()
    test_resolve_starts_after_signal_bar()
    test_timeout_needs_closed_horizon()
    test_reclog_recorded_flag_and_note()
    test_reclog_dedup_duplicate_within_hour()
    test_reclog_cooldown_and_stale_notes()
    print("\nSEMUA TES AUDIT 2026-09-20 LULUS")