"""Test WhatsApp Fonnte (analyzer/wa.py) — tanpa jaringan: _send di-stub.

Run: python tests/test_wa.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import push, store, wa  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")


def _isolate() -> Path:
    tmp = Path(tempfile.mkdtemp())
    store.DATA_DIR = tmp   # write_json (save_state) menulis ke sini
    push.DATA_DIR = tmp     # _events_today (dipakai ulang wa) baca kalender sini
    wa.STATE_FILE = tmp / "wa_state.json"
    return tmp


def _env() -> None:
    os.environ["FONNTE_TOKEN"] = "TOKEN-TEST"
    os.environ["FONNTE_TARGET"] = "628123456789"


def _clear_env() -> None:
    os.environ.pop("FONNTE_TOKEN", None)
    os.environ.pop("FONNTE_TARGET", None)


def _rec(status: str = "entry", rid: str = "2026-09-09") -> dict:
    return {
        "id": rid, "status": status, "bias": "bearish", "pattern": "inside_bar",
        "confidence": 0.45, "created_at": "2026-09-09T10:00:00Z",
        "levels": {"entry": 2300.0, "sl": 2290.0, "tp1": 2315.0, "atr14": 10.0},
    }


def _write_calendar(events: list[dict]) -> None:
    (store.DATA_DIR / "calendar.json").write_text(
        json.dumps({"events": events}), encoding="utf-8")


def test_entry_message() -> None:
    m = wa.entry_message(_rec())
    assert "GoldPulse — Daily Recommendation Setup" in m, m
    # bias dulu, baru keterangan pola; tiap level baris sendiri
    assert m.index("Bias:") < m.index("Pola:"), m
    assert "Bias: TURUN (sell)" in m, m
    assert "Pola: Inside Bar searah trend H4" in m, m
    assert "Entry: 2,300.00 USD/oz" in m, m
    assert "\nSL: 2,290.00" in m and "\nTP1: 2,315.00" in m, m
    assert "Peluang historis: 45%" in m, m
    print("ok: pesan entry (judul baru + bias dulu + level per baris + peluang)")


def test_agenda_message() -> None:
    evs = [{"title": "CPI", "t_wib": "19:30"}, {"title": "Fed Chair", "t_wib": "21:00"}]
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)  # Rabu WIB
    m = wa.agenda_message(evs, now=now)
    assert "GoldPulse — Event (Rabu, 9 September 2026)" in m, m
    assert "CPI" in m and "19:30" in m and "Fed Chair" in m, m
    assert "2j..+3j" in m, m  # "−2j..+3j" (minus unicode)
    print("ok: pesan agenda (judul bertanggal WIB + daftar event + catatan jeda)")


def test_dispatch_skips_without_secrets() -> None:
    _isolate(); _clear_env()
    _write_calendar([{"title": "CPI", "t_utc": "2026-09-09T12:30:00Z"}])
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    wa.dispatch(_rec(), now=now)  # entry + agenda dalam window — harus senyap
    assert not wa.STATE_FILE.exists(), "secrets kosong tidak boleh menulis state"
    print("ok: secrets kosong -> WA dilewati tanpa error, tanpa state")


def test_dispatch_entry_dedup() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = wa._send

    def fake_send(message):
        calls.append(message)
        return "ok"

    wa._send = fake_send
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    _write_calendar([])
    wa.dispatch(_rec(), now=now)
    assert len(calls) == 1 and "Inside Bar" in calls[0], calls
    state = wa.load_state()
    assert state["entry_notified_id"] == "2026-09-09T10:00:00Z", state
    assert state["last_status"] == "ok", state

    # dispatch kedua created_at sama -> tidak kirim ulang
    wa.dispatch(_rec(), now=now + timedelta(hours=2))
    assert len(calls) == 1, calls

    # re-entry sehari yang sama (created_at baru) -> pesan BARU
    reentry = _rec(); reentry["created_at"] = "2026-09-09T14:00:00Z"
    wa.dispatch(reentry, now=now + timedelta(hours=4))
    assert len(calls) == 2, calls
    assert wa.load_state()["entry_notified_id"] == "2026-09-09T14:00:00Z"

    # sinyal entry saat posisi masih aktif (entry_recorded=False) -> senyap
    sig3 = _rec(); sig3["created_at"] = "2026-09-09T16:00:00Z"
    wa.dispatch(sig3, now=now + timedelta(hours=6), entry_recorded=False)
    assert len(calls) == 2, calls
    wa._send = orig
    print("ok: pesan entry 1x per entry TERCATAT (dedup created_at, "
          "re-entry dapat pesan baru, posisi aktif = senyap)")


def test_cooldown_message() -> None:
    m = wa.cooldown_message(_rec(status="tunggu"))
    assert "GoldPulse — Daily Recommendation (COOLDOWN)" in m, m
    # analisa lengkap apa adanya — persis pesan entry
    assert "Bias: TURUN (sell)" in m, m
    assert "Pola: Inside Bar searah trend H4" in m, m
    assert "Entry: 2,300.00 USD/oz" in m, m
    assert "\nSL: 2,290.00" in m and "\nTP1: 2,315.00" in m, m
    assert "Peluang historis: 45%" in m, m
    assert "cooldown re-entry" in m, m
    print("ok: pesan cooldown (label COOLDOWN + analisa lengkap apa adanya)")


def test_dispatch_cooldown_no_dedup() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = wa._send
    wa._send = lambda m: (calls.append(m), "ok")[1]
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    _write_calendar([])
    rec = _rec(status="tunggu"); rec["cooldown"] = True
    wa.dispatch(rec, now=now)
    assert len(calls) == 1 and "COOLDOWN" in calls[0], calls
    state = wa.load_state()
    assert state["last_status"] == "ok", state
    assert "entry_notified_id" not in state, state  # tak menyentuh dedup entry
    # keputusan user 2026-09-24: TANPA anti-spam — iterasi berikutnya sinyal
    # masih cooldown (created_at baru, sinyal sama) -> kirim lagi apa adanya
    rec2 = _rec(status="tunggu"); rec2["cooldown"] = True
    wa.dispatch(rec2, now=now + timedelta(hours=1))
    assert len(calls) == 2, calls
    # tunggu karena blackout event (tanpa flag cooldown) -> senyap
    wa.dispatch(_rec(status="tunggu"), now=now + timedelta(hours=2))
    assert len(calls) == 2, calls
    wa._send = orig
    print("ok: pesan cooldown tanpa dedup (kirim apa adanya); tunggu "
          "blackout senyap; dedup entry tak tersentuh")


def test_dispatch_agenda_window() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = wa._send
    wa._send = lambda m: (calls.append(m), "ok")[1]

    # event pertama hari ini 19:30 WIB (12:30 UTC) — sekarang 08:00 UTC = H-4.5
    _write_calendar([{"title": "CPI", "t_utc": "2026-09-09T12:30:00Z"}])
    now = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
    wa.dispatch(_rec(status="netral"), now=now)
    assert calls == [], calls

    # 09:31 UTC = lewat H-3 -> kirim, lalu dedup per hari WIB
    now2 = datetime(2026, 9, 9, 9, 31, tzinfo=timezone.utc)
    wa.dispatch(_rec(status="netral"), now=now2)
    assert len(calls) == 1 and "CPI" in calls[0], calls
    assert wa.load_state()["agenda_date"] == "2026-09-09"
    wa.dispatch(_rec(status="netral"), now=now2 + timedelta(hours=1))
    assert len(calls) == 1, calls
    wa._send = orig
    print("ok: pesan agenda H-3 sebelum event pertama, 1x per hari")


def test_dispatch_agenda_only_that_day() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = wa._send
    wa._send = lambda m: (calls.append(m), "ok")[1]

    # event hari ini sudah lewat -> kirim segera (1x); event besok tidak ikut
    _write_calendar([
        {"title": "CPI", "t_utc": "2026-09-09T12:30:00Z"},
        {"title": "PPI", "t_utc": "2026-09-10T12:30:00Z"},
    ])
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)  # 21:00 WIB
    wa.dispatch(_rec(status="netral"), now=now)
    assert len(calls) == 1 and "CPI" in calls[0] and "PPI" not in calls[0], calls
    wa._send = orig
    print("ok: event lewat dikirim segera 1x; hanya event hari WIB itu")


def test_dispatch_failed_send_can_retry() -> None:
    _isolate(); _env()
    orig = wa._send
    wa._send = lambda m: "error"  # jaringan mati / Fonnte menolak

    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    _write_calendar([])
    wa.dispatch(_rec(), now=now)
    state = wa.load_state()
    assert state["last_status"] == "error", state
    # gagal -> dedup id TIDAK dicatat -> iterasi berikut bisa coba ulang
    assert "entry_notified_id" not in state, state

    wa._send = lambda m: "ok"  # pulih -> pesan terkirim, dedup tercatat
    wa.dispatch(_rec(), now=now + timedelta(minutes=30))
    assert wa.load_state()["entry_notified_id"] == "2026-09-09T10:00:00Z"
    wa._send = orig
    print("ok: gagal kirim ditandai 'error' + id tidak dianggap terkirim "
          "(retry iterasi berikut)")


def main() -> int:
    test_entry_message()
    test_agenda_message()
    test_cooldown_message()
    test_dispatch_skips_without_secrets()
    test_dispatch_entry_dedup()
    test_dispatch_cooldown_no_dedup()
    test_dispatch_agenda_window()
    test_dispatch_agenda_only_that_day()
    test_dispatch_failed_send_can_retry()
    print("\nALL WA TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())