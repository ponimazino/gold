"""Berita fundamental 24 jam terakhir — HANYA dari media kredibel (whitelist).

Sumber:
1. Google News RSS (3 query keyword, gratis, tanpa key) — item difilter
   berdasarkan tag <source> terhadap whitelist wire + media finansial utama.
   Tanpa whitelist hasilnya penuh noise ("Gold Rush" universitas dll).
2. Suplemen GDELT DOC API (satu query, throttle) — gratis, tanpa key,
   tapi rate-limited ketat (429 kalau < 5 detik antar request). Hasil bisa
   kosong; kegagalan GDELT tidak pernah menggagalkan koleksi berita.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

WIB = ZoneInfo("Asia/Jakarta")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

QUERIES = [
    '"gold price" OR "gold market" OR XAU',
    '"federal reserve" OR FOMC OR "interest rates"',
    'geopolitics OR sanctions OR "trade war"',
]

# whitelist sumber kredibel (cocok via 'contains', lowercase)
WHITELIST = [
    "reuters", "bloomberg", "associated press", "ap news", "apnews", "bbc",
    "cnbc", "financial times", "al jazeera", "marketwatch", "fxstreet",
    "kitco", "investing.com", "barron", "wall street journal", "wsj",
    "yahoo finance", "business insider", "the economist", "npr",
]

MAX_ITEMS = 30
GDELT_QUERY = '(gold OR "federal reserve" OR geopolitical) sourcelang:english'

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
LINK_RE = re.compile(r"<link>(.*?)</link>", re.S)
PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
SOURCE_RE = re.compile(r'<source url="[^"]*">(.*?)</source>', re.S)
DOMAIN_RE = re.compile(r"https?://(?:www\.)?([^/]+)")


def _allowed(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in WHITELIST)


def fetch_googlenews() -> list[dict]:
    items = []
    for q in QUERIES:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(f"{q} when:1d")
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            continue  # satu query gagal -> lanjut query berikutnya
        for raw in ITEM_RE.findall(resp.text):
            t = TITLE_RE.search(raw)
            s = SOURCE_RE.search(raw)
            if not t or not s or not _allowed(s.group(1)):
                continue
            title, source = t.group(1).strip(), s.group(1).strip()
            # judul Google News berakhiran " - Publisher" -> dibuang agar
            # duplikat antar-edisi sumber yang sama bisa didedupe
            if title.endswith(f" - {source}"):
                title = title[: -len(source) - 3].strip()
            p = PUBDATE_RE.search(raw)
            l = LINK_RE.search(raw)
            try:
                when = parsedate_to_datetime(p.group(1)).astimezone(timezone.utc) if p else None
            except (ValueError, TypeError):
                when = None
            items.append({
                "title": title,
                "url": l.group(1).strip() if l else "",
                "source": source,
                "published_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
                "via": "googlenews",
            })
        time.sleep(2)  # sopan ke Google
    return items


def fetch_gdelt() -> list[dict]:
    """Satu query GDELT (rate limit ketat) — hasil kosong itu normal."""
    try:
        resp = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": GDELT_QUERY, "mode": "ArtList", "maxrecords": 25,
                    "timespan": "1d", "format": "json", "sort": "DateDesc"},
            headers={"User-Agent": UA},
            timeout=30,
        )
        if resp.status_code != 200:  # 429 = terlalu sering; skip dengan tenang
            return []
        rows = resp.json().get("articles") or []
    except (requests.RequestException, ValueError):
        return []
    items = []
    for a in rows:
        d = DOMAIN_RE.match(a.get("url", ""))
        domain = d.group(1) if d else ""
        if not _allowed(domain):
            continue
        try:
            when = datetime.strptime(a.get("seendate", "")[:16], "%Y%m%dT%H%M%S") \
                .replace(tzinfo=timezone.utc)
        except ValueError:
            when = None
        items.append({
            "title": (a.get("title") or "").strip(),
            "url": a.get("url", ""),
            "source": domain,
            "published_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
            "via": "gdelt",
        })
    return items


def collect_news(now_utc: datetime | None = None) -> list[dict]:
    """Gabung Google News + GDELT, dedupe judul, urut terbaru, max MAX_ITEMS.
    Hanya item < 36 jam dihitung (koleksi tiap 4 jam -> sedikit overlap)."""
    now = now_utc or datetime.now(timezone.utc)
    merged = fetch_googlenews() + fetch_gdelt()

    seen, out = set(), []
    for it in merged:
        # key dedupe: judul tanpa suffix publisher apapun (" - X" di akhir),
        # karena berita sama bisa disindikasi dari penerbit aslinya (WSJ dll)
        base = re.sub(r"\s*-\s*[^-]{2,40}$", "", it["title"])
        key = re.sub(r"\W+", "", base.lower())[:80]
        if not it["title"] or key in seen:
            continue
        seen.add(key)
        if it["published_utc"]:
            age_h = (now - datetime.fromisoformat(
                it["published_utc"].replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h > 36:
                continue
        out.append(it)
    out.sort(key=lambda x: x["published_utc"] or "", reverse=True)
    return out[:MAX_ITEMS]