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
  - `daily.yml` — cron "37 22 * * 1-5" (05:37 WIB, Sen–Jum): research + resolve feedback loop + rekomendasi harian → `data/recommendation.json`, `data/tracking.json`
  - `sync.yml` — "17 * * * 1-6": update bar H1/H4 tiap jam
  - `fundamental.yml` — "43 */4 * * *": kalender 3★ US + berita whitelist
  - `report.yml` — "23 21 * * 0" (04:23 WIB Senin) + dispatch: `analyzer/jobs/report.py` → PDF mingguan `data/reports/weekly-YYYY-MM-DD.pdf` + `data/reports/index.json` (max 52 entri; reportlab di requirements.txt; smoke test `tests/test_report_smoke.py`)
  - `backfill.yml` — dispatch only (jalankan manual saja; idempotent, merging)
- Rekomendasi: pola H1 di 2 bar terakhir searah trend H4 (EMA20/50) → status entry/tunggu/netral; confidence = win-rate OOS pola; blackout −2h..+1h sekitar event 3★; level Entry=close terakhir, SL 1×ATR, TP1 1,5×ATR, TP2 3×ATR.
- Feedback loop: `analyzer/track.py` resolve rec aktif vs bar H1 nyata (SL dicek dulu, konservatif; timeout 24 bar) → hit-rate jujur di `data/tracking.json`.

## Frontend produksi: folder `web/` (GoldPulse)
- React 19 + Vite 7 + recharts + lucide-react, desain dari prototipe user di `gold-ui/` (Manus — **di-gitignore, jangan dipublish**).
- Baca data dari `https://raw.githubusercontent.com/ponimazino/gold/main/data/*.json` (8 file) + `data/reports/index.json` (laporan mingguan) + spot live `https://xaus.com/api/v1/spot?compact=1` (field `xau.price`, tanpa key, display only).
- Struktur: `web/src/data.ts` (layer data + tipe), `web/src/pages/Home.tsx` (5 view: **ringkasan** [tab pertama, bahasa awam] / overview / analysis / backtest [ada panel PDF mingguan] / calendar), `web/src/index.css` (design system goldpulse), PWA di `web/public/` (manifest+sw.js — navigasi network-first, asset cache-first).
- Chart Primary Instrument: bisa **digeser (drag) & zoom (scroll + tombol +/−/reset)** — window 15..1000 bar dari 1200 bar dimuat; default 90 bar terakhir.
- Build: `npm run check` (tsc) + `npm run build`. Bundle ~188 kB gzip.
- **PWA vanilla lama masih ada di root repo** (index.html/app.js/style.css/sw.js/manifest.webmanifest) — tidak ter-deploy saat Vercel root=web, tapi kandidat pembersihan (tanya user dulu).

## Perubahan lokal BELUM di-push (menunggu perintah user, per 2026-09-06)
Audit + fitur baru: fix track.py/sw.js/localStorage/filter event; chart pan-zoom; sumber Twelve Data; last update di Daily posture; view Ringkasan; laporan mingguan PDF (report.py + report.yml + panel UI). Test Python 4/4 suite lolos, tsc + build bersih.

## Next step user: deploy ke Vercel
Root Directory = `web` (auto-detect Vite), lihat DEPLOY.md Bagian 5. Setelah itu: verifikasi jam jalan workflow `daily.yml` (cek tab Actions besok ~05:37 WIB).

## Audit 2026-09-06 — bug yang sudah diperbaiki (jangan kambuh)
- `track.py`: job daily mencatat status "entry" tapi `_resolve_one` cuma proses "active" → rekomendasi tak pernah dinilai. FIXED: normalisasi entry→active (regression test di test_p5.py). Hit-rate baru muncul setelah rekomendasi mulai teresolve (24 bar H1 ~1 hari).
- `web/public/sw.js`: index.html cache-first bikin user stuck versi lama setelah deploy. FIXED: request navigasi = network-first.
- localStorage (analyst notes) dibungkus try/catch (private mode).
- Fundamentals: event >24 jam lampau difilter dari feed.

## Gotcha Windows/Git yang pernah terjadi
- Credential Manager pernah cache akun salah (miawopen) → 403. Pakai remote URL dengan hint username `https://ponimazino@github.com/...`.
- Push dari shell Claude pernah gagal "Cannot prompt / could not read Username" → kalau git butuh interaksi auth, minta user push sendiri.