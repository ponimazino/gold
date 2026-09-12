"""Log per-run analisa harian (slot per iterasi job daily) di
data/rec_log.json: TIAP run dicatat jam WIB + status rekomendasi
(entry / tunggu / netral) supaya riwayat harian lengkap per slot — jam
tanpa setup terlihat jelas 'skip', bukan lubang data. Tanpa log ini,
recommendation.json yang dioverwrite tiap run menghapus jejak analisa
jam-jam sebelumnya.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import store

WIB = ZoneInfo("Asia/Jakarta")
KEEP_DAYS = 30  # riwayat slot disimpan 30 hari (~19 slot/hari kerja, grid 1 jam)


def load() -> dict:
    path = store.DATA_DIR / "rec_log.json"
    if not path.exists():
        return {"updated_at": None, "days": []}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("days"), list):
            data["days"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "days": []}


def log_run(rec: dict, now: datetime | None = None,
            entry_recorded: bool = True) -> dict:
    """Catat satu run analisa. Idempotent per menit WIB: dobel-run pada
    menit yang sama menimpa slot lama, bukan menambah baris baru.
    entry_recorded=False = sinyal entry tidak jadi posisi (aturan 1 posisi
    aktif) — slot diberi catatan supaya Riwayat jujur bahwa sinyal itu
    tidak akan pernah dinilai feedback loop."""
    now = now or datetime.now(timezone.utc)
    now_wib = now.astimezone(WIB)
    date = now_wib.strftime("%Y-%m-%d")
    t_wib = now_wib.strftime("%H:%M")
    status = rec.get("status") if isinstance(rec, dict) else "netral"

    slot: dict = {"t_wib": t_wib, "status": status}
    if status == "entry":
        slot["pattern"] = rec.get("pattern")
        slot["bias"] = rec.get("bias")
        slot["confidence"] = rec.get("confidence")
        lv = rec.get("levels") or {}
        slot["levels"] = {k: lv[k] for k in ("entry", "sl", "tp1", "tp2") if k in lv}
        if rec.get("levels_safe"):
            slot["levels_safe"] = {
                k: rec["levels_safe"][k] for k in ("entry", "sl", "tp1")
                if k in rec["levels_safe"]}
        if rec.get("experiment"):
            slot["experiment"] = True
        if not entry_recorded:
            slot["note"] = ("sinyal saat posisi lain masih berjalan — "
                            "tidak dicatat/dinilai feedback loop "
                            "(aturan 1 posisi aktif)")
    else:
        # slot tanpa entry: bawa alasan gate bila ada pola yang disaring —
        # riwayat harus jujur "ada sinyal tapi kualitasnya tidak lolos",
        # bukan terlihat seperti data kosong
        gate = rec.get("gate")
        if isinstance(gate, dict) and gate.get("reason"):
            slot["note"] = f"{gate.get('pattern')}: {gate['reason']}"

    log = load()
    days = {d.get("date"): d for d in log["days"] if d.get("date")}
    day = days.setdefault(date, {"date": date, "slots": []})
    # dedup menit identik (dobel-run / rerun cepat)
    day["slots"] = [s for s in day["slots"] if s.get("t_wib") != t_wib] + [slot]

    # trim: simpan KEEP_DAYS hari terakhir saja
    if len(days) > KEEP_DAYS:
        for old in sorted(days)[: len(days) - KEEP_DAYS]:
            del days[old]

    log["days"] = sorted(days.values(), key=lambda d: d["date"], reverse=True)
    log["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    log["updated_at_wib"] = now_wib.strftime("%Y-%m-%d %H:%M WIB")
    store.write_json("rec_log.json", log)
    return log