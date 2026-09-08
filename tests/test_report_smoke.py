"""Smoke test job laporan harian (EOD): unduh data live dari repo ke folder
sementara, jalankan report.build(), pastikan PDF + index.json terbentuk.

Run: python tests/test_report_smoke.py
"""

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import store  # noqa: E402
from analyzer.jobs import report  # noqa: E402

BASE = "https://raw.githubusercontent.com/ponimazino/gold/main/data"


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    store.DATA_DIR = tmp

    files = ["meta.json", "xauusd_1h.json", "xauusd_4h.json", "patterns.json",
             "tracking.json", "recommendation.json", "calendar.json", "news.json"]
    for name in files:
        try:
            (tmp / name).write_bytes(
                urllib.request.urlopen(f"{BASE}/{name}", timeout=30).read())
            print(f"  fetched {name}")
        except Exception as exc:  # offline -> job tetap harus jalan
            print(f"  skip {name}: {exc}")

    out = report.build()
    pdf = tmp / "reports" / out["pdf"]
    assert pdf.exists() and pdf.stat().st_size > 5_000, pdf
    index = json.loads((tmp / "reports" / "index.json").read_text(encoding="utf-8"))
    assert index["files"][0]["name"] == out["pdf"], index
    assert index["updated_at_wib"], index
    print(f"\nOK: {out['pdf']} ({out['entry']['kb']} KB), "
          f"index.json {len(index['files'])} entri")
    print(f"  lokasi sementara: {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())