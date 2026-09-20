"""Kalender ekonomi bintang-3 US: Trading Economics (utama) + ForexFactory (fallback).

Output data/calendar.json dipakai UI (tampilan WIB) dan mesin rekomendasi P5
(aturan jeda entry di sekitar rilis bintang-3).

Fakta yang sudah diverifikasi (Sep 2026):
- Halaman kalender TE di-render server-side dan bisa diambil dengan GET biasa
  tanpa key; waktu event GMT/UTC (NFP tampil "12:30 PM" = 12:30 UTC).
- Feed resmi ForexFactory (impact High, currency USD) identik secara semantik
  dengan "bintang 3 US" di TE — dipakai bila TE berubah struktur/block.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import store

WIB = ZoneInfo("Asia/Jakarta")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

TE_URL = "https://tradingeconomics.com/united-states/calendar"
FF_FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]

# Aturan jeda entry (dipakai mesin rekomendasi P5 & banner UI):
# jangan buka posisi baru dari 2 jam sebelum hingga 3 jam sesudah rilis.
BLACKOUT_BEFORE_H = 2.0
BLACKOUT_AFTER_H = 3.0
# Pasca-event 3 jam (bukan 1) — divalidasi data 2026-09-20 (arsip 835 event
# 3-star US x bar H1 3 tahun, scripts/audit_events.py LOKAL): volatilitas
# |close-to-close|/ATR14 bar rilis 2,24x baseline, +1h 1,72x, +2h 1,38x,
# normal kembali di +3h; EV sinyal kolam produksi (net) di jendela
# +0..+3h pasca-event −0,25R (n=118) — sekarang sinyal di +1..+3h yang
# tadinya boleh entry jadi "tunggu". Pra-event dipertahankan 2 jam demi
# risiko ekor (slippage/gap menembus SL saat rilis — backtest R menganggap
# fill SL persis, optimis saat spike), walau EV backtest pra-event
# justru positif (+0,15R n=66).

ROW_RE = re.compile(
    r'<tr data-url="[^"]*"\s*data-id="\d+"\s*data-country="([^"]+)"[^>]*?data-event="([^"]*)"',
)
# NOTE: baris kalender TE berisi tabel bendera BERSARANG (<table><tr>...</tr></table>
# di dalam sel country), jadi baris tidak bisa diakhiri di '</tr>' pertama —
# blok baris dipisah dengan split('<tr data-url="'), bukan regex non-greedy.
DATE_RE = re.compile(r"class='?\s*(\d{4}-\d{2}-\d{2})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)")
VAL_RES = {
    "actual": re.compile(r"id='actual'[^>]*>([^<]*)<"),
    "previous": re.compile(r"id='previous'[^>]*>([^<]*)<"),
    "forecast": re.compile(r"id='forecast'[^>]*>([^<]*)<"),
    "consensus": re.compile(r"id='consensus'[^>]*>([^<]*)<"),
}


def _clean(val: str | None) -> str | None:
    if val is None:
        return None
    v = val.replace("&nbsp;", " ").replace("&amp;", "&").strip()
    return v or None


def _parse_utc(block: str) -> datetime | None:
    """Tanggal dari atribut class td + jam AM/PM (GMT) dari span waktu."""
    d = DATE_RE.search(block)
    if not d:
        return None
    date = datetime.strptime(d.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t = TIME_RE.search(block)
    if not t:
        return date  # all-day / tentative -> pakai tengah hari? tidak: awal hari
    hh = int(t.group(1)) % 12 + (12 if t.group(3) == "PM" else 0)
    return date.replace(hour=hh, minute=int(t.group(2)))


def fetch_te() -> list[dict]:
    """Scrape kalender TE bintang-3 (US saja). 1 request, tanpa key."""
    start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    resp = requests.get(
        TE_URL,
        params={"g": "top", "importance": "3", "startdate": start},
        headers={"User-Agent": UA},
        timeout=30,
    )
    resp.raise_for_status()
    events = []
    # tiap chunk = satu baris kalender lengkap (nested table ikut di dalamnya)
    for chunk in resp.text.split('<tr data-url="')[1:]:
        head = ROW_RE.match(f'<tr data-url="{chunk}')
        if not head:
            continue
        country, data_event = head.group(1).strip(), head.group(2)
        if country != "united states":
            continue
        when = _parse_utc(chunk)
        if when is None:
            continue
        vals = {}
        for key, rx in VAL_RES.items():
            m = rx.search(chunk)
            vals[key] = _clean(m.group(1)) if m else None
        events.append({
            "t_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": data_event.strip().title(),
            "country": "US",
            "importance": 3,
            "source": "tradingeconomics",
            **vals,
        })
    return events


def fetch_ff() -> list[dict]:
    """Fallback: feed resmi ForexFactory, High impact + USD."""
    events, seen = [], set()
    for url in FF_FEEDS:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
        except (requests.RequestException, ValueError):
            continue
        for e in rows:
            if e.get("country") != "USD" or e.get("impact") != "High":
                continue
            try:
                when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except (KeyError, ValueError):
                continue
            key = (when.strftime("%Y-%m-%dT%H"), e.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "t_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": e.get("title") or "US High-Impact Event",
                "country": "US",
                "importance": 3,
                "actual": None,
                "previous": _clean(e.get("previous")),
                "forecast": _clean(e.get("forecast")),
                "consensus": None,
                "source": "forexfactory",
            })
    return events


def collect_calendar() -> tuple[list[dict], str, str]:
    """(events, source, note). TE dulu; kalau gagal/kosong -> ForexFactory."""
    try:
        events = fetch_te()
        if events:
            return events, "tradingeconomics", f"{len(events)} event 3-star US"
        note = "TE merespons tapi 0 event terparse"
    except requests.RequestException as exc:
        note = f"TE gagal: {exc}"
    events = fetch_ff()
    if events:
        return events, "forexfactory", note + f" -> fallback FF ({len(events)} event)"
    return [], "none", note + " -> fallback FF juga kosong"


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---- arsip event 3-star historis (batch 2026-09-20) ----
# data/events_hist.json = semua event 3-star US yang SUDAH LEWAT (seed
# 3 tahun via TE paginasi; dilanjutkan otomatis oleh job fundamental tiap
# run — feed memuat ±7 hari ke belakang, job jalan tiap 3 jam, jadi tidak
# ada yang lolos). Dipakai backtest kondisi event: validasi jendela
# blackout + EV pola di sekitar rilis. Tanpa arsip ini, analisa korelasi
# event tidak mungkin (feed cuma punya event ke depan).

HIST_FILE = "events_hist.json"


def load_hist() -> list[dict]:
    """Event 3-star US historis dari arsip (urut waktu). Kosong kalau
    arsip belum ada — pemanggil harus tangani pool kosong dengan jujur."""
    path = store.DATA_DIR / HIST_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    return sorted(
        (e for e in events if isinstance(e, dict) and e.get("t_utc")
         and e.get("title")),
        key=lambda e: e["t_utc"])


def archive_past(events: list[dict], now: datetime | None = None) -> int:
    """Merge event yang SUDAH LEWAT dari feed ke arsip. Idempotent per
    (t_utc, title). Return jumlah baris baru yang ditambahkan. Event ke
    depan TIDAK diarsip (masih bisa bergeser jadwalnya)."""
    now = now or datetime.now(timezone.utc)
    path = store.DATA_DIR / HIST_FILE
    hist = load_hist()
    have = {(e["t_utc"], e["title"]) for e in hist}
    added = 0
    for e in sorted(events, key=lambda e: e.get("t_utc", "")):
        when = e.get("t_utc")
        title = e.get("title")
        if not when or not title:
            continue
        try:
            if parse_utc(when) > now:
                continue  # masih ke depan — jangan arsip
        except ValueError:
            continue
        if (when, title) in have:
            continue
        have.add((when, title))
        hist.append({"t_utc": when, "title": title})
        added += 1
    if added:
        hist.sort(key=lambda e: e["t_utc"])
        store.write_json(HIST_FILE, {
            "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "tradingeconomics",
            "note": ("arsip event 3-star US untuk backtest kondisi event "
                     "(seed 2023-09 -> 2026-09 via TE; auto-append oleh "
                     "job fundamental)"),
            "events": hist,
        })
    return added


def active_blackout(events: list[dict], now: datetime | None = None) -> dict | None:
    """Event bintang-3 yang sedang dalam jendela jeda entry (untuk P5 & UI)."""
    now = now or datetime.now(timezone.utc)
    for e in events:
        when = parse_utc(e["t_utc"])
        start = when - timedelta(hours=BLACKOUT_BEFORE_H)
        end = when + timedelta(hours=BLACKOUT_AFTER_H)
        if start <= now <= end:
            return {
                "event": e,
                "minutes_to_event": round((when - now).total_seconds() / 60),
            }
    return None


def upcoming(events: list[dict], hours: float = 48, now: datetime | None = None) -> list[dict]:
    """Event dalam N jam ke depan, urut waktu (untuk UI)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for e in events:
        when = parse_utc(e["t_utc"])
        if now <= when <= now + timedelta(hours=hours):
            out.append(e)
    return sorted(out, key=lambda e: e["t_utc"])