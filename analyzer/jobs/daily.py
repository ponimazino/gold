"""Job harian (P5): statistik pola -> rekomendasi hari ini -> nilai hasil kemarin.

Local:  python -m analyzer.jobs.daily
GitHub: workflow daily.yml, cron 22:37 UTC = 05:37 WIB.

Urutan langkah (penting):
  1. refresh statistik pola (walk-forward — selalu pakai data sampai detik ini)
  2. resolve rekomendasi lama yang masih active vs pergerakan aktual
  3. buat rekomendasi baru -> recommendation.json (UI membaca file ini)
  4. kalau status "entry", catat ke tracking.json (besok dinilai: win/loss/timeout)

Tanpa notifikasi — output hidup di web (PWA), dibuka user kapan pun.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import recommend, store, track
from ..backtest import DEFAULT_HORIZON
from ..config import INTERVALS
from . import research

WIB = ZoneInfo("Asia/Jakarta")


def _today_wib(now: datetime) -> str:
    return now.astimezone(WIB).strftime("%Y-%m-%d")


def main() -> int:
    now = datetime.now(timezone.utc)

    # 1. refresh statistik pola (payload dipakai rekomendasi sebagai confidence)
    all_results: list[dict] = []
    horizon_map = {tf: DEFAULT_HORIZON[tf] for tf in INTERVALS}
    for tf in INTERVALS:
        all_results.extend(research.run(tf, horizon_map[tf]))
    if all_results:
        store.write_json("patterns.json", research.build_payload(all_results, horizon_map))
        print("[daily] statistik pola diperbarui")
    else:
        print("[daily] data harga belum ada — lewati research")

    # 2. nilai rekomendasi lama dulu (feedback loop)
    tracking = track.load_tracking()
    track.resolve_pending(tracking, now=now)

    # 3. rekomendasi hari ini
    rec = recommend.build_recommendation(now=now)
    store.write_json("recommendation.json", rec)

    # 4. catat ke history kalau actionable dan belum ada entry hari ini
    if rec["status"] == "entry":
        today = _today_wib(now)
        if any(r.get("id") == today for r in tracking["history"]):
            print("[daily] rekomendasi hari ini sudah tercatat — tidak dobel")
        else:
            tracking["history"].append(rec)
            print("[daily] rekomendasi entry dicatat ke tracking")
    else:
        print(f"[daily] status '{rec['status']}' — tidak ada posisi baru")

    track.compute_stats(tracking)
    tracking["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tracking["updated_at_wib"] = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
    store.write_json("tracking.json", tracking)

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