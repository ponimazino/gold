"""JSON storage for candles, committed by Actions so the PWA reads them statically.

Files under data/:
    xauusd_1h.json / xauusd_4h.json   {"symbol", "interval", "updated_at", "bars": [...]}
    meta.json                         last sync status, coverage, gap report

All timestamps are UTC ISO strings ("2026-09-06T01:00:00Z").
WIB conversion happens only at display time, never in storage or backtests.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DATA_DIR, DISPLAY_TZ, INTERVALS, SYMBOL

WEEKEND_SKIP = timedelta(hours=48)


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def file_path(interval: str) -> Path:
    return DATA_DIR / f"xauusd_{interval}.json"


def load(interval: str) -> list[dict]:
    path = file_path(interval)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("bars", [])


def save(interval: str, bars: list[dict]) -> dict:
    """Merge with what's already stored (a commit is never lost history),
    then write atomically so a killed job can't leave a truncated file."""
    merged = {b["t"]: b for b in load(interval)}
    merged.update({b["t"]: b for b in bars})
    ordered = sorted(merged.values(), key=lambda b: b["t"])
    payload = {
        "symbol": SYMBOL,
        "interval": interval,
        "timezone": "UTC",
        "updated_at": _now_iso(),
        "bar_count": len(ordered),
        "bars": ordered,
    }
    _atomic_write(file_path(interval), payload)
    return payload


def gap_report(bars: list[dict], interval: str) -> list[str]:
    """Heuristic session-gap check.

    Gold trades ~23h/day Mon-Fri with a daily maintenance break, plus a weekend
    close — those gaps are expected. What we flag: suspicious in-week gaps
    (>= 4 consecutive missing bars Mon-Fri), so a silently failing collector
    stands out in meta.json instead of quietly corrupting the dataset.
    """
    step_h = 4 if interval == "4h" else 1
    threshold = timedelta(hours=step_h * 4)
    gaps = []
    for prev, curr in zip(bars, bars[1:]):
        t0, t1 = parse(prev["t"]), parse(curr["t"])
        delta = t1 - t0
        if delta <= threshold:
            continue
        # Monday reopen after the weekend is normal (Fri/Sat/Sun -> Mon)
        if t1.weekday() == 0 and t0.weekday() in (4, 5, 6):
            continue
        gaps.append(f"{prev['t']} -> {curr['t']} ({delta})")
    return gaps[-5:]  # keep meta.json small: last few gaps only


def write_json(filename: str, payload: dict) -> None:
    """Tulis file JSON umum ke data/ (mis. patterns.json, recommendation.json)."""
    _atomic_write(DATA_DIR / filename, payload)


def refuse_if_synthetic() -> None:
    """Abort the job if data/ still holds synthetic dev data
    (tools/make_synth_data.py). Merging real API bars into fake ones would
    silently poison the dataset forever — the merge never forgets."""
    path = DATA_DIR / "meta.json"
    if not path.exists():
        return
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if "SYNTHETIC" in (meta.get("message") or ""):
        raise SystemExit(
            "data/ masih berisi data sintetis (dari tools/make_synth_data.py).\n"
            "Hapus dulu: rm -rf data — lalu jalankan ulang job ini."
        )


def write_meta(status: dict) -> None:
    """Rewrite data/meta.json — sync status + coverage summary for the PWA."""
    coverage = {}
    for interval in INTERVALS:
        bars = load(interval)
        coverage[interval] = {
            "from": bars[0]["t"] if bars else None,
            "to": bars[-1]["t"] if bars else None,
            "bars": len(bars),
        }
    meta = {
        "symbol": SYMBOL,
        "timezone_display": DISPLAY_TZ,
        "updated_at": _now_iso(),
        "updated_at_wib": _now_wib(),
        "coverage": coverage,
    }
    meta.update(status)
    _atomic_write(DATA_DIR / "meta.json", meta)


def _atomic_write(path: Path, payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        # Windows: AV/indexer bisa mengunci file target sesaat saat ditulis
        # rapat — coba beberapa kali sebelum menyerah.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_wib() -> str:
    now_wib = datetime.now(timezone.utc).astimezone(ZoneInfo(DISPLAY_TZ))
    return now_wib.strftime("%Y-%m-%d %H:%M WIB")