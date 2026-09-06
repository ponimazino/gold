"""Generate SYNTHETIC XAU/USD candles into data/ for local UI development (P2).

⚠ Data ini PALSU (random walk) — hanya untuk mengembangkan/mengetes chart
tanpa API key. JANGAN PERNAH commit data hasil generator ini, dan HAPUS
folder data/ sebelum menjalankan backfill/sync yang asli:

    python tools/make_synth_data.py     # buat data palsu untuk dev UI
    rm -rf data                         # bersihkan SEBELUM data asli masuk
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import store  # noqa: E402


def main() -> int:
    random.seed(42)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=90)

    h1: list[dict] = []
    price = 4000.0
    t = start
    while t <= now:
        # gold: tutup weekend (Jum 22:00 UTC - Min 22:00 UTC) + istirahat harian 21-22 UTC
        if not (t.weekday() == 5 or (t.weekday() == 6 and t.hour < 22)):
            if not (t.hour == 21):
                o = price
                drift = random.gauss(0, 3.5)
                c = o + drift
                h = max(o, c) + abs(random.gauss(0, 1.5))
                l = min(o, c) - abs(random.gauss(0, 1.5))
                h1.append({
                    "t": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "o": round(o, 2), "h": round(h, 2),
                    "l": round(l, 2), "c": round(c, 2), "v": None,
                })
                price = c
        t += timedelta(hours=1)

    h4 = []
    for i in range(0, len(h1), 4):
        chunk = h1[i:i + 4]
        if chunk:
            h4.append({
                "t": chunk[0]["t"],
                "o": chunk[0]["o"],
                "h": max(b["h"] for b in chunk),
                "l": min(b["l"] for b in chunk),
                "c": chunk[-1]["c"],
                "v": None,
            })

    store.save("1h", h1)
    store.save("4h", h4)
    store.write_meta({"status": "ok", "message": "SYNTHETIC DEV DATA — delete before real backfill"})
    print(f"[synth] {len(h1)} bar H1, {len(h4)} bar H4 -> data/")
    print("[synth] INGAT: rm -rf data sebelum backfill asli!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())