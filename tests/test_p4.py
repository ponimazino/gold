"""Test P4: parse TE, fallback FF, aturan jeda entry, filter berita.

Run: python tests/test_p4.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import calendar as cal  # noqa: E402
from analyzer import news  # noqa: E402


TE_FIXTURE = """
<tr data-url="/united-states/non-farm-payrolls" data-id="401030"
    data-country="united states" data-category="non farm payrolls"
    data-event="non farm payrolls" data-symbol='USNFP'>
 <td style="white-space: nowrap;" class=' 2026-09-04'>
   <span class="event-39 calendar-date-3"> 12:30 PM </span> </td>
 <td class="calendar-item" style="white-space: nowrap"><table style="padding: 0px;">
   <tr><td style="padding-left: 5px;"><div title="United States" class='flag flag-us'></div></td>
       <td title="United States" class="calendar-iso">US</td></tr></table></td>
 <td><a class='calendar-event' href='/united-states/non-farm-payrolls'>Non Farm Payrolls</a></td>
 <td class="calendar-item"><span id='actual'>151</span></td>
 <td class="calendar-item"><span id='previous'>119</span></td>
 <td class="calendar-item"><a id='consensus' aria-label='USNFP' href='x'></a></td>
 <td class="calendar-item"><a id='forecast' href='x'>150</a></td>
</tr>
<tr data-url="/united-states/cpi-yoy" data-id="1" data-country="united states"
    data-category="inflation rate" data-event="inflation rate yoy">
 <td class=' 2026-09-11'><span class="event-1 calendar-date-3"> 12:30 PM </span></td>
 <td><td class="calendar-iso">US</td></td>
 <td><a class='calendar-event' href='/united-states/inflation-rate'>Inflation Rate YoY</a></td>
 <td><span id='actual'></span></td><td><span id='previous'>2.9%</span></td>
 <td><a id='consensus' href='x'></a></td><td><a id='forecast' href='x'></a></td>
</tr>
<tr data-url="/germany/manufacturing-pmi" data-id="2" data-country="germany"
    data-category="manufacturing pmi" data-event="s&p global manufacturing pmi flash">
 <td class=' 2026-09-23'><span class="event-2 calendar-date-3"> 07:30 AM </span></td>
</tr>
<tr data-url="/united-states/all-day-event" data-id="3" data-country="united states"
    data-category="x" data-event="crude oil inventories tentative">
 <td class=' 2026-09-10'><span class="event-3"> Tentative </span></td>
 <td><span id='actual'></span></td><td><span id='previous'></span></td>
 <td><a id='consensus' href='x'></a></td><td><a id='forecast' href='x'></a></td>
</tr>
"""


def test_te_parse():
    # ganti requests.get dengan fixture
    import requests as _r

    class FakeResp:
        text = TE_FIXTURE
        def raise_for_status(self): pass

    orig_get = _r.get
    _r.get = lambda *a, **k: FakeResp()
    try:
        events = cal.fetch_te()
    finally:
        _r.get = orig_get

    assert len(events) == 3, events                    # baris germany dibuang
    nfp = events[0]
    assert nfp["title"] == "Non Farm Payrolls"
    assert nfp["t_utc"] == "2026-09-04T12:30:00Z"      # 12:30 PM GMT
    assert nfp["actual"] == "151" and nfp["forecast"] == "150"
    cpi = events[1]
    assert cpi["t_utc"] == "2026-09-11T12:30:00Z"
    assert cpi["previous"] == "2.9%" and cpi["actual"] is None
    assert events[2]["t_utc"] == "2026-09-10T00:00:00Z"  # tentative -> awal hari
    print("ok: parse TE (US only, waktu UTC, nilai aktual/forecast, tentative)")


def test_ff_parse():
    import requests as _r

    fake_feed = [
        {"title": "Non-Farm Employment Change", "country": "USD", "impact": "High",
         "date": "2026-09-04T08:30:00-04:00", "forecast": "150K", "previous": "119K"},
        {"title": "Low impact stuff", "country": "USD", "impact": "Low",
         "date": "2026-09-04T08:30:00-04:00", "forecast": "", "previous": ""},
        {"title": "German stuff", "country": "EUR", "impact": "High",
         "date": "2026-09-04T02:00:00-04:00", "forecast": "", "previous": ""},
    ]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return fake_feed

    orig_get = _r.get
    _r.get = lambda *a, **k: FakeResp()
    try:
        events = cal.fetch_ff()
    finally:
        _r.get = orig_get

    assert len(events) == 1, events                     # hanya USD High
    assert events[0]["t_utc"] == "2026-09-04T12:30:00Z"  # -04:00 -> UTC
    assert events[0]["source"] == "forexfactory"
    print("ok: fallback ForexFactory (USD High, konversi offset -04:00)")


def test_blackout():
    events = [{"t_utc": "2026-09-04T12:30:00Z", "title": "NFP", "country": "US",
               "importance": 3, "source": "test"}]
    t0 = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
    assert cal.active_blackout(events, now=t0 - timedelta(hours=1)) is not None      # -1h -> dalam jeda
    assert cal.active_blackout(events, now=t0 - timedelta(hours=2.5)) is None       # -2.5h -> aman
    assert cal.active_blackout(events, now=t0 + timedelta(minutes=30)) is not None   # +30m -> masih jeda
    assert cal.active_blackout(events, now=t0 + timedelta(hours=2)) is None         # +2h -> selesai
    blk = cal.active_blackout(events, now=t0 - timedelta(hours=1))
    assert blk["minutes_to_event"] == 60
    # upcoming window
    assert len(cal.upcoming(events, hours=48, now=t0 - timedelta(hours=47))) == 1
    assert cal.upcoming(events, hours=1, now=t0 - timedelta(hours=3)) == []
    print("ok: aturan jeda entry (-2h .. +1h) & daftar upcoming")


def test_news_whitelist():
    rss = """<rss><channel>
<item><title>Gold surges as Fed signals pause</title><link>https://news.google.com/x</link>
<pubDate>Fri, 04 Sep 2026 12:00:00 GMT</pubDate>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>Gold Rush Arrives - Montana State Athletics</title><link>https://news.google.com/y</link>
<pubDate>Fri, 04 Sep 2026 13:00:00 GMT</pubDate>
<source url="https://www.montana.edu">Montana State University Athletics</source></item>
<item><title>Gold steady, focus on CPI</title><link>https://news.google.com/z</link>
<pubDate>Fri, 04 Sep 2026 14:00:00 GMT</pubDate>
<source url="https://www.fxstreet.com">FXStreet</source></item>
</channel></rss>"""

    import requests as _r

    class FakeResp:
        text = rss
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {}

    orig_get = _r.get
    _r.get = lambda *a, **k: FakeResp()
    try:
        import analyzer.news as n
        n.fetch_gdelt = lambda: []          # GDELT dilepas di test ini
        items = n.collect_news(now_utc=datetime(2026, 9, 4, 18, tzinfo=timezone.utc))
    finally:
        _r.get = orig_get

    assert len(items) == 2, items            # noise universitas tersaring
    sources = {i["source"] for i in items}
    assert sources == {"Reuters", "FXStreet"}
    assert items[0]["source"] == "FXStreet"  # terbaru dulu
    print("ok: filter berita whitelist + dedupe + urut terbaru")


def main() -> int:
    test_te_parse(None)
    test_ff_parse()
    test_blackout()
    test_news_whitelist()
    print("\nALL P4 TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())