"""Web Push (VAPID) ke PWA GoldPulse — dipicu job daily (P5).

Dua notifikasi (teks WIB, storage UTC):
  1. ENTRY  — rekomendasi status "entry" yang dicatat feedback loop:
     1x per entry (dedup created_at — re-entry sehari yang sama tetap
     dapat notif; sinyal saat posisi masih aktif tidak dinotifikasi).
  2. AGENDA — 1x per hari WIB: H-3 jam sebelum event bintang-3 PERTAMA
     hari itu, isi = daftar SEMUA event 3★ hari yang sama (kalau run
     pertama yang lolos sudah lewat H-3, kirim segera — sisa event hari
     itu masih berguna).

Credential TIDAK pernah di repo (repo public):
  env PUSH_VAPID_PRIVATE_KEY  — private key VAPID (GitHub Secrets)
  env PUSH_CONTACT_EMAIL      — "mailto:..." untuk klaim VAPID (Secrets)
  env PUSH_SUBSCRIPTIONS      — JSON subscription dari PWA (Secrets).
     Disimpan sebagai Secret, bukan file data/, karena endpoint
     subscription bisa dipakai pihak lain untuk spam notifikasi bila
     ter-expose di repo public.

State dedup + status pengiriman di data/push_state.json (public, tanpa
data sensitif — hanya tanggal + status) supaya UI bisa menampilkan
banner "notif putus, aktifkan ulang" kalau subscription ditolak push
service (404/410).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import DATA_DIR
from .store import write_json

WIB = ZoneInfo("Asia/Jakarta")

STATE_FILE = DATA_DIR / "push_state.json"
AGENDA_LEAD_H = 3.0  # H-3 jam sebelum event pertama hari itu
MAX_AGENDA_EVENTS = 5

PATTERN_LABELS = {
    "bullish_engulfing": "Bullish Engulfing",
    "bearish_engulfing": "Bearish Engulfing",
    "hammer": "Hammer (pin bar)",
    "shooting_star": "Shooting Star",
    "inside_bar": "Inside Bar",
}

ARAH = {"bullish": "NAIK (buy)", "bearish": "TURUN (sell)"}


# ---- env & subscription ----

def _subscriptions() -> list[dict]:
    """Subscription aktif dari env (GitHub Secrets). Kosong = notif off."""
    raw = (os.environ.get("PUSH_SUBSCRIPTIONS") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("[push] PUSH_SUBSCRIPTIONS bukan JSON yang valid — notif dilewati")
        return []
    subs = data if isinstance(data, list) else [data]
    return [s for s in subs if isinstance(s, dict) and s.get("endpoint")]


def _vapid_claims() -> dict | None:
    key = (os.environ.get("PUSH_VAPID_PRIVATE_KEY") or "").strip()
    email = (os.environ.get("PUSH_CONTACT_EMAIL") or "").strip()
    if not key or not email:
        return None
    sub = email if email.startswith("mailto:") else f"mailto:{email}"
    return {"sub": sub}


# ---- state (dedup + status untuk UI) ----

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
    write_json("push_state.json", state)


# ---- payload builder (dipisah supaya bisa dites tanpa jaringan) ----

def entry_payload(rec: dict) -> dict:
    """Payload notif untuk rekomendasi status 'entry'."""
    lv = rec.get("levels") or {}
    pola = PATTERN_LABELS.get(rec.get("pattern") or "", rec.get("pattern") or "pola H1")
    arah = ARAH.get(rec.get("bias") or "", rec.get("bias") or "netral")
    conf = rec.get("confidence")
    body = (f"{pola} searah trend H4 — bias {arah}.\n"
            f"Entry ±{lv.get('entry', 0):,.2f} · SL {lv.get('sl', 0):,.2f} · "
            f"TP1 {lv.get('tp1', 0):,.2f} (USD/oz).")
    if conf is not None:
        body += f"\nPeluang historis {round(conf * 100)}%."
    return {
        "title": "GoldPulse — setup entry hari ini",
        "body": body,
        "tag": "entry",
        "url": "#/analysis",
    }


def agenda_payload(events: list[dict]) -> dict:
    """Payload notif agenda: daftar event 3★ hari itu (t_wib sudah diisi)."""
    lines = []
    for e in events[:MAX_AGENDA_EVENTS]:
        lines.append(f"• {e['title']} — {e['t_wib']}")
    extra = len(events) - len(lines)
    if extra > 0:
        lines.append(f"• (+{extra} event lagi)")
    body = ("Event bintang-3 AS hari ini (WIB):\n" + "\n".join(lines) +
            "\nJeda entry otomatis −2j..+1j tiap event.")
    return {
        "title": "GoldPulse — agenda event hari ini",
        "body": body,
        "tag": "agenda",
        "url": "#/calendar",
    }


# ---- kirim ----

def _send(subs: list[dict], payload: dict, claims: dict) -> str:
    """Kirim ke semua subscription. Return status: ok / expired / error."""
    from pywebpush import webpush, WebPushException  # import lazy: tes tanpa lib

    status = "ok"
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=os.environ["PUSH_VAPID_PRIVATE_KEY"],
                vapid_claims=claims,
                ttl=3600,
            )
        except WebPushException as exc:
            # 404/410 = subscription sudah tidak valid (browser rotasi/
            # uninstall) — tandai supaya UI minta aktifkan ulang.
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                print(f"[push] subscription ditolak ({code}) — tandai expired")
                status = "expired"
            else:
                print(f"[push] WebPushException: {exc}")
                status = "error"
        except Exception as exc:  # jaringan dsb. — tidak boleh gagalkan job
            print(f"[push] gagal kirim: {exc}")
            status = "error"
    return status


def dispatch(rec: dict, now: datetime | None = None,
             entry_recorded: bool = True) -> None:
    """Pintu job daily: notif ENTRY + AGENDA (dedup + status push_state.json).

    entry_recorded=False → sinyal entry tidak dinotifikasi (posisi masih
    aktif — aturan 1 posisi, lihat jobs/daily.record_entry).

    Tidak pernah raise — notif adalah bonus, bukan critical path analisa.
    """
    now = now or datetime.now(timezone.utc)
    subs = _subscriptions()
    claims = _vapid_claims()
    if not subs or not claims:
        print("[push] tidak terkonfigurasi (secrets kosong) — notif dilewati")
        return

    state = load_state()
    today = now.astimezone(WIB).strftime("%Y-%m-%d")
    status: str | None = None
    sent_wib: str | None = None

    # --- 1. notif entry (1x per entry TERCATAT; dedup via created_at —
    #     re-entry sehari yang sama tetap dapat notif, sinyal yang tidak
    #     dicatat karena posisi masih aktif TIDAK dinotifikasi) ---
    if (rec.get("status") == "entry" and entry_recorded
            and state.get("entry_notified_id") != rec.get("created_at")):
        st = _send(subs, entry_payload(rec), claims)
        status = st or status
        sent_wib = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
        if st != "expired":
            state["entry_notified_id"] = rec.get("created_at")
            print("[push] notif entry terkirim")
        else:
            print("[push] notif entry GAGAL — subscription expired")

    # --- 2. notif agenda event (1x per hari WIB, H-3 event pertama) ---
    events_today = _events_today(now)
    if events_today and state.get("agenda_date") != today:
        first_utc = events_today[0]["t_utc_dt"]
        if now >= first_utc - timedelta(hours=AGENDA_LEAD_H):
            st = _send(subs, agenda_payload(events_today), claims)
            status = st or status
            sent_wib = sent_wib or now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
            if st != "expired":
                state["agenda_date"] = today
                print(f"[push] notif agenda terkirim ({len(events_today)} event)")
            else:
                print("[push] notif agenda GAGAL — subscription expired")

    if status is not None:
        state["last_status"] = status
        state["last_sent_wib"] = sent_wib
        save_state(state, now)


def _events_today(now: datetime) -> list[dict]:
    """Event 3★ hari ini menurut kalender WIB (data/calendar.json),
    urut naik, dengan field bantu t_wib + t_utc_dt."""
    path = DATA_DIR / "calendar.json"
    if not path.exists():
        return []
    try:
        events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
    except (json.JSONDecodeError, OSError):
        return []
    today = now.astimezone(WIB).date()
    out = []
    for e in events:
        try:
            t_utc = datetime.fromisoformat(e["t_utc"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        t_wib = t_utc.astimezone(WIB)
        if t_wib.date() == today:
            out.append({**e, "t_utc_dt": t_utc, "t_wib": t_wib.strftime("%H:%M")})
    out.sort(key=lambda e: e["t_utc_dt"])
    return out