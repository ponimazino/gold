"""Test Web Push (analyzer/push.py) — tanpa jaringan: _send di-stub,
pywebpush dipalsukan via sys.modules.

Run: python tests/test_push.py
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import push, store  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")


def _isolate() -> Path:
    tmp = Path(tempfile.mkdtemp())
    store.DATA_DIR = tmp
    push.DATA_DIR = tmp
    push.STATE_FILE = tmp / "push_state.json"
    return tmp


def _env() -> None:
    os.environ["PUSH_SUBSCRIPTIONS"] = json.dumps(
        [{"endpoint": "https://push.example/x", "keys": {}}])
    os.environ["PUSH_VAPID_PRIVATE_KEY"] = "a" * 43  # dummy — _send di-stub
    os.environ["PUSH_CONTACT_EMAIL"] = "test@example.com"


def _rec(status: str = "entry", rid: str = "2026-09-09") -> dict:
    return {
        "id": rid, "status": status, "bias": "bearish", "pattern": "inside_bar",
        "confidence": 0.45,
        "levels": {"entry": 2300.0, "sl": 2290.0, "tp1": 2315.0, "tp2": 2330.0,
                   "atr14": 10.0},
    }


def _write_calendar(events: list[dict]) -> None:
    (store.DATA_DIR / "calendar.json").write_text(
        json.dumps({"events": events}), encoding="utf-8")


def test_entry_payload() -> None:
    p = push.entry_payload(_rec())
    assert "Inside Bar" in p["body"], p
    assert "2,300.00" in p["body"], p
    assert "45%" in p["body"], p
    assert p["tag"] == "entry" and p["url"] == "#/analysis", p
    print("ok: payload entry (pola + level + confidence)")


def test_agenda_payload() -> None:
    evs = [{"title": "CPI", "t_wib": "19:30"}, {"title": "Fed Chair", "t_wib": "21:00"}]
    p = push.agenda_payload(evs)
    assert "CPI" in p["body"] and "19:30" in p["body"], p
    assert "2j..+1j" in p["body"], p  # "−2j..+1j" (minus unicode)
    assert p["tag"] == "agenda" and p["url"] == "#/calendar", p
    print("ok: payload agenda (daftar event WIB + catatan jeda)")


def test_dispatch_entry_dedup() -> None:
    _isolate(); _env()
    calls: list[str] = []

    def fake_send(subs, payload, claims):
        calls.append(payload["tag"])
        return "ok"

    orig = push._send
    push._send = fake_send
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)

    # kalender tanpa event -> hanya notif entry yang dipertimbangkan
    _write_calendar([])
    push.dispatch(_rec(), now=now)
    assert calls == ["entry"], calls
    state = push.load_state()
    assert state["entry_notified_id"] == "2026-09-09", state
    assert state["last_status"] == "ok", state

    # dispatch kedua dengan id sama -> tidak kirim ulang
    push.dispatch(_rec(), now=now + timedelta(hours=2))
    assert calls == ["entry"], calls
    push._send = orig
    print("ok: notif entry 1x per hari (dedup id rec) + state tersimpan")


def test_dispatch_agenda_window() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = push._send
    push._send = lambda subs, payload, claims: (calls.append(payload["tag"]), "ok")[1]

    # event pertama hari ini 19:30 WIB (12:30 UTC) — sekarang 08:00 UTC = 15:00 WIB
    ev_utc = "2026-09-09T12:30:00Z"
    _write_calendar([{"title": "CPI", "t_utc": ev_utc}])
    now = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
    push.dispatch(_rec(status="netral"), now=now)  # H-4.5 -> belum waktunya
    assert calls == [], calls

    # 09:31 UTC (16:31 WIB) = lewat H-3 -> kirim, lalu dedup
    now2 = datetime(2026, 9, 9, 9, 31, tzinfo=timezone.utc)
    push.dispatch(_rec(status="netral"), now=now2)
    assert calls == ["agenda"], calls
    assert push.load_state()["agenda_date"] == "2026-09-09"
    push.dispatch(_rec(status="netral"), now=now2 + timedelta(hours=1))
    assert calls == ["agenda"], calls
    push._send = orig
    print("ok: notif agenda H-3 sebelum event pertama, 1x per hari")


def test_dispatch_agenda_late_and_other_day() -> None:
    _isolate(); _env()
    calls: list[str] = []
    orig = push._send
    push._send = lambda subs, payload, claims: (calls.append("send"), "ok")[1]

    # event sudah lewat saat pertama dicek -> kirim segera (1x), event besok
    # tidak ikut (dihitung per hari WIB)
    _write_calendar([
        {"title": "CPI", "t_utc": "2026-09-09T12:30:00Z"},
        {"title": "PPI", "t_utc": "2026-09-10T12:30:00Z"},
    ])
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)  # 21:00 WIB
    push.dispatch(_rec(status="netral"), now=now)
    assert calls == ["send"], calls
    payload = push.agenda_payload(push._events_today(now))
    assert "CPI" in payload["body"] and "PPI" not in payload["body"], payload
    push._send = orig
    print("ok: event lewat dikirim segera 1x; hanya event hari WIB itu")


def test_send_expired_marking() -> None:
    _isolate(); _env()

    # palsukan modul pywebpush: webpush raise 410 (subscription mati)
    mod = types.ModuleType("pywebpush")

    class WebPushException(Exception):
        def __init__(self, message="", response=None):
            super().__init__(message)
            self.response = response

    class Resp:
        status_code = 410

    def webpush(**kwargs):
        raise WebPushException("gone", response=Resp())

    mod.webpush = webpush
    mod.WebPushException = WebPushException
    sys.modules["pywebpush"] = mod
    try:
        status = push._send(push._subscriptions(),
                           push.entry_payload(_rec()),
                           {"sub": "mailto:test@example.com"})
        assert status == "expired", status
    finally:
        del sys.modules["pywebpush"]

    # dispatch menandai expired di push_state (UI menampilkan banner)
    orig = None
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    _write_calendar([])
    # pakai pywebpush palsukan lagi untuk dispatch path
    sys.modules["pywebpush"] = mod
    try:
        push.dispatch(_rec(), now=now)
        state = push.load_state()
        assert state["last_status"] == "expired", state
        assert "entry_notified_id" not in state, state  # gagal -> bisa retry
    finally:
        del sys.modules["pywebpush"]
    assert orig is None
    print("ok: 410/404 ditandai 'expired' + id entry tidak dianggap terkirim")


def main() -> int:
    test_entry_payload()
    test_agenda_payload()
    test_dispatch_entry_dedup()
    test_dispatch_agenda_window()
    test_dispatch_agenda_late_and_other_day()
    test_send_expired_marking()
    print("\nALL PUSH TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())