"""Job harian (P5): statistik pola -> rekomendasi hari ini -> nilai hasil kemarin.

Local:  python -m analyzer.jobs.daily
GitHub: workflow daily.yml, grid 1 jam "37 0-15 * * 1-5" + "37 21-23 * * 0-4" (04:37-22:37 WIB, Sen-Jum).

Urutan langkah (penting):
  1. refresh statistik pola (walk-forward — selalu pakai data sampai detik ini)
  2. resolve rekomendasi lama yang masih active vs pergerakan aktual
  3. buat rekomendasi baru -> recommendation.json (UI membaca file ini)
  4. kalau status "entry", catat ke tracking.json HANYA kalau tidak ada
     posisi aktif (aturan 2026-09-10: posisi terakhir harus sudah selesai
     TP1/SL/timeout — boleh re-entry sehari yang sama, tidak ada overlap)
  5. catat slot run ini ke rec_log.json (jam WIB + status — supaya riwayat
     harian per 1 jam lengkap, jam tanpa setup kelihatan 'skip')
  6. web push (PWA, Web Push VAPID): notif entry + agenda event 3★ (H-3, 1x/hari).
     Butuh secrets PUSH_* di workflow — kalau kosong, dilewati tanpa error.
  7. WhatsApp via Fonnte (2026-09-20, kanal tambahan): pesan yang sama persis
     (entry + agenda, dedup terpisah di data/wa_state.json). Butuh secrets
     FONNTE_TOKEN + FONNTE_TARGET — kalau kosong, dilewati tanpa error.

Notifikasi = Web Push PWA + WhatsApp Fonnte (permintaan user 2026-09-09 +
2026-09-20); Telegram tetap terlarang. Output utama tetap di web, dibuka user
kapan pun.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import push, recommend, reclog, store, track, wa
from ..backtest import DEFAULT_HORIZON
from ..config import INTERVALS
from . import research

WIB = ZoneInfo("Asia/Jakarta")
# Cooldown re-entry searah = COOLDOWN_BARS research (2 bar H1). Statistik
# gate (n, EV, win-rate) dihitung dari kolam sinyal yang SUDAH didedup
# 2 bar — live tanpa cooldown jauh lebih agresif dari basis statistiknya
# sendiri (audit 2026-09-20: 3 loss eksperimen berturut-turut dalam 3 jam
# dari pola yang sama arah, tiap jam langsung re-entry setelah SL).
# 4 -> 2 jam (2026-09-21, audit frekuensi): dedup 2 bar diuji di data
# 3 tahun — sinyal +27%, EV net OOS stabil/naik (inside_bar long +0.150R
# -> +0.182R); biayanya DD 11R -> 16R & streak 9 -> 14 (diterima user
# sebagai naik risiko yang masih aman demi frekuensi entry).
COOLDOWN_HOURS = 2


def _today_wib(now: datetime) -> str:
    return now.astimezone(WIB).strftime("%Y-%m-%d")


def record_entry(rec: dict, tracking: dict) -> bool:
    """Catat entry baru ke history — HANYA kalau tidak ada posisi aktif.

    Aturan (user, 2026-09-10): sinyal entry boleh dicatat kapan pun asal
    posisi terakhir sudah selesai (win/loss/timeout) — boleh re-entry
    sehari yang sama; yang dilarang hanya posisi OVERLAP. Kalau masih ada
    posisi aktif, sinyal tetap tampil di recommendation.json (pembacaan
    pasar terkini) tapi tidak dinilai feedback loop — diberi catatan di
    rationale supaya UI menjelaskan kenapa.
    """
    if rec.get("status") != "entry":
        return False
    if any(r.get("status") in ("active", "entry") for r in tracking.get("history", [])):
        rec.setdefault("rationale", []).append(
            "Ada posisi sebelumnya yang masih berjalan — sinyal entry ini "
            "TIDAK dicatat ke feedback loop; entry baru baru boleh dicatat "
            "setelah posisi aktif selesai (TP1 / SL / timeout).")
        return False
    tracking.setdefault("history", []).append(rec)
    return True


def apply_cooldown(rec: dict, tracking: dict, now: datetime) -> bool:
    """Tunda sinyal entry searah yang muncul < COOLDOWN_HOURS dari sinyal
    searah sebelumnya (dicatat di tracking['last_signal'], dicatat JUGA untuk
    sinyal yang tidak jadi posisi — persis dedup research yang menghitung
    sinyal, bukan posisi). Sinyal yang tertunda cooldown TIDAK memperbarui
    last_signal: dedup research menghitung jarak dari sinyal yang DIPAKAI.
    Return True kalau entry ini tertunda."""
    if rec.get("status") != "entry":
        return False
    last = tracking.get("last_signal") or {}
    if last.get("d") != rec.get("direction") or not last.get("t"):
        return False
    age_h = (now - store.parse(last["t"])).total_seconds() / 3600
    if age_h >= COOLDOWN_HOURS:
        return False
    rec["status"] = "tunggu"
    rec["cooldown"] = True
    rec.setdefault("rationale", []).append(
        f"COOLDOWN {COOLDOWN_HOURS} bar H1: sinyal searah terakhir baru "
        f"{age_h:.1f} jam lalu — entry ditunda (konsisten dedup {COOLDOWN_HOURS} bar yang "
        f"jadi dasar statistik gate, cegah re-entry beruntun setelah SL)")
    return True


def main() -> int:
    now = datetime.now(timezone.utc)

    # 1. refresh statistik pola (payload dipakai rekomendasi sebagai confidence)
    #    EMPAT rule sekaligus: (standar, mode aman) x (gross, net of cost) —
    #    sinyal dedup + indikator dihitung sekali per tf.
    all_results: list[dict] = []
    safe_results: list[dict] = []
    cost_results: list[dict] = []
    safe_cost_results: list[dict] = []
    horizon_map = {tf: DEFAULT_HORIZON[tf] for tf in INTERVALS}
    for tf in INTERVALS:
        std, safe, cost, safe_cost = research.run_all(tf, horizon_map[tf])
        all_results.extend(std)
        safe_results.extend(safe)
        cost_results.extend(cost)
        safe_cost_results.extend(safe_cost)
    if all_results:
        store.write_json("patterns.json",
                         research.build_payload(all_results, horizon_map,
                                                safe_results=safe_results,
                                                cost_results=cost_results,
                                                safe_cost_results=safe_cost_results))
        print("[daily] statistik pola diperbarui (standar + aman, gross + net)")
    else:
        print("[daily] data harga belum ada — lewati research")

    # 2. nilai rekomendasi lama dulu (feedback loop)
    tracking = track.load_tracking()
    track.resolve_pending(tracking, now=now)

    # 3. rekomendasi hari ini (pembacaan pasar terkini — dioverwrite tiap run)
    rec = recommend.build_recommendation(now=now)

    # 3b. cooldown re-entry searah (konsisten dedup research 4 bar). Sinyal
    # yang lolos cooldown dicatat waktunya di last_signal — WALAU nanti
    # tidak jadi posisi (posisi lain aktif), supaya cooldown menghitung
    # sinyal, bukan posisi (persis asumsi statistik gate).
    apply_cooldown(rec, tracking, now)
    if rec.get("status") == "entry":
        tracking["last_signal"] = {"d": rec["direction"], "t": rec["created_at"]}

    # 4. catat entry baru ke history (aturan: posisi terakhir harus selesai).
    #    rec ditulis SETELAH ini supaya catatan rationale "masih ada posisi
    #    aktif" ikut tampil di UI.
    entry_recorded = record_entry(rec, tracking)
    if entry_recorded:
        print(f"[daily] rekomendasi entry dicatat ke tracking "
              f"(total history {len(tracking['history'])})")
    elif rec["status"] == "entry":
        print("[daily] masih ada posisi aktif — entry baru tidak dicatat")
    else:
        print(f"[daily] status '{rec['status']}' — tidak ada posisi baru")
    store.write_json("recommendation.json", rec)

    track.compute_stats(tracking)
    tracking["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tracking["updated_at_wib"] = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
    store.write_json("tracking.json", tracking)

    # 5. slot log: riwayat per-run (jam WIB + status) untuk UI Riwayat
    try:
        reclog.log_run(rec, now=now, entry_recorded=entry_recorded)
    except Exception as exc:  # log = bonus, jangan gagalkan analisa
        print(f"[daily] rec_log error (tidak fatal): {exc}")

    # 6. web push: notif entry + agenda event (dedup di push_state.json).
    #    Notif entry hanya untuk entry yang BENAR dicatat ke feedback loop
    #    (posisi aktif -> tidak ada notif entry baru).
    try:
        push.dispatch(rec, now=now, entry_recorded=entry_recorded)
    except Exception as exc:  # notif = bonus, jangan gagalkan analisa
        print(f"[daily] push error (tidak fatal): {exc}")

    # 6b. WhatsApp Fonnte: pesan entry + agenda yang sama (dedup terpisah
    #     di wa_state.json — gagal satu kanal tidak membatalkan kanal lain).
    try:
        wa.dispatch(rec, now=now, entry_recorded=entry_recorded)
    except Exception as exc:  # notif = bonus, jangan gagalkan analisa
        print(f"[daily] wa error (tidak fatal): {exc}")

    s = tracking["stats"]
    print(f"[daily] bias: {rec['bias']}  status: {rec['status']}  "
          f"confidence: {rec['confidence']}")
    print(f"[daily] tracking: {s.get('total', 0)} rekomendasi, "
          f"hit-rate TP1 {s.get('hit_rate')} "
          f"(win {s.get('wins', 0)}/{s.get('resolved', 0)})")
    for line in rec["rationale"]:
        print(f"[daily]   - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())