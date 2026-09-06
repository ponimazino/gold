"""Twelve Data client for XAU/USD candles.

API reference: https://twelvedata.com/docs#time-series
- 1 credit per time_series request (free plan: 800 credits/day)
- max 5,000 data points per request -> fetch_history paginates backwards
- forex datetimes come back in UTC when timezone=UTC, newest-first
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import requests

from .config import MAX_OUTPUT_SIZE

BASE_URL = "https://api.twelvedata.com"
TIMEOUT = 30
MAX_RETRIES = 3


class TwelveDataError(RuntimeError):
    pass


def get_api_key() -> str:
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        raise TwelveDataError(
            "TWELVEDATA_API_KEY is not set. Put it in .env locally, "
            "or in GitHub repo Secrets for Actions runs."
        )
    return key


def parse_ts(value: str) -> datetime:
    """Parse a stored/ISO UTC timestamp ('2026-09-06T01:00:00Z')."""
    return datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))


def time_series(
    symbol: str,
    interval: str,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    outputsize: int = MAX_OUTPUT_SIZE,
) -> list[dict]:
    """Single request. Returns normalized bars oldest-first.

    Each bar: {t: 'YYYY-MM-DDTHH:MM:SSZ', o, h, l, c, v} — floats, v may be None
    (Twelve Data forex has no volume).
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "timezone": "UTC",
        "apikey": get_api_key(),
    }
    if start_date:
        params["start_date"] = start_date.strftime("%Y-%m-%d %H:%M:%S")
    if end_date:
        params["end_date"] = end_date.strftime("%Y-%m-%d %H:%M:%S")

    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(f"{BASE_URL}/time_series", params=params, timeout=TIMEOUT)
        if resp.status_code == 429:  # 8 credits/min on the free plan
            wait = 15 * attempt
            print(f"[twelve] 429 rate limited, waiting {wait}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        payload = resp.json()
        if payload.get("status") == "error":
            raise TwelveDataError(payload.get("message", "unknown Twelve Data error"))
        return _normalize(payload.get("values") or [])
    raise TwelveDataError("still rate limited after retries; try again next minute")


def _normalize(values: list[dict]) -> list[dict]:
    bars = []
    for v in values:
        try:
            bars.append({
                "t": datetime.strptime(v["datetime"][:19], "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"]),
                "v": float(v["volume"]) if v.get("volume") else None,
            })
        except (KeyError, ValueError) as exc:
            raise TwelveDataError(f"unexpected bar payload: {v!r} ({exc})") from exc
    bars.reverse()  # API returns newest-first; we store oldest-first
    return bars


def fetch_history(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime | None = None,
    *,
    pause_s: float = 8.0,
) -> list[dict]:
    """Fetch all bars covering [start, end], paging backwards past the
    5,000-point cap. 1 credit per page, throttled to stay under 8/min."""
    end = end or datetime.now(timezone.utc)
    collected: dict[str, dict] = {}
    window_end = end
    for _ in range(20):  # safety cap: 20 pages far exceeds 3 years of H1
        chunk = time_series(symbol, interval, end_date=window_end)
        if not chunk:
            break
        for bar in chunk:
            collected[bar["t"]] = bar
        oldest = parse_ts(chunk[0]["t"])
        if oldest <= start:
            break
        if oldest >= window_end:  # no forward progress -> avoid an infinite loop
            break
        print(f"[twelve] {interval}: page done, oldest so far "
              f"{oldest:%Y-%m-%d %H:%M} UTC, paging back...")
        window_end = oldest
        time.sleep(pause_s)
    bars = sorted(collected.values(), key=lambda b: b["t"])
    return [b for b in bars if parse_ts(b["t"]) >= start]