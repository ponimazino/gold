"""Job harian (EOD): laporan PDF detail untuk review manual.

Isi: halaman sampul bergaya resmi (banner hitam + aksen lime + tag
CONFIDENTIAL), ringkasan eksekutif (hari ini + 7 hari), pergerakan harga
+ grafik 4 minggu, ringkasan bulan-bulan sebelumnya (price action per
bulan), statistik backtest per pola, pembacaan pola berdasarkan history,
hasil feedback loop (rekomendasi vs harga aktual), hasil rekomendasi
PER HARI (7 hari terakhir, dari log EOD tracking.json), track event
3-star 7 hari ke depan, dan sorotan berita. Setiap halaman diberi
watermark diagonal CONFIDENTIAL.

Local:  python -m analyzer.jobs.report
GitHub: workflow report.yml, cron 16:07 UTC Sen-Jum = 23:07 WIB (EOD,
        setelah slot grid daily terakhir 22:37 WIB selesai).
Output: data/reports/daily-YYYY-MM-DD.pdf (tanggal WIB saat dibuat)
        + data/reports/index.json (maksimal 90 entri terakhir).

PDF ~100-200 KB/hari; biarkan terakumulasi di repo (90 entri ~15 MB).
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
from reportlab.platypus import (Flowable, HRFlowable, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from .. import backtest, store
from .. import patterns as pat  # alias: "patterns" dipakai variabel JSON di build()
from . import research

WIB = ZoneInfo("Asia/Jakarta")
INK = colors.HexColor("#101014")     # hitam pekat (banner / header tabel)
LIME = colors.HexColor("#6b7d2a")    # lime gelap agar terbaca di kertas
LIME_BRIGHT = colors.HexColor("#a8c93f")
DARK = colors.HexColor("#16161c")
GRAY = colors.HexColor("#666674")
AMBER = colors.HexColor("#a06a10")
ZEBRA = colors.HexColor("#f0f0f3")
RULE = colors.HexColor("#d8d8de")

S_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17,
                      textColor=DARK, spaceAfter=2)
S_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11,
                     textColor=DARK, leading=14)
S_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                        leading=13.5, textColor=DARK, spaceAfter=4)
S_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=7,
                         leading=10, textColor=GRAY)
S_KICK = ParagraphStyle("kick", fontName="Helvetica-Bold", fontSize=7,
                        textColor=GRAY, spaceBefore=12)
S_BADGE = ParagraphStyle("badge", fontName="Helvetica-Bold", fontSize=10,
                         textColor=LIME_BRIGHT, alignment=1)
S_COVER_SUB = ParagraphStyle("coversub", fontName="Helvetica", fontSize=8,
                             leading=12, textColor=colors.HexColor("#b9b9c4"))

KEEP = 90  # jumlah laporan di index.json (harian ~3 bulan)


class _CoverBanner(Flowable):
    """Banner sampul: blok hitam, wordmark lime + brand-mark, subjudul,
    dan tag CONFIDENTIAL di kanan atas."""

    def __init__(self, width: float, date_txt: str, height: float = 46 * mm):
        super().__init__()
        self.width = width
        self.height = height
        self.date_txt = date_txt

    def draw(self) -> None:
        c = self.canv
        w, h = self.width, self.height
        c.saveState()
        c.setFillColor(INK)
        c.roundRect(0, 0, w, h, 3 * mm, stroke=0, fill=1)
        # brand-mark: persegi lime miring + garis gelap (replika sidebar)
        c.saveState()
        c.translate(12 * mm, h - 20 * mm)
        c.rotate(-8)
        c.setFillColor(LIME_BRIGHT)
        c.roundRect(-4 * mm, -4 * mm, 11 * mm, 11 * mm, 3 * mm,
                    stroke=0, fill=1)
        c.setStrokeColor(INK)
        c.setLineWidth(2.2)
        c.saveState()
        c.rotate(-32)
        c.line(-2.6 * mm, 0.6 * mm, 3.4 * mm, 0.6 * mm)
        c.restoreState()
        c.restoreState()
        # wordmark + judul
        c.setFillColor(LIME_BRIGHT)
        c.setFont("Helvetica-Bold", 21)
        c.drawString(24 * mm, h - 15 * mm, "GOLDPULSE")
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(24 * mm, h - 22 * mm, "LAPORAN HARIAN XAUUSD")
        # garis info di bawah banner
        c.setFillColor(colors.HexColor("#b9b9c4"))
        c.setFont("Helvetica", 7.5)
        c.drawString(24 * mm, h - 29 * mm,
                     f"Periode: {self.date_txt}  |  Semua waktu WIB (UTC+7)")
        c.drawString(24 * mm, h - 34.5 * mm,
                     "Analisis otomatis dari data historis - bukan saran finansial")
        # tag CONFIDENTIAL kanan atas
        tag_w = 36 * mm
        c.setStrokeColor(colors.HexColor("#ff7799"))
        c.setLineWidth(0.9)
        c.setFillColor(colors.HexColor("#ff7799"))
        c.roundRect(w - tag_w - 8 * mm, h - 15.5 * mm, tag_w, 7 * mm,
                    1.8 * mm, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(w - tag_w / 2 - 8 * mm, h - 13.4 * mm,
                            "CONFIDENTIAL")
        c.restoreState()


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


def _section(num: int, title: str) -> Table:
    """Header seksi: badge nomor lime di blok hitam + judul + garis aksen."""
    t = Table(
        [[Paragraph(f"{num:02d}", S_BADGE), Paragraph(title, S_H2)]],
        colWidths=[11 * mm, None], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, LIME),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, RULE),
    ]))
    return t


def _table(data: list[list], widths: list[float],
           align_right_from: int = 1) -> Table:
    """Tabel bergaya resmi: header blok hitam teks putih, zebra rows,
    angka rata kanan."""
    t = Table(data, colWidths=widths, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#33333c")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, LIME),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, DARK),
    ]
    for row in range(1, len(data)):
        if row % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), ZEBRA))
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
    """Statistik 7 hari terakhir + bar untuk grafik 4 minggu."""
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


def _day_summary(bars: list[dict], now: datetime) -> dict:
    """OHLC hari berjalan (per tanggal WIB) + close hari sebelumnya."""
    today = now.astimezone(WIB).strftime("%Y-%m-%d")
    day: list[dict] = []
    prev_close: float | None = None
    for b in bars:
        d = store.parse(b["t"]).astimezone(WIB).strftime("%Y-%m-%d")
        if d == today:
            day.append(b)
        elif d < today:
            prev_close = b["c"]  # bar terakhir sebelum hari ini
    if not day:
        return {}
    return {
        "open": day[0]["o"],
        "high": max(b["h"] for b in day),
        "low": min(b["l"] for b in day),
        "close": day[-1]["c"],
        "prev_close": prev_close,
    }


def _monthly_recap(bars: list[dict], now: datetime,
                   months: int = 6) -> tuple[list[dict], list[dict]]:
    """Ringkasan bulanan: OHLC + perubahan % per bulan WIB (6 bulan terakhir).

    Return (rows_untuk_tabel, list_bulan). Persen dihitung terhadap close
    bulan sebelumnya; bulan pertama terhadap open bulan itu sendiri.
    """
    by_month: dict[str, dict] = {}
    for b in bars:
        t = store.parse(b["t"]).astimezone(WIB)
        key = t.strftime("%Y-%m")
        m = by_month.setdefault(key, {"label": t.strftime("%b %Y"),
                                      "open": b["o"], "high": b["h"],
                                      "low": b["l"], "close": b["c"]})
        m["high"] = max(m["high"], b["h"])
        m["low"] = min(m["low"], b["l"])
        m["close"] = b["c"]
    keys = sorted(by_month)[-months:]
    out = [by_month[k] for k in keys]
    prev_close: float | None = None
    for i, m in enumerate(out):
        base = prev_close if prev_close is not None else m["open"]
        m["pct"] = (m["close"] - base) / base * 100 if base else 0.0
        m["is_current"] = i == len(out) - 1
        prev_close = m["close"]
    return out, out


def _monthly_narrative(months: list[dict]) -> str:
    """Kalimat ringkas: bulan terbaik/terburuk, streak, dan MTD bulan berjalan."""
    done = [m for m in months if not m.get("is_current")]
    txt = ""
    if len(done) >= 2:
        best = max(done, key=lambda m: m["pct"])
        worst = min(done, key=lambda m: m["pct"])
        up = sum(1 for m in done if m["pct"] > 0)
        txt += (f"Dari {len(done)} bulan terakhir yang tuntas, {up} bulan "
                f"ditutup positif. Bulan terbaik: <b>{best['label']}</b> "
                f"({best['pct']:+.2f}%, ditutup {best['close']:,.2f}); "
                f"terburuk: <b>{worst['label']}</b> ({worst['pct']:+.2f}%, "
                f"ditutup {worst['close']:,.2f}). ")
        # streak arah pada bulan-bulan tuntas (terakhir ke belakang)
        streak = 0
        direction = 0
        for m in reversed(done):
            d = 1 if m["pct"] > 0 else (-1 if m["pct"] < 0 else 0)
            if direction == 0 and d != 0:
                direction, streak = d, 1
            elif d != 0 and d == direction:
                streak += 1
            elif d != 0:
                break
        if streak >= 2:
            kata = "kenaikan" if direction > 0 else "penurunan"
            txt += (f"{streak} bulan terakhir membentuk rangkaian {streak} bulan "
                    f"{kata} berturut-turut. ")
    cur = months[-1] if months else None
    if cur and cur.get("is_current"):
        kata = "naik" if cur["pct"] > 0 else "turun"
        txt += (f"Bulan berjalan ({cur['label']}): {kata} {abs(cur['pct']):.2f}% "
                f"month-to-date, tertinggi {cur['high']:,.2f} dan terendah "
                f"{cur['low']:,.2f}.")
    return txt or "Belum cukup data bulanan untuk direkap."


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
    day = _day_summary(bars1h, now)
    month_rows, months = _monthly_recap(bars4h, now)
    stats = tracking.get("stats", {})
    results = sorted(
        patterns.get("results", []),
        key=lambda r: (r.get("tf", ""), -(r.get("oos_win_rate") or 0)))

    periode_txt = now.astimezone(WIB).strftime("%a %d %b %Y")

    # ---------- isi PDF ----------
    story: list = []
    story.append(_CoverBanner(174 * mm, periode_txt))
    story.append(Spacer(1, 10))

    status = rec.get("status", "-")
    bias = rec.get("bias", "-")
    conf = rec.get("confidence")
    conf_txt = f"{round(conf * 100)}%" if isinstance(conf, float) else "belum ada"
    trend = (rec.get("h4_context") or {}).get("trend", "-")
    hit = stats.get("hit_rate")

    # 1. Ringkasan eksekutif
    exec_txt = ""
    if day:
        chg_d = _pct(day["close"], day["prev_close"] or day["open"])
        exec_txt += (
            f"Hari ini (WIB) XAUUSD bergerak dari {day['open']:,.2f} ke "
            f"{day['close']:,.2f} ({chg_d} terhadap close kemarin), "
            f"tertinggi {day['high']:,.2f} dan terendah {day['low']:,.2f}. ")
    if week:
        chg = _pct(week["close"], week["prev_close"] or week["open"])
        exec_txt += (
            f"7 hari terakhir: {week['open']:,.2f} ke {week['close']:,.2f} "
            f"({chg} terhadap close 7 hari lalu). "
            f"Tren H4 saat ini: {trend}. Rekomendasi terbaru ({rec.get('id', '-')}): "
            f"status <b>{status.upper()}</b>, bias {bias}, peluang historis {conf_txt}. ")
    if not week:
        exec_txt += "Data harga 7 hari terakhir belum tersedia. "
    if hit is not None:
        exec_txt += (f"Rekam jejak feedback loop: {round(hit * 100)}% rekomendasi "
                     f"teresolve kena TP1 ({stats.get('wins', 0)}/{stats.get('resolved', 0)}).")
    else:
        exec_txt += ("Feedback loop belum punya sampel teresolve yang cukup - "
                     "laporan berikutnya akan mulai memuat hit-rate aktual.")
    story.append(_section(1, "RINGKASAN EKSEKUTIF"))
    story.append(Spacer(1, 5))
    story.append(Paragraph(exec_txt, S_BODY))

    # 2. Grafik harga
    story.append(Spacer(1, 4))
    story.append(_section(2, "PERGERAKAN HARGA - 4 MINGGU TERAKHIR (CLOSE H4)"))
    story.append(Spacer(1, 5))
    if chart_bars:
        story.append(_price_chart(chart_bars[-168:], 170 * mm, 52 * mm))
        story.append(Spacer(1, 6))

    # 3. Ringkasan bulan sebelumnya
    story.append(Spacer(1, 4))
    story.append(_section(3, "RINGKASAN BULAN SEBELUMNYA - PRICE ACTION PER BULAN"))
    story.append(Spacer(1, 5))
    if month_rows:
        rows = [["Bulan", "Open", "High", "Low", "Close", "Perubahan"]]
        for m in month_rows:
            label = m["label"] + (" (berjalan)" if m.get("is_current") else "")
            rows.append([
                label,
                f"{m['open']:,.2f}", f"{m['high']:,.2f}",
                f"{m['low']:,.2f}", f"{m['close']:,.2f}",
                f"{m['pct']:+.2f}%",
            ])
        story.append(_table(rows, [34 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm,
                                   28 * mm]))
        story.append(Paragraph(_monthly_narrative(months), S_BODY))
        story.append(Paragraph(
            "Perubahan dihitung terhadap close bulan sebelumnya; bulan berjalan "
            "bersifat month-to-date dan masih berubah sampai akhir bulan.",
            S_SMALL))
    else:
        story.append(Paragraph("Data bulanan belum tersedia.", S_BODY))

    # 4. Statistik backtest
    story.append(Spacer(1, 4))
    story.append(_section(4, "STATISTIK BACKTEST PER POLA (HASIL TERBARU)"))
    story.append(Spacer(1, 5))
    if results:
        head = ["TF", "Pola", "n", "Win%", "95% CI", "OOS n",
                "OOS Win%", "Net OOS%", "Avg R"]
        rows = [head]
        for r in results:
            lo, hi = r.get("win_rate_lo"), r.get("win_rate_hi")
            ci = (f"{round(lo * 100, 1)}-{round(hi * 100, 1)}"
                  if lo is not None and hi is not None else "-")
            rows.append([
                r.get("tf", "-").upper(), r.get("pattern", "-"),
                r.get("n", 0),
                f"{round((r.get('win_rate') or 0) * 100, 1)}",
                ci,
                r.get("oos_n", 0),
                f"{round((r.get('oos_win_rate') or 0) * 100, 1)}"
                if r.get("oos_win_rate") is not None else "-",
                f"{round((r.get('cost_oos_win_rate') or 0) * 100, 1)}"
                if r.get("cost_oos_win_rate") is not None else "-",
                f"{r.get('avg_r'):+.2f}" if r.get("avg_r") is not None else "-",
            ])
        story.append(_table(rows, [13 * mm, 40 * mm, 12 * mm, 15 * mm, 20 * mm,
                                   13 * mm, 16 * mm, 17 * mm, 15 * mm]))
        story.append(Paragraph(
            "Aturan entry backtest: pola searah trend EMA, SL 1.0x ATR, TP 1.5x ATR, "
            "SL dianggap duluan bila TP & SL tersentuh di bar sama (konservatif). "
            "95% CI = interval kepercayaan Wilson untuk Win%. Net OOS% = win-rate "
            f"out-of-sample SETELAH biaya spread ${backtest.COST_USD:.2f}/oz. "
            "OOS = 30% data terakhir (walk-forward); sinyal tumpang tindih "
            f"dalam {research.COOLDOWN_BARS} bar didedup agar n jujur.", S_SMALL))
    else:
        story.append(Paragraph("Statistik pola belum tersedia.", S_BODY))

    # 5. Pembacaan pola dari history
    story.append(Spacer(1, 4))
    story.append(_section(5, "PEMBACAAN POLA BERDASARKAN BACKTEST / HISTORY"))
    story.append(Spacer(1, 5))
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
            txt += ("Perlu dicatat: masih ada pola dengan win-rate gross di atas "
                    "50% - setelah biaya spread (kolom Net OOS%) angka nyata "
                    "selalu lebih rendah, dan slippage tetap tidak dimodelkan.")
        else:
            txt += ("Perlu dicatat: <b>tidak ada pola yang mencapai win-rate OOS "
                    "di atas 50%</b> - secara historis sinyal ini lebih sering gagal "
                    "daripada berhasil. Perlakukan setiap sinyal dengan ukuran risiko "
                    "kecil dan SL yang disiplin.")
        story.append(Paragraph(txt, S_BODY))
    else:
        story.append(Paragraph(
            "Belum cukup sampel pola (n>=50) untuk pembacaan yang berarti.", S_BODY))

    # 5b. TP2 (runner) + statistik risiko per pola — dihitung dari data H1
    #     saat laporan dibuat (harian, jadi biaya hitung tidak masalah).
    if bars1h:
        df1 = research.bars_to_df(bars1h)
        sigs1 = research.dedup_signals(pat.detect_signals(df1, "1h"))
        ind1 = pat.add_indicators(df1)
        hz = backtest.DEFAULT_HORIZON["1h"]
        std1 = backtest.evaluate(ind1, sigs1, hz)
        tp2_1 = backtest.evaluate_tp2(ind1, sigs1, hz)
        risk1 = backtest.risk_stats(std1)
        tp2_by: dict[str, dict] = {}
        for r in tp2_1:
            m = tp2_by.setdefault(r["pattern"], {"n": 0, "tp1": 0, "tp2": 0, "be": 0})
            m["n"] += 1
            if r["outcome"] in ("tp2", "be", "timeout"):  # posisi yang mencapai TP1
                m["tp1"] += 1
                if r["outcome"] == "tp2":
                    m["tp2"] += 1
                elif r["outcome"] == "be":
                    m["be"] += 1
        if tp2_by:
            rows = [["Pola H1", "Sinyal", "Capai TP1", "Capai TP2",
                     "Peluang TP2*", "Exit BE", "Max DD (R)", "Loss runtun"]]
            for pname in sorted(tp2_by, key=lambda p: -tp2_by[p]["n"]):
                m = tp2_by[pname]
                rk = risk1.get(("1h", pname), {})
                rate = (f"{round(m['tp2'] / m['tp1'] * 100, 1)}%"
                        if m["tp1"] else "-")
                rows.append([
                    pname, m["n"], m["tp1"], m["tp2"], rate, m["be"],
                    f"{rk.get('max_dd_r', 0):.1f}",
                    rk.get("max_loss_streak", 0),
                ])
            story.append(Spacer(1, 6))
            story.append(Paragraph("TP2 (runner) & statistik risiko per pola:", S_H2))
            story.append(Spacer(1, 3))
            story.append(_table(rows, [40 * mm, 16 * mm, 18 * mm, 18 * mm, 20 * mm,
                                       16 * mm, 20 * mm, 20 * mm]))
            story.append(Paragraph(
                "*Skenario runner: saat TP1 (1.5xATR) tercapai, SL dipindah ke "
                "breakeven lalu sisa posisi mengejar TP2 (3xATR). Peluang TP2 = "
                "capai TP2 di antara posisi yang berhasil mencapai TP1. Exit BE = "
                "kembali ke entry setelah TP1 (tidak untung tidak rugi). Max DD dan "
                "loss runtun dihitung dari ekuitas kumulatif R (sinyal dedup, urut "
                "waktu).", S_SMALL))

    # 6. Feedback loop
    story.append(Spacer(1, 4))
    story.append(_section(6, "FEEDBACK LOOP - REKOMENDASI VS HARGA AKTUAL"))
    story.append(Spacer(1, 5))
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

    # 7. Hasil rekomendasi per hari (log EOD tracking.json, 7 hari terakhir)
    eod_days = tracking.get("eod", [])[:7]
    if eod_days:
        story.append(Spacer(1, 4))
        story.append(_section(7, "HASIL REKOMENDASI PER HARI (7 HARI TERAKHIR)"))
        story.append(Spacer(1, 5))
        rows = [["Tanggal (WIB)", "Entry", "TP1", "SL", "Timeout", "Hasil hari"]]
        for d in eod_days:
            ents = d.get("entries", [])
            w = sum(1 for e in ents if e.get("status") == "win")
            l = sum(1 for e in ents if e.get("status") == "loss")
            t = sum(1 for e in ents if e.get("status") == "timeout")
            if not ents and d.get("status") == "skip":
                note = "skip — tanpa setup entry"
            elif w > l:
                note = f"net +{w - l} TP1" if w else "tanpa win"
            elif l > w:
                note = f"net -{l - w} SL"
            else:
                note = "seimbang" if w else "tanpa hasil"
            rows.append([d.get("date", "-"), len(ents), w, l, t, note])
        story.append(_table(rows, [34 * mm, 18 * mm, 16 * mm, 16 * mm, 20 * mm,
                                   40 * mm], align_right_from=1))
        # detail entry untuk hari terbaru yang memang punya hasil (hari
        # terbaru bisa berupa "skip" — jangan tampilkan detail kosong)
        detail_day = next(
            (d for d in eod_days
             if any(e.get("why") for e in d.get("entries", []))), None)
        detail = [e for e in (detail_day or {}).get("entries", []) if e.get("why")]
        if detail and detail_day:
            story.append(Spacer(1, 5))
            story.append(Paragraph(
                f"Detail {detail_day.get('date', '-')} (hari terbaru berisi hasil):",
                S_H2))
            story.append(Spacer(1, 3))
            drows = [["Pola", "Bias", "Hasil", "MFE/MAE USD", "Ringkas"]]
            for e in detail[:8]:
                mfe = e.get("mfe_usd")
                mae = e.get("mae_usd")
                mm_txt = ("-" if mfe is None or mae is None
                          else f"+{mfe:.2f} / -{mae:.2f}")
                why = (e.get("why") or "-")
                if len(why) > 110:
                    why = why[:107] + "..."
                drows.append([
                    e.get("pattern", "-"),
                    e.get("bias", "-"),
                    _fmt_rec_status(e.get("status", "-")),
                    mm_txt,
                    why,
                ])
            story.append(_table(drows, [34 * mm, 20 * mm, 20 * mm, 30 * mm,
                                        48 * mm]))
        story.append(Paragraph(
            "Log EOD diturunkan dari history feedback loop (idempotent): setiap "
            "rekomendasi entry dinilai vs harga aktual — SL dicek lebih dulu "
            "(konservatif), timeout 24 bar H1.", S_SMALL))

    # 8. Track event 7 hari ke depan
    story.append(Spacer(1, 4))
    story.append(_section(8, "EVENT EKONOMI 3-STAR US 7 HARI KE DEPAN"))
    story.append(Spacer(1, 5))
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

    # 9. Sorotan berita
    story.append(Spacer(1, 4))
    story.append(_section(9, "SOROTAN BERITA TERVALIDASI (7 HARI TERAKHIR)"))
    story.append(Spacer(1, 5))
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

    # Disclaimer
    story.append(Spacer(1, 6))
    story.append(Paragraph("DISCLAIMER", S_KICK))
    story.append(Paragraph(
        "Laporan ini dibuat otomatis dari data historis dan statistik probabilitas. "
        "Kinerja masa lalu tidak menjamin hasil masa depan. Kolom NET memodelkan "
        f"spread flat ${backtest.COST_USD:.2f}/oz; slippage, komisi, dan pelebaran "
        "spread saat rollover/berita tidak dimodelkan. Bukan saran finansial - "
        "keputusan trading sepenuhnya tanggung jawab Anda.", S_SMALL))

    # ---------- tulis PDF + index ----------
    out_dir = store.DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_wib = now.astimezone(WIB).strftime("%Y-%m-%d")
    pdf_name = f"daily-{date_wib}.pdf"
    pdf_path = out_dir / pdf_name
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"GoldPulse Daily {date_wib}",
        author="goldpulse analyzer")

    def _page(canvas, _doc):
        # watermark diagonal CONFIDENTIAL di setiap halaman
        canvas.saveState()
        canvas.translate(A4[0] / 2, A4[1] / 2)
        canvas.rotate(38)
        canvas.setFont("Helvetica-Bold", 40)
        canvas.setFillColor(colors.Color(0.55, 0.55, 0.60, alpha=0.09))
        canvas.drawCentredString(0, -6 * mm, "C O N F I D E N T I A L")
        canvas.restoreState()
        # footer: garis aksen lime + identitas + nomor halaman
        canvas.setStrokeColor(LIME)
        canvas.setLineWidth(1)
        canvas.line(18 * mm, 12.5 * mm, A4[0] - 18 * mm, 12.5 * mm)
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(18 * mm, 8.5 * mm,
                          f"GoldPulse daily report {date_wib} - "
                          f"CONFIDENTIAL - bukan saran finansial")
        canvas.drawRightString(A4[0] - 18 * mm, 8.5 * mm,
                               f"Hal. {canvas.getPageNumber()}")
        # identitas tepi atas halaman lanjutan
        if canvas.getPageNumber() > 1:
            canvas.setFont("Helvetica-Bold", 6.5)
            canvas.setFillColor(GRAY)
            canvas.drawString(18 * mm, A4[1] - 10 * mm,
                              f"GOLDPULSE - LAPORAN HARIAN - {periode_txt}")

    doc.build(story, onFirstPage=_page, onLaterPages=_page)

    index_path = out_dir / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {"updated_at_wib": None, "files": []}
    entry = {
        "name": pdf_name,
        "date_wib": _wib(now),
        "kb": round(pdf_path.stat().st_size / 1024),
        "summary": (f"XAUUSD {week['close']:,.2f}" if week else "laporan harian")
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