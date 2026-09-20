"""Notifikasi WhatsApp via Fonnte (fonnte.com) — kanal TAMBAHAN selain
Web Push PWA, dipicu job daily (permintaan user 2026-09-20).

Dua pesan saja (teks WIB, storage UTC — semantik IDENTIK push.dispatch):
  1. ENTRY  — rekomendasi status "entry" yang dicatat feedback loop:
     1x per entry (dedup created_at; re-entry sehari yang sama tetap
     dapat pesan; sinyal saat posisi masih aktif = senyap).
  2. AGENDA — 1x per hari WIB: H-3 jam sebelum event bintang-3 PERTAMA
     hari itu, isi = daftar SEMUA event 3★ hari yang sama. Kalau hari itu
     tidak ada event 3★ → tidak ada pesan.

Doc Fonnte memakai contoh PHP, tapi API-nya HTTP POST biasa — cukup
`requests` (pola sama pywebpush di push.py).

Credential TIDAK pernah di repo (repo public):
  env FONNTE_TOKEN   — token device Fonnte (GitHub Secrets + .env lokal)
  env FONNTE_TARGET  — nomor WA penerima, format 62xxxxxxxxxx (Secrets).
Salah satu kosong → WA dilewati tanpa error. Gagal kirim TIDAK pernah
menggagalkan job (notif = bonus, bukan critical path analisa).

Dedup state INDEPENDEN dari Web Push di data/wa_state.json (publik,
tanpa data sensitif — hanya tanggal + status): dua kanal masing-masing
menghitung "sudah dikirim hari ini / untuk entry ini" sendiri, jadi
gagal di satu kanal tidak membatalkan dedup kanal lain.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import DATA_DIR
from .push import AGENDA_LEAD_H, ARAH, PATTERN_LABELS, _events_today
from .store import write_json

WIB = ZoneInfo("Asia/Jakarta")

STATE_FILE = DATA_DIR / "wa_state.json"
FONNTE_URL = "https://api.fonnte.com/send"
MAX_AGENDA_EVENTS = 5  # sama seperti push agenda — pesan WA tetap ringkas


# ---- env ----

def _credentials() -> tuple[str, str] | None:
    """(token, target) dari env (GitHub Secrets). Kosong = WA off."""
    token = (os.environ.get("FONNTE_TOKEN") or "").strip()
    target = (os.environ.get("FONNTE_TARGET") or "").strip()
    if not token or not target:
        return None
    return token, target


# ---- state (dedup, terpisah dari push_state.json) ----

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict, now: datetime) -> None:
    state["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["updated_at_wib"] = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
    write_json("wa_state.json", state)


# ---- pembangun pesan (dipisah supaya bisa dites tanpa jaringan) ----

def entry_message(rec: dict) -> str:
    """Pesan WA untuk rekomendasi status 'entry'."""
    lv = rec.get("levels") or {}
    pola = PATTERN_LABELS.get(rec.get("pattern") or "", rec.get("pattern") or "pola H1")
    arah = ARAH.get(rec.get("bias") or "", rec.get("bias") or "netral")
    out = (f"GoldPulse — setup entry hari ini\n\n"
           f"{pola} searah trend H4 — bias {arah}.\n"
           f"Entry {lv.get('entry', 0):,.2f} · SL {lv.get('sl', 0):,.2f} · "
           f"TP1 {lv.get('tp1', 0):,.2f} (USD/oz).")
    conf = rec.get("confidence")
    if conf is not None:
        out += f"\nPeluang historis {round(conf * 100)}%."
    return out


def agenda_message(events: list[dict]) -> str:
    """Pesan WA agenda: daftar event 3★ hari itu (t_wib sudah diisi)."""
    lines = []
    for e in events[:MAX_AGENDA_EVENTS]:
        lines.append(f"• {e['title']} — {e['t_wib']}")
    extra = len(events) - len(lines)
    if extra > 0:
        lines.append(f"• (+{extra} event lagi)")
    return ("GoldPulse — agenda event hari ini\n\n"
            "Event bintang-3 AS hari ini (WIB):\n" + "\n".join(lines) +
            "\nJeda entry otomatis −2j..+3j tiap event.")


# ---- kirim ----

def _send(message: str) -> str:
    """Kirim satu pesan via Fonnte. Return 'ok' / 'error'."""
    import requests  # import lazy: tes tanpa jaringan

    creds = _credentials()
    if creds is None:
        return "error"
    token, target = creds
    try:
        resp = requests.post(
            FONNTE_URL,
            headers={"Authorization": token},
            data={"target": target, "message": message},
            timeout=30,
        )
        data = resp.json()
        if data.get("status") is True:
            return "ok"
        # {"status": false, "reason": "..."} — token salah / kuota / nomor
        print(f"[wa] Fonnte menolak: {data.get('reason', data)}")
        return "error"
    except Exception as exc:  # jaringan dsb. — tidak boleh gagalkan job
        print(f"[wa] gagal kirim: {exc}")
        return "error"


def dispatch(rec: dict, now: datetime | None = None,
             entry_recorded: bool = True) -> None:
    """Pintu job daily: pesan ENTRY + AGENDA (dedup wa_state.json).

    entry_recorded=False → sinyal entry tidak dikirim (posisi masih aktif —
    aturan 1 posisi, lihat jobs/daily.record_entry). Tidak pernah raise.
    """
    now = now or datetime.now(timezone.utc)
    if _credentials() is None:
        print("[wa] tidak terkonfigurasi (secrets kosong) — WA dilewati")
        return

    state = load_state()
    today = now.astimezone(WIB).strftime("%Y-%m-%d")
    status: str | None = None
    sent_wib: str | None = None

    # --- 1. pesan entry (1x per entry TERCATAT; dedup via created_at) ---
    if (rec.get("status") == "entry" and entry_recorded
            and state.get("entry_notified_id") != rec.get("created_at")):
        st = _send(entry_message(rec))
        status = st or status
        if st == "ok":
            state["entry_notified_id"] = rec.get("created_at")
            sent_wib = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
            print("[wa] pesan entry terkirim")
        else:
            print("[wa] pesan entry GAGAL — bisa dicoba ulang iterasi berikut")

    # --- 2. pesan agenda event (1x per hari WIB, H-3 event pertama) ---
    events_today = _events_today(now)
    if events_today and state.get("agenda_date") != today:
        first_utc = events_today[0]["t_utc_dt"]
        if now >= first_utc - timedelta(hours=AGENDA_LEAD_H):
            st = _send(agenda_message(events_today))
            status = st or status
            if st == "ok":
                state["agenda_date"] = today
                sent_wib = sent_wib or now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
                print(f"[wa] pesan agenda terkirim ({len(events_today)} event)")
            else:
                print("[wa] pesan agenda GAGAL — bisa dicoba ulang iterasi berikut")

    if status is not None:
        state["last_status"] = status
        state["last_sent_wib"] = sent_wib
        save_state(state, now)