"""One-time deep backfill: ~3 years of H1 + H4 XAU/USD from Twelve Data.

Local:  python -m analyzer.jobs.backfill --years 3
GitHub: Actions tab -> "Backfill" -> Run workflow (manual dispatch).

Cost: ~5-8 requests total (each page returns up to 5,000 bars), well inside
the 800 free credits/day. Existing stored bars are merged, never overwritten.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .. import store, twelve
from ..config import DEFAULT_BACKFILL_YEARS, INTERVALS, SYMBOL


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep backfill of XAU/USD history")
    parser.add_argument("--years", type=float, default=DEFAULT_BACKFILL_YEARS,
                        help="how far back to fetch (default: 3)")
    parser.add_argument("--intervals", nargs="*", default=INTERVALS,
                        help="timeframes to backfill (default: 1h 4h)")
    args = parser.parse_args()

    store.refuse_if_synthetic()

    start = datetime.now(timezone.utc) - timedelta(days=365.25 * args.years)
    print(f"[backfill] {SYMBOL} since {start:%Y-%m-%d} for {args.intervals}")

    for interval in args.intervals:
        bars = twelve.fetch_history(SYMBOL, interval, start)
        if not bars:
            print(f"[backfill] {interval}: API returned nothing, skipping")
            continue
        payload = store.save(interval, bars)
        gaps = store.gap_report(payload["bars"], interval)
        print(f"[backfill] {interval}: fetched {len(bars)} bars, "
              f"total stored {payload['bar_count']}, "
              f"suspicious gaps: {len(gaps)}")
        for g in gaps:
            print(f"[backfill]   gap: {g}")

    store.write_meta({"status": "ok",
                      "message": f"backfill {args.years}y complete"})
    print("[backfill] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())