"""Job mingguan: laporan PDF detail untuk review manual.

Isi: ringkasan eksekutif, pergerakan harga mingguan + grafik, statistik
backtest per pola, pembacaan pola berdasarkan history, hasil feedback loop
(rekomendasi vs harga aktual), track event 3-star minggu depan, dan
sorotan berita.

Local:  python -m analyzer.jobs.report
GitHub: workflow report.yml, cron 21:23 UTC Minggu = 04:23 WIB Senin.
Output: data/reports/weekly-YYYY-MM-DD.pdf (tanggal WIB saat dibuat)
        + data/reports/index.json (maksimal 52 entri terakhir).

PDF ~100-200 KB/minggu; biarkan terakumulasi di repo (setahun ~10 MB).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, PolyLine, String
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .. import store

WIB = ZoneInfo("Asia/Jakarta")
LIME = colors.HexColor("#6b7d2a")   # lime gelap agar terbaca di kertas
DARK = colors.HexColor("#16161c")
GRAY = colors.HexColor("#666674")
AMBER = colors.HexColor("#a06a10")

S_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17,
                      textColor=DARK, spaceAfter=2)
S_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12,
                      textColor=DARK, spaceBefore=10, spaceAfter=4)
S_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                        leading=13, textColor=DARK)
S_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=7,
                         leading=10, textColor=GRAY)
S_KICK = ParagraphStyle("kick", fontName="Helvetica-Bold", fontSize=7,
                        textColor=GRAY, spaceBefore=12)

KEEP = 52  # jumlah laporan di index.json


def _read_json(name: str) -> dict:
    path = store.DATA_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _wib(dt: datetime) -> str:
    return dt.astimezone(WIB).strftime("%d %b %Y %H:%M WIB")


def _pct(a: float, b: float) -> str:
    if not b:
        return "-"
    return f"{(a - b) / b * 100:+.2f}%"


def _fmt_rec_status(s: str) -> str:
    return {"win": "WIN (kena TP1)", "loss": "LOSS (kena SL)",
            "timeout": "TIMEOUT", "active": "berjalan",
            "entry": "baru dicatat"}.get(s, s)


def _table(data: list[list], widths: list[float], align_right_from: int = 1) -> Table:
    t = Table(data, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#33333c")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, DARK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#d8d8de")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for col in range(align_right_from, len(widths)):
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _price_chart(bars: list[dict], width: float, height: float) -> Drawing:
    """Garis sederhana close H4 (4 minggu terakhir) tanpa dependensi berat."""
    closes = [b["c"] for b in bars]
    d = Drawing(width, height)
    if len(closes) < 2:
        d.add(String(2, height / 2, "data belum cukup", fontName="Helvetica",
                     fontSize=8, fillColor=GRAY))
        return d
    lo, hi = min(closes), max(closes)
    if hi == lo:
        hi = lo + 1.0
    pad = 14
    n = len(closes)
    pts: list[float] = []
    for i, c in enumerate(closes):
        x = pad + (width - 2 * pad) * i / (n - 1)
        y = pad + (height - 2 * pad) * (c - lo) / (hi - lo)
        pts += [x, y]
    d.add(PolyLine(pts, strokeColor=LIME, strokeWidth=1.1))
    d.add(String(2, height - pad, f"{hi:,.0f}", fontName="Helvetica",
                 fontSize=7, fillColor=GRAY))
    d.add(String(2, pad - 3, f"{lo:,.0f}", fontName="Helvetica",
                 fontSize=7, fillColor=GRAY))
    d.add(String(pad, height - 8, bars[0]["t"][:10], fontName="Helvetica",
                 fontSize=6, fillColor=GRAY))
    d.add(String(width - 46, height - 8, bars[-1]["t"][:10],
                 fontName="Helvetica", fontSize=6, fillColor=GRAY))
    return d


def _week_summary(bars: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    """Statistik minggu berjalan (7 hari) + bar untuk grafik 4 minggu."""
    cutoff = now - timedelta(days=7)
    chart_cutoff = now - timedelta(days=28)
    week = [b for b in bars if store.parse(b["t"]) >= cutoff]
    chart = [b for b in bars if store.parse(b["t"]) >= chart_cutoff]
    prev = [b for b in bars if store.parse(b["t"]) < cutoff]
    if not week:
        return {}, chart
    summary = {
        "open": week[0]["o"],
        "high": max(b["h"] for b in week),
        "low": min(b["l"] for b in week),
        "close": week[-1]["c"],
        "prev_close": prev[-1]["c"] if prev else None,
    }
    return summary, chart


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    bars1h = store.load("1h")
    bars4h = store.load("4h")
    patterns = _read_json("patterns.json")
    tracking = _read_json("tracking.json")
    rec = _read_json("recommendation.json")
    calendar = _read_json("calendar.json")
    news = _read_json("news.json")

    week, chart_bars = _week_summary(bars4h, now)
    stats = tracking.get("stats", {})
    results = sorted(
        patterns.get("results", []),
        key=lambda r: (r.get("tf", ""), -(r.get("oos_win_rate") or 0)))

    # ---------- isi PDF ----------
    story: list = []
    story.append(Paragraph("GOLDPULSE - LAPORAN MINGGUAN XAUUSD", S_H1))
    story.append(Paragraph(
        f"Dibuat {_wib(now)}  |  Periode laporan: "
        f"{(now - timedelta(days=7)).astimezone(WIB).strftime('%d %b')} - "
        f"{now.astimezone(WIB).strftime('%d %b %Y')}  |  Semua waktu WIB", S_SMALL))
    story.append(HRFlowable(width="100%", thickness=0.8, color=DARK,
                            spaceBefore=4, spaceAfter=8))

    # 1. Ringkasan eksekutif
    status = rec.get("status", "-")
    bias = rec.get("bias", "-")
    conf = rec.get("confidence")
    conf_txt = f"{round(conf * 100)}%" if isinstance(conf, float) else "belum ada"
    trend = (rec.get("h4_context") or {}).get("trend", "-")
    if week:
        chg = _pct(week["close"],
                   week["prev_close"] or week["open"])
        exec_txt = (
            f"Minggu ini XAUUSD bergerak dari {week['open']:,.2f} ke "
            f"{week['close']:,.2f} ({chg} terhadap close minggu lalu), "
            f"tertinggi {week['high']:,.2f} dan terendah {week['low']:,.2f}. "
            f"Tren H4 saat ini: {trend}. Rekomendasi terbaru ({rec.get('id', '-')}): "
            f"status <b>{status.upper()}</b>, bias {bias}, peluang historis {conf_txt}. ")
    else:
        exec_txt = "Data harga minggu ini belum tersedia. "
    hit = stats.get("hit_rate")
    if hit is not None:
        exec_txt += (f"Rekam jejak feedback loop: {round(hit * 100)}% rekomendasi "
                     f"teresolve kena TP1 ({stats.get('wins', 0)}/{stats.get('resolved', 0)}).")
    else:
        exec_txt += ("Feedback loop belum punya sampel teresolve yang cukup - "
                     "laporan berikutnya akan mulai memuat hit-rate aktual.")
    story.append(Paragraph("1. RINGKASAN EKSEKUTIF", S_H2))
    story.append(Paragraph(exec_txt, S_BODY))

    # 2. Grafik harga
    story.append(Paragraph("2. PERGERAKAN HARGA (4 MINGGU TERAKHIR, CLOSE H4)", S_H2))
    if chart_bars:
        story.append(_price_chart(chart_bars[-168:], 170 * mm, 52 * mm))
        story.append(Spacer(1, 6))

    # 3. Statistik backtest
    story.append(Paragraph("3. STATISTIK BACKTEST PER POLA (HASIL TERBARU)", S_H2))
    if results:
        head = ["TF", "Pola", "n", "Resolved", "Win%", "OOS n",
                "OOS Win%", "Avg R"]
        rows = [head]
        for r in results:
            rows.append([
                r.get("tf", "-").upper(), r.get("pattern", "-"),
                r.get("n", 0), r.get("resolved", 0),
                f"{round((r.get('win_rate') or 0) * 100, 1)}",
                r.get("oos_n", 0),
                f"{round((r.get('oos_win_rate') or 0) * 100, 1)}"
                if r.get("oos_win_rate") is not None else "-",
                f"{r.get('avg_r'):+.2f}" if r.get("avg_r") is not None else "-",
            ])
        story.append(_table(rows, [14 * mm, 42 * mm, 14 * mm, 18 * mm, 16 * mm,
                                   14 * mm, 18 * mm, 16 * mm]))
        story.append(Paragraph(
            "Aturan entry backtest: pola searah trend EMA, SL 1.0x ATR, TP 1.5x ATR, "
            "SL dianggap duluan bila TP & SL tersentuh di bar sama (konservatif). "
            "OOS = 30% data terakhir (walk-forward).", S_SMALL))
    else:
        story.append(Paragraph("Statistik pola belum tersedia.", S_BODY))

    # 4. Pembacaan pola dari history
    story.append(Paragraph("4. PEMBACAAN POLA BERDASARKAN HISTORY", S_H2))
    with_n = [r for r in results if (r.get("n") or 0) >= 50]
    if with_n:
        by_oos = sorted(with_n,
                        key=lambda r: (r.get("oos_win_rate") or 0), reverse=True)
        best = by_oos[0]
        worst = by_oos[-1]
        txt = (f"Pola dengan rekam out-of-sample terbaik: <b>{best['pattern']}</b> "
               f"{best['tf'].upper()} (win-rate OOS "
               f"{round((best.get('oos_win_rate') or 0) * 100, 1)}%, n={best.get('oos_n', 0)}). "
               f"Terlemah: {worst['pattern']} {worst['tf'].upper()} "
               f"({round((worst.get('oos_win_rate') or 0) * 100, 1)}%). ")
        over50 = [r for r in by_oos if (r.get("oos_win_rate") or 0) > 0.5]
        if over50:
            txt += ("Perlu dicatat: masih ada pola dengan win-rate di atas 50%, "
                    "namun tanpa biaya transaksi (spread/slippage) - angka nyata "
                    "selalu lebih rendah.")
        else:
            txt += ("Perlu dicatat: <b>tidak ada pola yang mencapai win-rate OOS "
                    "di atas 50%</b> - secara historis sinyal ini lebih sering gagal "
                    "daripada berhasil. Perlakukan setiap sinyal dengan ukuran risiko "
                    "kecil dan SL yang disiplin.")
        story.append(Paragraph(txt, S_BODY))
    else:
        story.append(Paragraph(
            "Belum cukup sampel pola (n>=50) untuk pembacaan yang berarti.", S_BODY))

    # 5. Feedback loop
    story.append(Paragraph("5. FEEDBACK LOOP - REKOMENDASI VS HARGA AKTUAL", S_H2))
    story.append(Paragraph(
        f"Total rekomendasi tercatat: {stats.get('total', 0)}  |  "
        f"WIN: {stats.get('wins', 0)}  |  LOSS: {stats.get('losses', 0)}  |  "
        f"TIMEOUT: {stats.get('timeouts', 0)}  |  berjalan: {stats.get('active', 0)}  |  "
        f"hit-rate TP1: {f'{round(hit * 100)}%' if hit is not None else 'belum ada'}",
        S_BODY))
    history = tracking.get("history", [])
    if history:
        rows = [["Tanggal", "Bias", "Pola", "Conf", "Status"]]
        for h in history[-12:][::-1]:
            conf_h = h.get("confidence")
            rows.append([
                (h.get("id") or "-"),
                h.get("bias", "-"),
                h.get("pattern", "-"),
                f"{round(conf_h * 100)}%" if isinstance(conf_h, float) else "-",
                _fmt_rec_status(h.get("status", "-")),
            ])
        story.append(Spacer(1, 4))
        story.append(_table(rows, [30 * mm, 24 * mm, 42 * mm, 20 * mm, 40 * mm],
                            align_right_from=3))

    # 6. Track event minggu depan
    story.append(Paragraph("6. EVENT EKONOMI 3-STAR US MINGGU DEPAN", S_H2))
    events = []
    for e in calendar.get("events", []):
        try:
            t = store.parse(e["t_utc"])
        except (KeyError, ValueError):
            continue
        if now - timedelta(hours=6) <= t <= now + timedelta(days=7):
            events.append((t, e))
    events.sort(key=lambda x: x[0])
    if events:
        rows = [["Waktu rilis (WIB)", "Event", "Forecast", "Previous", "Actual"]]
        for t, e in events[:14]:
            rows.append([
                t.astimezone(WIB).strftime("%a %d %b %H:%M"),
                e.get("title", "-"),
                e.get("forecast") or "-",
                e.get("previous") or "-",
                e.get("actual") or "-",
            ])
        story.append(_table(rows, [36 * mm, 66 * mm, 22 * mm, 22 * mm, 22 * mm],
                            align_right_from=2))
        story.append(Paragraph(
            "Jeda entry otomatis: 2 jam sebelum sampai 1 jam setelah setiap rilis di atas.",
            S_SMALL))
    else:
        story.append(Paragraph(
            "Tidak ada event 3-star US terjadwal dalam 7 hari ke depan "
            "(berdasarkan kalender tersimpan).", S_BODY))

    # 7. Sorotan berita
    story.append(Paragraph("7. SOROTAN BERITA TERVALIDASI (7 HARI TERAKHIR)", S_H2))
    items = []
    for n in news.get("items", []):
        ts = n.get("published_utc")
        if not ts:
            continue
        try:
            t = store.parse(ts)
        except ValueError:
            continue
        if t >= now - timedelta(days=7):
            items.append(n)
    if items:
        rows = [["Tanggal (WIB)", "Sumber", "Judul"]]
        for n in items[:10]:
            t = store.parse(n["published_utc"]).astimezone(WIB)
            rows.append([t.strftime("%d %b %H:%M"), n.get("source", "-"),
                         (n.get("title") or "-")[:95]])
        story.append(_table(rows, [26 * mm, 30 * mm, 114 * mm], align_right_from=99))
    else:
        story.append(Paragraph(
            "Tidak ada berita whitelist dalam 7 hari terakhir.", S_BODY))

    # 8. Disclaimer
    story.append(Paragraph("DISCLAIMER", S_KICK))
    story.append(Paragraph(
        "Laporan ini dibuat otomatis dari data historis dan statistik probabilitas. "
        "Kinerja masa lalu tidak menjamin hasil masa depan. Backtest tidak "
        "memodelkan spread, slippage, dan biaya eksekusi lainnya. Bukan saran "
        "finansial - keputusan trading sepenuhnya tanggung jawab Anda.", S_SMALL))

    # ---------- tulis PDF + index ----------
    out_dir = store.DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_wib = now.astimezone(WIB).strftime("%Y-%m-%d")
    pdf_name = f"weekly-{date_wib}.pdf"
    pdf_path = out_dir / pdf_name
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"GoldPulse Weekly {date_wib}",
        author="goldpulse analyzer")

    def _footer(canvas, _doc):
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(18 * mm, 9 * mm,
                          f"GoldPulse weekly report {date_wib} - bukan saran finansial")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm,
                               f"Hal. {canvas.getPageNumber()}")

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

    index_path = out_dir / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {"updated_at_wib": None, "files": []}
    entry = {
        "name": pdf_name,
        "date_wib": _wib(now),
        "kb": round(pdf_path.stat().st_size / 1024),
        "summary": (f"XAUUSD {week['close']:,.2f}" if week else "laporan mingguan")
                   + (f" | status {status} bias {bias}" if rec else ""),
        "rec_status": status,
    }
    index["files"] = [f for f in index.get("files", [])
                      if f.get("name") != pdf_name][:KEEP - 1]
    index["files"].insert(0, entry)
    index["updated_at_wib"] = _wib(now)
    store._atomic_write(index_path, index)

    print(f"[report] {pdf_name} ({entry['kb']} KB) -> data/reports/")
    print(f"[report] index.json: {len(index['files'])} entri (maks {KEEP})")
    return {"pdf": pdf_name, "entry": entry}


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())