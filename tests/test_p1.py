"""Smoke test P1 tanpa API key: parsing, merge, gap heuristic, atomic write.

Run: python tests/test_p1.py
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import store, twelve  # noqa: E402


def _isolate_data_dir() -> None:
    """Test ini tidak boleh tergantung isi data/ yang mungkin sudah ada
    (mis. hasil make_synth_data.py) — arahkan storage ke temp dir."""
    store.DATA_DIR = Path(tempfile.mkdtemp())


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def bar(dt: datetime, close: float) -> dict:
    return {"t": iso(dt), "o": close, "h": close, "l": close, "c": close, "v": None}


def main() -> int:
    _isolate_data_dir()
    base = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)  # a Tuesday

    # --- twelve._normalize: API order (newest-first) -> stored oldest-first
    values = [
        {"datetime": "2026-09-01 02:00:00", "open": "3", "high": "4", "low": "2", "close": "3.5"},
        {"datetime": "2026-09-01 01:00:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
    ]
    bars = twelve._normalize(values)
    assert bars[0]["t"] == "2026-09-01T01:00:00Z", bars
    assert bars[0]["c"] == 1.5 and bars[1]["c"] == 3.5, bars
    print("ok: _normalize sorts oldest-first and parses floats")

    # --- store.save: merge + dedupe (newer close overwrites the same bar)
    store.save("1h", [bar(base, 100), bar(base + timedelta(hours=1), 101)])
    store.save("1h", [bar(base + timedelta(hours=1), 999),   # overwrite in-progress bar
                      bar(base + timedelta(hours=2), 103)])
    bars = store.load("1h")
    assert len(bars) == 3, bars
    assert bars[1]["c"] == 999, bars
    assert [b["c"] for b in bars] == [100, 999, 103]
    print("ok: save merges, dedupes and overwrites the forming bar")

    # --- gap_report: weekend (Fri->Mon) is fine, in-week holes get flagged
    fri = datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)   # Friday
    mon = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)   # next Monday
    bars = [bar(fri + timedelta(hours=i), 100 + i) for i in range(5)]
    bars += [bar(mon + timedelta(hours=i), 200 + i) for i in range(5)]
    gaps = store.gap_report(bars, "1h")
    assert gaps == [], gaps                                # Fri->Mon weekend is expected
    tue_hole = datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)  # Tuesday island, 22h after Mon 05:00
    bars.append(bar(tue_hole, 50))
    gaps = store.gap_report(bars, "1h")
    assert len(gaps) == 1, gaps                            # Mon->Tue hole must be flagged
    print("ok: gap_report flags in-week holes but skips weekend reopen")

    # --- meta.json written and readable
    store.write_meta({"status": "ok", "message": "test"})
    meta = json.loads((store.file_path("1h")).with_name("meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "ok" and "coverage" in meta and "updated_at_wib" in meta, meta
    print(f"ok: meta.json written (wib={meta['updated_at_wib']})")

    # --- parse round-trip
    assert store.parse("2026-09-01T01:00:00Z") == datetime(2026, 9, 1, 1, tzinfo=timezone.utc)
    print("ok: timestamp parse round-trip")

    print("\nALL P1 SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())