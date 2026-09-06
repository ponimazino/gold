"""Hourly incremental sync: append the newest candles to data/.

Local:   set TWELVEDATA_API_KEY (in .env), then `python -m analyzer.jobs.sync`
GitHub: scheduled by .github/workflows/sync.yml — hourly at minute 17, Mon-Sat UTC.

Cost: 2 requests per run (H1 + H4) -> ~50 of the 800 free credits/day.
Runs are idempotent and gap-filling: cron jitter or a skipped hour is harmless,
the next run catches up from the last stored bar.

If a timeframe file is missing, sync bootstraps itself with the most recent
5,000 bars (~8 months of H1, ~5 years of H4), so the repo is never empty even
before the deep backfill is run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import store, twelve
from ..config import INTERVALS, SYMBOL


def main() -> int:
    store.refuse_if_synthetic()
    status = {"status": "ok", "message": ""}
    for interval in INTERVALS:
        bars = store.load(interval)
        if bars:
            last = store.parse(bars[-1]["t"])
            fresh = twelve.time_series(SYMBOL, interval, start_date=last)
            print(f"[sync] {interval}: last stored {last:%Y-%m-%d %H:%M} UTC, "
                  f"fetched {len(fresh)} bars")
        else:
            fresh = twelve.time_series(SYMBOL, interval)
            print(f"[sync] {interval}: no history yet, bootstrapped "
                  f"{len(fresh)} bars (run the backfill job for 3 years)")
        payload = store.save(interval, fresh)

        gaps = store.gap_report(payload["bars"], interval)
        if gaps:
            status["status"] = "warn"
            status["message"] = (status["message"] or "") + \
                f"{interval}: {len(gaps)} gaps (last: {gaps[-1]}) "
        print(f"[sync] {interval}: total {payload['bar_count']} bars, "
              f"{len(gaps)} suspicious gaps")

    age_note = ""
    for interval in INTERVALS:
        bars = store.load(interval)
        if bars:
            last = store.parse(bars[-1]["t"])
            age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            print(f"[sync] {interval}: newest bar is {age_h:.1f}h old")
            if age_h > 24 and datetime.now(timezone.utc).weekday() < 5:
                age_note += f"{interval} stale {age_h:.0f}h "
    if age_note and status["status"] == "ok":
        status = {"status": "warn", "message": f"stale data: {age_note}"}

    store.write_meta(status)
    print(f"[sync] done, status={status['status']} {status['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())