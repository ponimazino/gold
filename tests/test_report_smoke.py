"""Smoke test job laporan harian (EOD): unduh data live dari repo ke folder
sementara, jalankan report.build(), pastikan PDF + index.json terbentuk.

Run: python tests/test_report_smoke.py
"""

import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import store  # noqa: E402
from analyzer.jobs import report  # noqa: E402

BASE = "https://raw.githubusercontent.com/ponimazino/gold/main/data"


def test_report_now_guard():
    """Audit 2026-09-30: workflow backup EOD terbukti dieksekusi GitHub 4-6
    jam terlambat (mendarat 00:00-05:00 WIB hari berikutnya). Build pagi
    buta harus melaporkan KEMARIN (hari trading yang barusan selesai),
    bukan hari baru yang baru berjalan 3-5 jam — supaya nama file & isi
    laporan tetap benar (dulu: daily-2026-09-29.pdf berisi akhir sesi 28
    Sep, daily-2026-09-28.pdf tidak pernah ada)."""
    # 23:30 WIB 30 Sep (16:30 UTC, jalur normal iterasi loop daily) -> sama
    now = datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc)
    assert report._report_now(now) == now
    # 04:05 WIB 1 Okt (21:05 UTC 30 Sep, backup GitHub telat) -> laporan 30 Sep
    late = datetime(2026, 9, 30, 21, 5, tzinfo=timezone.utc)
    assert report._report_now(late) == late - timedelta(days=1)
    # 05:07 WIB (22:07 UTC) -> hari yang sama lagi (bukan EOD kemarin)
    dawn = datetime(2026, 9, 30, 22, 7, tzinfo=timezone.utc)
    assert report._report_now(dawn) == dawn
    print("ok: guard tanggal EOD (pagi buta = laporan kemarin, >=05:00 WIB = hari ini)")


def main() -> int:
    test_report_now_guard()

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