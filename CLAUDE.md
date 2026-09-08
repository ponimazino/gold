# Gold Analyzer (XAUUSD) — Project Context

Project: analyzer harian XAUUSD, serverless penuh (GitHub Actions + JSON di repo + static hosting).
Semua komunikasi dengan user dalam **Bahasa Indonesia**.

## ATURAN KERJA (WAJIB)
- **Commit/push ke GitHub HANYA kalau user menyuruh.** Jangan pernah commit/push atas inisiatif sendiri.
- **Credential tidak boleh bocor**: API key hanya di `.env` (gitignored) lokal dan GitHub Secrets. Tidak pernah hardcoded, tidak pernah di `data/`, frontend nol secret.
- **Web-based only, TIDAK ADA notifikasi** (Telegram sudah dihapus total, jangan ditambahkan lagi).
- Waktu tampil selalu **WIB (Asia/Jakarta)**, penyimpanan UTC, timeframe H1/H4 (H4 prioritas).
- Berita hanya dari sumber wire/valid (whitelist), event fundamental 3★ US only (Trading Economics).

## Status (per 2026-09-06): SEMUA PHASE P1–P5 SELESAI & TERDEPLOY
- Repo: **github.com/ponimazino/gold** (public). Remote: `https://ponimazino@github.com/ponimazino/gold.git`.
- Data 3 tahun ter-backfill: 20.493 bar H1 + 5.323 bar H4 (2023-09-06 → 2026-09-06).
- Workflow GitHub Actions (checkout@v5, setup-python@v6, semua commit step pakai `git pull --rebase origin main` dulu untuk hindari race):
  - `daily.yml` — grid 2 jam "37 1-15/2 * * 1-5" + "37 21,23 * * 0-4" (04:37–22:37 WIB, Sen–Jum; anchor 04:37 pra-Sydney, 14:37 London, 20:37 NY + rilis data AS; tiap pasangan candle H1 dievaluasi tepat sekali) → research + resolve feedback loop + `data/recommendation.json`, `data/tracking.json`. Recommendation.json dioverwrite tiap run; history tracking tetap 1 entry/hari (dedup per tanggal).
  - `sync.yml` — "17 * * * *" (setiap hari termasuk Minggu): update bar H1/H4 tiap jam
  - `fundamental.yml` — "13 1-23/3 * * *" (tiap 3 jam, 24 menit sebelum daily di jam yang sama): kalender 3★ US + berita whitelist
  - `report.yml` — "23 21 * * 0" (04:23 WIB Senin) + dispatch: `analyzer/jobs/report.py` → PDF mingguan `data/reports/weekly-YYYY-MM-DD.pdf` + `data/reports/index.json` (max 52 entri; reportlab di requirements.txt; smoke test `tests/test_report_smoke.py`)
  - `backfill.yml` — dispatch only (jalankan manual saja; idempotent, merging)
- Rekomendasi: pola H1 di 2 bar terakhir searah trend H4 (EMA20/50) → status entry/tunggu/netral; confidence = win-rate OOS pola; blackout −2h..+1h sekitar event 3★; level Entry=close terakhir, SL 1×ATR, TP1 1,5×ATR, TP2 3×ATR. **Mode aman** (TP 1×ATR / SL 0,75×ATR, entry sama): `levels_safe` + `confidence_safe` — win-rate-nya di-backtest terpisah (`run_pair` research, `safe_*` di patterns.json), feedback loop tetap menilai level standar.
- Feedback loop: `analyzer/track.py` resolve rec aktif vs bar H1 nyata (SL dicek dulu, konservatif; timeout 24 bar) → hit-rate jujur di `data/tracking.json`. Saat resolve juga menulis `outcome` (bars_held, MFE/MAE USD, event 3★ di jendela, narasi Indonesia "mengapa benar/salah") + log akhir hari `eod` per tanggal WIB (diturunkan penuh dari history — idempotent), tampil di panel Rule monitor UI.

## Frontend produksi: folder `web/` (GoldPulse)
- React 19 + Vite 7 + recharts + lucide-react, desain dari prototipe user di `gold-ui/` (Manus — **di-gitignore, jangan dipublish**).
- Baca data dari `https://raw.githubusercontent.com/ponimazino/gold/main/data/*.json` (8 file) + `data/reports/index.json` (laporan mingguan) + spot live `https://xaus.com/api/v1/spot?compact=1` (field `xau.price`, tanpa key, display only).
- Struktur: `web/src/data.ts` (layer data + tipe), `web/src/pages/Home.tsx` (5 view: **ringkasan** [tab pertama, bahasa awam] / overview / analysis / backtest [ada panel PDF mingguan] / calendar), `web/src/index.css` (design system goldpulse), PWA di `web/public/` (manifest+sw.js — navigasi network-first, asset cache-first).
- Chart Primary Instrument: bisa **digeser (drag) & zoom (scroll + tombol +/−/reset)** — window 15..1000 bar dari 1200 bar dimuat; default 90 bar terakhir.
- Build: `npm run check` (tsc) + `npm run build`. Bundle ~188 kB gzip.
- **PWA vanilla lama masih ada di root repo** (index.html/app.js/style.css/sw.js/manifest.webmanifest) — tidak ter-deploy saat Vercel root=web, tapi kandidat pembersihan (tanya user dulu).

## Next step user: deploy ke Vercel
Root Directory = `web` (auto-detect Vite), lihat DEPLOY.md Bagian 5. Setelah itu: verifikasi workflow `daily.yml` jalan di grid 2 jam (cek tab Actions, run pertama setelah push di jam :37 WIB berikutnya).

## Perubahan lokal BELUM di-push (2026-09-08, menunggu perintah user)
- Grid jadwal: daily 3 jam → **2 jam** (04:37–22:37 WIB, Sen–Jum); fundamental 4 jam → 3 jam (:13).
- Evaluasi akhir hari: narasi outcome + log `eod` di tracking.json (lihat bullet Feedback loop).
- Mode aman: `levels_safe`/`confidence_safe` (TP 1×ATR / SL 0,75×ATR) + UI di Analysis & Ringkasan.
- `data/patterns.json`, `recommendation.json`, `tracking.json` ikut berubah (smoke run lokal 2026-09-08).
- Fix kecil: `tests/test_p4.py` `test_te_parse(None)` → `test_te_parse()` (bug test harness lama).
- Verifikasi: 5/5 suite Python lolos, tsc + build bersih, daily job 10 detik di data nyata.

## Audit 2026-09-06 — bug yang sudah diperbaiki (jangan kambuh)
- `track.py`: job daily mencatat status "entry" tapi `_resolve_one` cuma proses "active" → rekomendasi tak pernah dinilai. FIXED: normalisasi entry→active (regression test di test_p5.py). Hit-rate baru muncul setelah rekomendasi mulai teresolve (24 bar H1 ~1 hari).
- `web/public/sw.js`: index.html cache-first bikin user stuck versi lama setelah deploy. FIXED: request navigasi = network-first.
- localStorage (analyst notes) dibungkus try/catch (private mode).
- Fundamentals: event >24 jam lampau difilter dari feed.

## Gotcha Windows/Git yang pernah terjadi
- Credential Manager pernah cache akun salah (miawopen) → 403. Pakai remote URL dengan hint username `https://ponimazino@github.com/...`.
- Push dari shell Claude pernah gagal "Cannot prompt / could not read Username" → kalau git butuh interaksi auth, minta user push sendiri.