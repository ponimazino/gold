"""Tes arsip event historis + jendela blackout (batch 2, 2026-09-20).

load_hist / archive_past di analyzer/calendar.py + BLACKOUT_AFTER_H = 3 jam
(divalidasi scripts/audit_events.py: volatilitas baru normal di +3h,
EV sinyal +0..+3h pasca-event negatif).

PENTING: tes ini mem-patch store.DATA_DIR ke folder temp — JANGAN pernah
menulis/hapus file data/ produksi dari tes (bug 2026-09-20: versi awal tes
ini menghapus arsip events_hist.json produksi 835 event).
"""

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import calendar as cal  # noqa: E402
from analyzer import store  # noqa: E402

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


@contextmanager
def temp_data_dir():
    """Arahkan store.DATA_DIR ke folder sementara (produksi aman)."""
    real = store.DATA_DIR
    with tempfile.TemporaryDirectory() as td:
        store.DATA_DIR = Path(td)
        try:
            yield Path(td)
        finally:
            store.DATA_DIR = real


def test_load_hist_missing_and_corrupt() -> None:
    with temp_data_dir() as td:
        hist = td / "events_hist.json"
        assert cal.load_hist() == []          # file belum ada -> kosong
        hist.write_text("{bukan json", encoding="utf-8")
        assert cal.load_hist() == []          # korup -> kosong, bukan crash
        hist.write_text(json.dumps({"events": "bukan-list"}), encoding="utf-8")
        assert cal.load_hist() == []          # schema salah -> kosong
        hist.write_text(json.dumps({
            "events": [
                {"title": "B"}, "tanpa-tahu",
                {"t_utc": "2026-01-02T13:30:00Z", "title": "Belakang"},
                {"t_utc": "2026-01-01T13:30:00Z", "title": "Depan"},
            ]}), encoding="utf-8")
        out = cal.load_hist()
        assert [e["title"] for e in out] == ["Depan", "Belakang"]  # urut + tersaring
    print("ok: load_hist (hilang/korup/schema/urut)")


def test_archive_past_idempotent_and_future_skipped() -> None:
    with temp_data_dir():
        feed = [
            # sudah lewat -> diarsip
            {"t_utc": "2026-09-19T13:30:00Z", "title": "Core CPI m/m"},
            # masih ke depan -> TIDAK diarsip (jadwal bisa bergeser)
            {"t_utc": (NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "title": "FOMC Statement"},
            # tanpa field -> dilewati
            {"title": "Tanpa waktu"},
        ]
        added = cal.archive_past(feed, now=NOW)
        assert added == 1, added
        hist = cal.load_hist()
        assert len(hist) == 1 and hist[0]["title"] == "Core CPI m/m", hist
        # run kedua dengan feed sama -> idempotent (dedup t_utc+title)
        assert cal.archive_past(feed, now=NOW) == 0
        assert len(cal.load_hist()) == 1
        # waktu sama tapi judul beda = event beda; yang lama tetap
        added3 = cal.archive_past(
            [{"t_utc": "2026-09-18T13:30:00Z", "title": "Core CPI m/m"},
             {"t_utc": "2026-09-19T13:30:00Z", "title": "Core CPI m/m"}],
            now=NOW)
        assert added3 == 1, added3
        assert len(cal.load_hist()) == 2
        # payload punya envelope yang benar
        env = json.loads((store.DATA_DIR / "events_hist.json")
                         .read_text(encoding="utf-8"))
        assert "updated_at" in env and "source" in env and "events" in env
    print("ok: archive_past (idempotent / skip future / skip invalid)")


def test_blackout_window_3h_after() -> None:
    assert cal.BLACKOUT_AFTER_H == 3.0, cal.BLACKOUT_AFTER_H
    ev = [{"t_utc": "2026-09-20T13:30:00Z", "title": "Fed Chair Powell Speaks"}]
    t = cal.parse_utc(ev[0]["t_utc"])
    assert cal.active_blackout(ev, now=t - timedelta(hours=2))       # tepat -2h
    assert cal.active_blackout(ev, now=t - timedelta(hours=2, minutes=1)) is None
    assert cal.active_blackout(ev, now=t) is not None               # saat rilis
    # +3h masih dalam jeda; +3h1m sudah tidak (batas jendela baru)
    assert cal.active_blackout(ev, now=t + timedelta(hours=3))
    assert cal.active_blackout(ev, now=t + timedelta(hours=3, minutes=1)) is None
    print("ok: jendela blackout -2h..+3h (pasca-event diperpanjang 1h -> 3h)")


if __name__ == "__main__":
    test_load_hist_missing_and_corrupt()
    test_archive_past_idempotent_and_future_skipped()
    test_blackout_window_3h_after()
    print("\nSEMUA TES ARSIP EVENT LULUS")