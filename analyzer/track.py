"""Feedback loop (P5): catat setiap rekomendasi entry, lalu nilai hasilnya
terhadap pergerakan harga aktual — hit TP1 sebelum SL (win), SL duluan
(loss), atau timeout. Aturan identik backtest: konservatif (SL dicek
duluan kalau TP & SL tersentuh di bar yang sama).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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


def _resolve_one(rec: dict, bars: list[dict], horizon: int = HORIZON_1H) -> bool:
    """Update rec['status'] in-place. Return True kalau status berubah."""
    if rec.get("status") != "active":
        return False
    d = rec["direction"]
    entry_t = rec["created_at"]
    entry, sl, tp1 = rec["levels"]["entry"], rec["levels"]["sl"], rec["levels"]["tp1"]
    after = [b for b in bars if b["t"] > entry_t]
    window = after[:horizon]
    for b in window:
        hit_sl = b["l"] <= sl if d == 1 else b["h"] >= sl
        hit_tp = b["h"] >= tp1 if d == 1 else b["l"] <= tp1
        if hit_sl:  # konservatif: SL dianggap duluan
            rec.update({"status": "loss", "resolved_at": b["t"]})
            return True
        if hit_tp:
            rec.update({"status": "win", "resolved_at": b["t"]})
            return True
    if len(after) >= horizon:
        rec.update({"status": "timeout", "resolved_at": window[-1]["t"]})
        return True
    return False  # masih berjalan


def resolve_pending(tracking: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    bars = store.load("1h")
    for rec in tracking.get("history", []):
        _resolve_one(rec, bars)
    tracking["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tracking["updated_at_wib"] = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
    return tracking


def compute_stats(tracking: dict) -> dict:
    hist = tracking.get("history", [])
    wins = sum(1 for r in hist if r.get("status") == "win")
    losses = sum(1 for r in hist if r.get("status") == "loss")
    timeouts = sum(1 for r in hist if r.get("status") == "timeout")
    active = sum(1 for r in hist if r.get("status") == "active")
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
    return tracking