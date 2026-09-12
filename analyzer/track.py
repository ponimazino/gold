"""Feedback loop (P5): catat setiap rekomendasi entry, lalu nilai hasilnya
terhadap pergerakan harga aktual — hit TP1 sebelum SL (win), SL duluan
(loss), atau timeout. Aturan identik backtest: konservatif (SL dicek
duluan kalau TP & SL tersentuh di bar yang sama).

Saat resolve, tiap rekomendasi juga dapat 'outcome': metrik perjalanan
posisi (berapa jam, pergerakan maksimum searah/melawan, event 3★ di
jendela) plus narasi "mengapa benar/salah" — digabung per tanggal WIB
menjadi log akhir hari 'eod' di tracking.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import store
from .backtest import DEFAULT_HORIZON

WIB = ZoneInfo("Asia/Jakarta")
HORIZON_1H = DEFAULT_HORIZON["1h"]  # 24 bar H1


def load_tracking() -> dict:
    path = store.DATA_DIR / "tracking.json"
    if not path.exists():
        return {"updated_at": None, "stats": {}, "history": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"updated_at": None, "stats": {}, "history": []}


def _wib(t: str) -> str:
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")


def _events_in_window(start_t: str, end_t: str) -> list[dict]:
    """Event 3★ US yang jatuh di jendela posisi (entry → selesai dinilai).
    Format 't_utc' ISO-Z sama dengan 'created_at' bar — bisa dibandingkan
    sebagai string (leksikografis = kronologis)."""
    path = store.DATA_DIR / "calendar.json"
    if not path.exists():
        return []
    try:
        events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
    except json.JSONDecodeError:
        return []
    return [e for e in events if e.get("importance") == 3
            and start_t < e.get("t_utc", "") <= end_t]


def _attach_outcome(rec: dict, held: list[dict]) -> None:
    """Metrik + narasi 'mengapa' untuk rekomendasi yang baru selesai dinilai.
    held = bar H1 dari entry sampai bar penyelesai (inklusif)."""
    d = rec["direction"]
    entry, sl, tp1 = rec["levels"]["entry"], rec["levels"]["sl"], rec["levels"]["tp1"]
    risk, reward = abs(entry - sl), abs(entry - tp1)
    mfe = max((b["h"] - entry) if d == 1 else (entry - b["l"]) for b in held)
    mae = max((entry - b["l"]) if d == 1 else (b["h"] - entry) for b in held)
    hours = len(held)  # 1 bar H1 = 1 jam
    events = _events_in_window(rec["created_at"], rec["resolved_at"])
    ev_names = ", ".join(e["title"] for e in events)
    ev_note = (f"ada event 3★ saat posisi berjalan ({ev_names})"
               if events else "tanpa event 3★ di jendela posisi")

    if rec["status"] == "win":
        why = (f"Benar — TP1 kena {hours} jam setelah entry; pergerakan maksimum "
               f"searah USD {mfe:.0f}/oz, {ev_note}")
        if risk and mae >= 0.5 * risk:
            why += (f"; sempat tertekan melawan USD {mae:.0f}/oz "
                    f"(≈{mae / risk:.0f}% dari jarak SL) sebelum berbalik searah")
    elif rec["status"] == "loss":
        why = (f"Salah — SL kena {hours} jam setelah entry; pergerakan searah "
               f"cuma USD {mfe:.0f}/oz (≈{(mfe / reward * 100) if reward else 0:.0f}% "
               f"dari jarak TP1)")
        why += (f"; {ev_note} — spike volatilitas rilis bisa jadi pemicunya"
                if events else "; tanpa event 3★ — struktur harga memang berbalik melawan bias H4")
    else:  # timeout
        why = (f"Tidak terbukti — {hours} jam tanpa menyentuh SL/TP; pergerakan "
               f"searah maksimum USD {mfe:.0f}/oz "
               f"(≈{(mfe / reward * 100) if reward else 0:.0f}% dari jarak TP1), "
               f"momentum tidak follow-through, {ev_note}")

    rec["outcome"] = {
        "bars_held": hours,
        "mfe_usd": round(mfe, 2),   # pergerakan maksimum searah (favorable)
        "mae_usd": round(mae, 2),   # pergerakan maksimum melawan (adverse)
        "events": [e["title"] for e in events],
        "why": why,
        "resolved_at_wib": _wib(rec["resolved_at"]),
    }


def _resolve_one(rec: dict, bars: list[dict], horizon: int = HORIZON_1H) -> bool:
    """Update rec['status'] in-place. Return True kalau status berubah."""
    if rec.get("status") == "entry":
        # job harian mencatat rekomendasi baru dengan status "entry";
        # normalisasi ke "active" supaya mulai dinilai terhadap harga aktual.
        rec["status"] = "active"
    if rec.get("status") != "active":
        return False
    d = rec["direction"]
    entry_t = rec["created_at"]
    entry, sl, tp1 = rec["levels"]["entry"], rec["levels"]["sl"], rec["levels"]["tp1"]
    after = [b for b in bars if b["t"] > entry_t]
    window = after[:horizon]
    for i, b in enumerate(window):
        hit_sl = b["l"] <= sl if d == 1 else b["h"] >= sl
        hit_tp = b["h"] >= tp1 if d == 1 else b["l"] <= tp1
        if hit_sl or hit_tp:  # konservatif: SL dianggap duluan
            rec.update({"status": "loss" if hit_sl else "win", "resolved_at": b["t"]})
            _attach_outcome(rec, window[: i + 1])
            return True
    if len(after) >= horizon:
        rec.update({"status": "timeout", "resolved_at": window[-1]["t"]})
        _attach_outcome(rec, window)
        return True
    return False  # masih berjalan


def build_eod(tracking: dict, now: datetime | None = None) -> list[dict]:
    """Log akhir hari per tanggal WIB: hasil + alasan tiap rekomendasi entry
    yang sudah selesai dinilai, PLUS hari tanpa rekomendasi ditandai 'skip'
    supaya riwayat harian lengkap — hari "kosong" jelas memang tanpa setup,
    bukan lubang data. Diturunkan penuh dari 'history' — idempotent."""
    now = now or datetime.now(timezone.utc)
    days: dict[str, dict] = {}
    for r in tracking.get("history", []):
        rid = r.get("id") or "?"
        if r.get("status") in ("win", "loss", "timeout"):
            out = r.get("outcome") or {}
            day = days.setdefault(rid, {"date": rid, "entries": []})
            day["entries"].append({
                "pattern": r.get("pattern"),
                "bias": r.get("bias"),
                **({"experiment": True} if r.get("experiment") else {}),
                "status": r["status"],
                "entry_at_wib": r.get("created_at_wib"),
                "resolved_at_wib": out.get("resolved_at_wib"),
                "mfe_usd": out.get("mfe_usd"),
                "mae_usd": out.get("mae_usd"),
                "events": out.get("events", []),
                "why": out.get("why"),
            })
        elif r.get("status") in ("entry", "active"):
            # entry hari itu masih dievaluasi — hari tandai "run"
            days.setdefault(rid, {"date": rid, "entries": [], "status": "run"})
    # rentang hari pertama ada rekomendasi -> hari ini (WIB): hari tanpa
    # entry apa pun ditandai 'skip' (netral/tunggu/pasar tutup). Hanya id
    # berformat tanggal yang dipakai sebagai batas rentang.
    ids = []
    for r in tracking.get("history", []):
        rid = r.get("id")
        try:
            datetime.strptime(rid, "%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        ids.append(rid)
    if ids:
        d = datetime.strptime(min(ids), "%Y-%m-%d").date()
        last = now.astimezone(WIB).date()
        while d <= last:
            key = d.isoformat()
            day = days.setdefault(key, {"date": key, "entries": []})
            if not day["entries"] and day.get("status") != "run":
                day.setdefault("status", "skip")
            d += timedelta(days=1)
    return sorted(days.values(), key=lambda x: x["date"] or "", reverse=True)


def resolve_pending(tracking: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    bars = store.load("1h")
    for rec in tracking.get("history", []):
        _resolve_one(rec, bars)
    tracking["eod"] = build_eod(tracking, now=now)
    tracking["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tracking["updated_at_wib"] = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
    return tracking


def compute_stats(tracking: dict) -> dict:
    hist = tracking.get("history", [])
    # entry EKSPERIMEN (gate sadar-arah 2026-09-12: arah dengan bukti
    # bermakna tapi EV negatif) dinilai TERPISAH — jangan mencemari
    # hit-rate statistik utama; review saat n live eksperimen >= 30
    exp = [r for r in hist if r.get("experiment")]
    main = [r for r in hist if not r.get("experiment")]

    def _count(rows: list[dict], status: str) -> int:
        return sum(1 for r in rows if r.get("status") == status)

    # statistik utama (tanpa entry eksperimen)
    wins = _count(main, "win")
    losses = _count(main, "loss")
    timeouts = _count(main, "timeout")
    # "entry" = baru dicatat job daily, belum dinormalisasi _resolve_one ke
    # "active" — dua-duanya posisi berjalan (audit 2026-09-12: tile UI
    # "Berjalan" pernah tampil 0 padahal ada 1 posisi entry aktif)
    active = sum(1 for r in main if r.get("status") in ("active", "entry"))
    resolved = wins + losses + timeouts
    tracking["stats"] = {
        "total": len(hist),
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "active": active,
        "resolved": resolved,
        "hit_rate": round(wins / resolved, 3) if resolved else None,
    }
    if exp:
        exp_wins = _count(exp, "win")
        exp_resolved = sum(1 for r in exp
                           if r.get("status") in ("win", "loss", "timeout"))
        tracking["stats"]["experiment"] = {
            "total": len(exp),
            "wins": exp_wins,
            "losses": _count(exp, "loss"),
            "timeouts": _count(exp, "timeout"),
            "active": sum(1 for r in exp
                          if r.get("status") in ("active", "entry")),
            "resolved": exp_resolved,
            "hit_rate": round(exp_wins / exp_resolved, 3) if exp_resolved else None,
        }
    return tracking