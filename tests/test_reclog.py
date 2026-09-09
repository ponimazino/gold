"""Smoke test reclog: log per-run analisa harian (slot 2 jam + jam WIB).
Jalankan: python tests/test_reclog.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import reclog, store  # noqa: E402


def _isolate() -> None:
    """Pakai folder data sementara supaya data asli tidak tersentuh."""
    import tempfile
    store.DATA_DIR = Path(tempfile.mkdtemp(prefix="reclog-test-"))
    reclog.store.DATA_DIR = store.DATA_DIR


def test_entry_and_netral_slots():
    _isolate()
    t = (2026, 9, 10, 3, 2)  # 10:02 WIB
    from datetime import datetime, timezone
    now = datetime(*t, tzinfo=timezone.utc)

    entry = {
        "status": "entry", "pattern": "inside_bar", "bias": "bullish",
        "confidence": 0.42,
        "levels": {"entry": 2500.0, "sl": 2494.0, "tp1": 2509.0, "tp2": 2518.0, "atr14": 6.0},
        "levels_safe": {"entry": 2500.0, "sl": 2495.5, "tp1": 2506.0, "atr14": 6.0},
    }
    log = reclog.log_run(entry, now=now)
    d = log["days"][0]
    assert d["date"] == "2026-09-10" and len(d["slots"]) == 1, d
    s = d["slots"][0]
    assert s["t_wib"] == "10:02" and s["status"] == "entry", s
    assert s["levels"]["entry"] == 2500.0 and s["levels_safe"]["sl"] == 2495.5, s
    print("ok: slot entry (jam WIB + level + mode aman)")

    # run berikutnya netral — jam berbeda, slot baru
    netral = {"status": "netral", "bias": "netral", "confidence": None}
    reclog.log_run(netral, now=now.replace(hour=5))  # 12:02 WIB
    d = reclog.load()["days"][0]
    assert [x["t_wib"] for x in d["slots"]] == ["10:02", "12:02"], d
    assert d["slots"][1]["status"] == "netral" and "levels" not in d["slots"][1]
    print("ok: slot netral terpisah per jam (urut naik dalam hari)")

    # tunggu: ada pola tapi jeda event — tetap dicatat
    reclog.log_run({"status": "tunggu"}, now=now.replace(hour=7))
    assert reclog.load()["days"][0]["slots"][-1]["status"] == "tunggu"
    print("ok: slot 'tunggu' tercatat")


def test_dedup_minute_and_trim():
    _isolate()
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    # dobel-run menit sama -> satu slot, bukan dua
    reclog.log_run({"status": "netral"}, now=base)
    reclog.log_run({"status": "entry"}, now=base)  # menit sama, status beda
    d = reclog.load()["days"][0]
    assert len(d["slots"]) == 1 and d["slots"][0]["status"] == "entry", d
    print("ok: dobel-run menit sama menimpa slot (dedup)")

    # trim: 35 hari -> hanya 30 terakhir
    for i in range(35):
        reclog.log_run({"status": "netral"},
                       now=base + timedelta(days=i + 1, hours=7))
    days = reclog.load()["days"]
    assert len(days) == 30, len(days)
    assert days[0]["date"] == "2026-09-05" and days[-1]["date"] == "2026-08-07", \
        (days[0]["date"], days[-1]["date"])
    assert days == sorted(days, key=lambda x: x["date"], reverse=True)
    print("ok: trim 30 hari, urut terbaru dulu")


def main():
    test_entry_and_netral_slots()
    test_dedup_minute_and_trim()
    print("\nALL RECLOG TESTS PASSED")


if __name__ == "__main__":
    main()