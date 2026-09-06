# Gold Analisator Harian 🥇

Analisa harian XAUUSD (timeframe H1 + H4) dengan output rekomendasi berbasis
statistik historis + kalender fundamental (bintang 3, US) + berita geopolitik.
Tampilan mobile (PWA), semua waktu ditampilkan dalam **WIB (UTC+7)**.

> Bukan saran finansial. Output sistem adalah probabilitas/statistik, bukan prediksi pasti.

## Arsitektur

```
GitHub Actions (cron, gratis)
  ├─ tiap jam  : sync candle H1/H4 Twelve Data  ─┐
  ├─ tiap 4 j  : kalender TradingEconomics 3★ US │ (P4)
  ├─ tiap 4 j  : berita Google News RSS + GDELT  │ (P4)
  └─ 05:37 WIB : analisa harian -> recommendation.json + feedback loop (P5)
                                                 │
             push data/*.json                    │
                                                 ▼
                    GitHub repo (public) ── auto-deploy ──> Vercel/Cloudflare Pages
                                                          PWA mobile
                                                          ├─ harga live: XAUS.com
                                                          │  (langsung dari browser, tanpa key)
                                                          └─ kartu rekomendasi + akurasi (P5)
```

- **Data harga**: Twelve Data `XAU/USD` — backfill ~3 tahun, sync tiap jam
  (~50 dari 800 kredit gratis/hari). `yfinance GC=F` dipakai sebagai
  cross-check (P3).
- **Penyimpanan**: file JSON di `data/`, di-commit oleh Actions.
- **Keamanan**: API key hanya hidup di GitHub Secrets / `.env` lokal.
  Frontend tidak memegang satu pun credential.

## Setup (sekali saja)

1. Push repo ini ke GitHub (repo **public** → menit Actions gratis tak terbatas).
2. Buka **Settings → Secrets and variables → Actions → New repository secret**,
   tambahkan `TWELVEDATA_API_KEY` (key gratis dari twelvedata.com).
3. Tab **Actions → Backfill → Run workflow** (years: 3). Ini mengisi
   `data/xauusd_1h.json` & `data/xauusd_4h.json` dengan ~3 tahun candle.
4. Workflow **Hourly Sync** jalan otomatis tiap jam (Mon–Sab UTC).
   Cek hasilnya di `data/meta.json` dan commit history.
5. Deploy frontend: **Vercel** (import repo, preset "Other", root `./`) atau
   **Cloudflare Pages** (connect repo, output dir `./`) — static, tanpa build step.
6. Buka di HP → "Add to Home Screen" → jalan seperti app (PWA).

## Preview lokal (tanpa API key)

```bash
python tools/make_synth_data.py     # data sintetis untuk melihat chart jalan
python -m http.server 8734          # buka http://localhost:8734 di browser
rm -rf data                         # WAJIB sebelum backfill data asli!
```

Backfill/sync otomatis **menolak jalan** kalau data sintetis masih ada
(guard di `store.refuse_if_synthetic`) — data palsu tidak akan pernah
tercampur ke dataset asli.

## Menjalankan lokal

```bash
python -m pip install -r requirements.txt
copy .env.example .env        # isi TWELVEDATA_API_KEY
python -m analyzer.jobs.backfill --years 3   # sekali saja
python -m analyzer.jobs.sync                 # kapan pun mau update
python -m analyzer.jobs.research             # statistik pola -> data/patterns.json
python -m analyzer.jobs.fundamental          # kalender 3★ US + berita (tanpa perlu API key)
python -m analyzer.jobs.daily                 # research + rekomendasi + nilai tracking (tanpa API key)
python tests/test_p1.py; python tests/test_p3.py; python tests/test_p4.py; python tests/test_p5.py
```

## Struktur

```
analyzer/
  config.py            simbol, interval, path, konstanta
  twelve.py            klien Twelve Data (paginasi 5.000 bar, backoff 429)
  store.py             simpan JSON per timeframe, merge, cek gap, guard data sintetis
  indicators.py        EMA20/50, RSI(14), ATR(14), regime trend
  patterns.py          deteksi pola (engulfing, hammer, shooting star, inside bar) searah trend
  backtest.py          evaluasi sinyal: TP 1,5xATR vs SL 1xATR, aturan konservatif, OOS 70/30
  calendar.py          kalender bintang-3 US: scrape TE + fallback ForexFactory + aturan jeda entry
  news.py              berita kredibel: Google News RSS (whitelist sumber) + GDELT
  recommend.py         rekomendasi harian: pola H1 searah trend H4 + confidence + filter jeda
  track.py             feedback loop: nilai tiap rekomendasi (TP1/SL/timeout) + hit-rate
  jobs/
    backfill.py        isi history ~3 tahun (sekali)
    sync.py            update inkremental tiap jam (idempotent, gap-filling)
    research.py        statistik pola historis -> data/patterns.json
    fundamental.py     kalender + berita -> data/calendar.json + data/news.json
    daily.py           05:37 WIB: research + rekomendasi + nilai tracking (P5)
index.html             PWA shell (mobile-first, dark)
app.js                 chart lightweight-charts + toggle H1/H4 + harga live XAUS.com
style.css              tema dark mobile
sw.js                  service worker: app shell offline, data network-first
manifest.webmanifest   PWA manifest (installable di HP)
data/
  xauusd_1h.json       candle H1 (UTC)
  xauusd_4h.json       candle H4 (UTC)
  meta.json            status sync terakhir + coverage + laporan gap
  patterns.json        statistik pola historis (job research)
  calendar.json        kalender bintang-3 US (TE, fallback FF)
  news.json            berita kredibel 24 jam terakhir
  recommendation.json  rekomendasi hari ini (job daily) — dibaca kartu di UI
  tracking.json        riwayat + hasil (win/loss/timeout) semua rekomendasi + hit-rate
tools/make_synth_data.py  data sintetis untuk dev UI (jangan pernah commit)
.github/workflows/
  sync.yml             cron tiap jam + commit data
  backfill.yml         manual dispatch dari tab Actions
  fundamental.yml      cron tiap 4 jam: kalender + berita
  daily.yml            cron 05:37 WIB (Sen-Jum): rekomendasi + feedback loop
```

## Anggaran kredit Twelve Data (free: 800/hari)

| Job | Frekuensi | Kredit |
|---|---|---|
| Hourly sync (H1+H4) | 24×/hari | ~48 |
| Backfill | sekali | ~6 |
| Research / daily / fundamental | harian | 0 (komputasi lokal + scrape, tanpa API) |

## Roadmap

- [x] **P1 — Data Foundation** (repo, backfill, sync, workflow)
- [x] **P2 — Chart PWA** (lightweight-charts, WIB, harga live XAUS.com, PWA installable)
- [x] **P3 — Backtest Engine** (EMA/RSI/ATR, pola searah trend, win-rate TP/SL berbasis ATR, uji out-of-sample 70/30, panel statistik di UI)
- [x] **P4 — Fundamental Layer** (kalender TE bintang-3 US + fallback ForexFactory, berita kredibel via Google News RSS whitelist + GDELT, aturan jeda entry −2h..+1h, panel UI)
- [x] **P5 — Rekomendasi Harian + Feedback Loop** (job daily 05:37 WIB: kartu bias/confidence/level entry di UI, feedback loop win/loss/timeout + dashboard hit-rate, tanpa notifikasi — web only)