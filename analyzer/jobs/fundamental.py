"""Collector fundamental: kalender 3★ US + berita kredibel.

Local:  python -m analyzer.jobs.fundamental
GitHub: .github/workflows/fundamental.yml — tiap 3 jam (menit :13, 24 mnt sebelum job daily).
Output: data/calendar.json + data/news.json
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import calendar as cal
from .. import news
from .. import store

WIB = ZoneInfo("Asia/Jakarta")


def main() -> int:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp_wib = now.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")

    events, source, note = cal.collect_calendar()
    store.write_json("calendar.json", {
        "updated_at": stamp,
        "updated_at_wib": stamp_wib,
        "source": source,
        "note": note,
        "blackout_hours": {"before": cal.BLACKOUT_BEFORE_H, "after": cal.BLACKOUT_AFTER_H},
        "events": events,
    })
    print(f"[fundamental] kalender: {len(events)} event via {source} ({note})")

    up = cal.upcoming(events, hours=48, now=now)
    for e in up[:5]:
        print(f"[fundamental]   dalam 48 jam: {e['t_utc']} {e['title']}")
    blk = cal.active_blackout(events, now=now)
    if blk:
        print(f"[fundamental] [!] sedang dalam jeda entry: {blk['event']['title']} "
              f"({blk['minutes_to_event']:+d} menit dari rilis)")

    items = news.collect_news(now)
    store.write_json("news.json", {
        "updated_at": stamp,
        "updated_at_wib": stamp_wib,
        "items": items,
    })
    print(f"[fundamental] berita: {len(items)} item kredibel (<36 jam)")

    print("[fundamental] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())