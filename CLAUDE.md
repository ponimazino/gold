# Gold Analyzer (XAUUSD) — Project Context

Project: analyzer harian XAUUSD, serverless penuh (GitHub Actions + JSON di repo + static hosting).
Semua komunikasi dengan user dalam **Bahasa Indonesia**.

## ATURAN KERJA (WAJIB)
- **Commit/push ke GitHub HANYA kalau user menyuruh.** Jangan pernah commit/push atas inisiatif sendiri.
- **Credential tidak boleh bocor**: API key hanya di `.env` (gitignored) lokal dan GitHub Secrets. Tidak pernah hardcoded, tidak pernah di `data/`, frontend nol secret.
- **Notifikasi = hanya Web Push ke PWA sendiri** (permintaan user 2026-09-09, menggantikan larangan total lama): `analyzer/push.py` + `pywebpush`, dipicu job daily. Notif: (1) setup entry (1×/hari WIB, dedup id rec), (2) agenda event 3★ hari itu H-3 jam sebelum event pertama (1×/hari). Credential: `PUSH_VAPID_PRIVATE_KEY` + `PUSH_CONTACT_EMAIL` + `PUSH_SUBSCRIPTIONS` (JSON subscription) **hanya GitHub Secrets + `.env` lokal** — subscription TIDAK boleh di `data/` (repo public, endpoint bisa dipakai spam). Public key VAPID memang boleh di frontend (`VAPID_PUBLIC_KEY` di data.ts). Status pengiriman di `data/push_state.json` (`last_status: ok|expired|error`) → UI banner "notif putus". **Telegram tetap terlarang** (sudah dihapus total, jangan ditambahkan lagi).
- Waktu tampil selalu **WIB (Asia/Jakarta)**, penyimpanan UTC, timeframe H1/H4 (H4 prioritas).
- Berita hanya dari sumber wire/valid (whitelist), event fundamental 3★ US only (Trading Economics).

## Status (per 2026-09-06): SEMUA PHASE P1–P5 SELESAI & TERDEPLOY
- Repo: **github.com/ponimazino/gold** (public). Remote: `https://ponimazino@github.com/ponimazino/gold.git`.
- Data 3 tahun ter-backfill: 20.493 bar H1 + 5.323 bar H4 (2023-09-06 → 2026-09-06).
- Workflow GitHub Actions (checkout@v5, setup-python@v6, semua commit step pakai `git pull --rebase origin main` dulu untuk hindari race):
  - `daily.yml` — grid 2 jam "37 1-15/2 * * 1-5" + "37 21,23 * * 0-4" (04:37–22:37 WIB, Sen–Jum; anchor 04:37 pra-Sydney, 14:37 London, 20:37 NY + rilis data AS; tiap pasangan candle H1 dievaluasi tepat sekali) + **slot cadangan :52** → research + resolve feedback loop + `data/recommendation.json`, `data/tracking.json` + **Web Push** (`push.dispatch`: notif entry + agenda event; dedup per hari WIB di `data/push_state.json`; secrets kosong = dilewati tanpa error; gagal kirim tidak pernah gagalkan job). Recommendation.json dioverwrite tiap run; history tracking tetap 1 entry/hari (dedup per tanggal). **Watch loop (2026-09-08)**: begitu satu run lolos, runner tetap hidup ~6 jam dan tiap 2 jam menjalankan **sync sendiri + analisa + commit** — tidak lagi bergantung workflow sync jalan duluan. `concurrency: daily, cancel-in-progress: true` (maks 1 loop aktif).
  - `sync.yml` — "17,47 * * * *" (setiap hari termasuk Minggu; :47 = cadangan anti-drop): update bar H1/H4, gap-filling. **Watch loop (2026-09-08)**: run yang lolos tetap hidup ~6 jam, sync + commit tiap 30 menit. `cancel-in-progress: true` (maks 1 loop, kuota Twelve Data terkendali ~24-48 req/run yang lolos).
  - `fundamental.yml` — "13 1-23/3 * * *" + cadangan ":28" (tiap 3 jam): kalender 3★ US + berita whitelist
  - `report.yml` — **Laporan Harian (EOD)**: "7 16 * * 1-5" + cadangan "53 16 * * 1-5" (23:07 WIB Sen–Jum, setelah slot grid daily terakhir 22:37 WIB) + dispatch: `analyzer/jobs/report.py` → PDF harian `data/reports/daily-YYYY-MM-DD.pdf` + `data/reports/index.json` (max 90 entri; reportlab di requirements.txt; smoke test `tests/test_report_smoke.py`). Isi: ringkasan hari itu + 7 hari, grafik 4 minggu, rekap bulanan, statistik backtest (CI + net), TP2/risiko, feedback loop, **hasil rekomendasi per hari (log EOD tracking.json, 7 hari terakhir + detail hari terbaru)**, event 7 hari ke depan, berita.
  - `backfill.yml` — dispatch only (jalankan manual saja; idempotent, merging)
- Rekomendasi: pola H1 di 2 bar terakhir searah trend H4 (EMA20/50) → status entry/tunggu/netral; confidence = win-rate OOS pola; blackout −2h..+1h sekitar event 3★; level Entry=close terakhir, SL 1×ATR, TP1 1,5×ATR, TP2 3×ATR. **Mode aman** (TP 1×ATR / SL 0,75×ATR, entry sama): `levels_safe` + `confidence_safe` — win-rate-nya di-backtest terpisah (`run_all` research, `safe_*` di patterns.json), feedback loop tetap menilai level standar. Layer research pakai sinyal **dedup cooldown 4 bar**, win-rate dibubuhi **Wilson CI 95%** (`win_rate_lo/hi`), dan ada pasangan kolom **net-of-cost** (`cost_*`/`safe_cost_*`, spread $0.35/oz — barrier TP lebih jauh + SL lebih dekat dalam harga mid, R nominal tetap); rekomendasi live tetap GROSS demi kompatibilitas feedback loop, angka net tampil di rationale.
- Feedback loop: `analyzer/track.py` resolve rec aktif vs bar H1 nyata (SL dicek dulu, konservatif; timeout 24 bar) → hit-rate jujur di `data/tracking.json`. Saat resolve juga menulis `outcome` (bars_held, MFE/MAE USD, event 3★ di jendela, narasi Indonesia "mengapa benar/salah") + log akhir hari `eod` per tanggal WIB (diturunkan penuh dari history — idempotent), tampil di panel Rule monitor UI.

## Frontend produksi: folder `web/` (GoldPulse)
- React 19 + Vite 7 + recharts + lucide-react, desain dari prototipe user di `gold-ui/` (Manus — **di-gitignore, jangan dipublish**).
- Baca data dari `https://raw.githubusercontent.com/ponimazino/gold/main/data/*.json` (9 file: 8 lama + `push_state.json`) + `data/reports/index.json` (laporan mingguan) + spot live `https://xaus.com/api/v1/spot?compact=1` (field `xau.price`, tanpa key, display only).
- **Web Push PWA**: `web/public/sw.js` (v4) punya listener `push` (notif dari payload JSON job daily) + `notificationclick` (buka/fokus PWA ke hash route `#/analysis` / `#/calendar` — deep-link via `location.hash` di Home.tsx). Panel langganan "Notifikasi Android" di view Jadwal (`PushPanel` Home.tsx): aktifkan izin + `pushManager.subscribe` (public key VAPID `VAPID_PUBLIC_KEY` di data.ts), tampilkan JSON subscription untuk disalin → paste ke GitHub Secrets `PUSH_SUBSCRIPTIONS`; banner "notif putus" di notice bar kalau `push_state.last_status == "expired"`.
- Struktur: `web/src/data.ts` (layer data + tipe), `web/src/pages/Home.tsx` (7 view: overview / **ringkasan** ["Apa kata hari ini", bahasa awam] / analysis / backtest [ada panel PDF harian — laporan terbaru tampil inline tanpa klik, arsip klik → popup modal] / **riwayat** [history rekomendasi per entry: level entry/SL/TP + hasil + narasi, filter hasil & pola] / calendar / jadwal), `web/src/index.css` (design system goldpulse), PWA di `web/public/` (manifest+sw.js — navigasi network-first, asset cache-first).
- Chart Primary Instrument: bisa **digeser (drag) & zoom (scroll + tombol +/−/reset)** — window 15..1000 bar dari 1200 bar dimuat; default 90 bar terakhir.
- Build: `npm run check` (tsc) + `npm run build`. Bundle ~188 kB gzip.
- **PWA vanilla lama masih ada di root repo** (index.html/app.js/style.css/sw.js/manifest.webmanifest) — tidak ter-deploy saat Vercel root=web, tapi kandidat pembersihan (tanya user dulu).

## Next step user: deploy ke Vercel
Root Directory = `web` (auto-detect Vite), lihat DEPLOY.md Bagian 5. Setelah itu: verifikasi workflow `daily.yml` jalan di grid 2 jam (cek tab Actions, run pertama setelah push di jam :37 WIB berikutnya).

## Batch TER-PUSH `0b66a89` (2026-09-09 malam): laporan mingguan → harian EOD
- `report.yml` cron "7 16 * * 1-5" + cadangan "53 16" (23:07 WIB Sen–Jum, setelah grid daily terakhir); `report.py` → `daily-YYYY-MM-DD.pdf`, banner/judul "LAPORAN HARIAN", KEEP 52→90, ringkasan eksekutif kini OHLC hari itu (WIB) + 7 hari, **section 7 baru "Hasil rekomendasi per hari"** (tabel 7 hari dari log EOD tracking.json + detail entry hari terbaru: pola/bias/hasil/MFE-MAE/narasi), section event → "7 hari ke depan" (no. 8), berita → no. 9. Frontend: label panel Backtest + kartu Jadwal → "Laporan harian PDF (EOD)". Smoke test + pypdf + tsc + build LULUS. PDF mingguan lama tetap di repo (arsip, index tetap valid).

## Batch TER-PUSH `9b8689b` (2026-09-09): upgrade jujur backtest + layout Riwayat mobile
- **Upgrade jujur backtest** (assessment vs literatur: Bailey/López de Prado dsb.), 4 perbaikan sekaligus di layer research (`analyzer/jobs/research.py`, `analyzer/backtest.py`):
  - **Net of cost**: `cost_usd=0.35` (spread standar XAUUSD) menggeser barrier MELAWAN trader dalam harga mid — TP harus tercapai +$0.35 lebih jauh, SL kena $0.35 lebih cepat; R nominal per trade TIDAK berubah. Kolom `cost_win_rate`/`cost_oos_win_rate` + `safe_cost_*` di patterns.json. Rekomendasi live masih pakai angka GROSS (kompatibel feedback loop); angka net tampil di rationale + panel Backtest UI.
  - **Dedup sinyal overlap**: sinyal searah dalam 4 bar dari sinyal searah sebelumnya di-drop di layer research (rekomendasi live TIDAK didedup). n jujur turun drastis (inside_bar H1: 2806 → 1542).
  - **Wilson CI 95%**: `win_rate_lo/hi` di patterns.json + tampil di panel Backtest; confidence_note rekomendasi menyebut rentangnya.
  - **TP2 runner + risiko di PDF mingguan** (`report.py` §5b): skenario TP1 → SL breakeven → kejar TP2 3×ATR (outcome tp2/be/stopped/no_tp1/timeout) + `risk_stats` (max drawdown R, loss streak) per pola H1.
  - `research.run_pair` → `research.run_all` (return 4 list: std/safe/std-net/safe-net); test_p3 +4 tes (cost/CI/dedup/tp2+risk); 5/5 suite + report smoke + tsc + build LULUS.
  - BUG FIX saat implementasi: model cost awal TERBALIK arah SL (SL digeser lebih jauh = terlalu optimis) — dikoreksi jadi SL lebih dekat; `report.py` loop var `pat` men-shadow alias modul `patterns` → rename `pname`.
  - **Fix layout view Riwayat mobile**: level dipecah jadi chip per-level (Entry/SL/TP1/TP2 terpisah, `.hist-detail span` = chip mono + warna good/bad), media ≤760px pill status turun ke baris sendiri (`.hist-ledger` 2 kolom) — teks tidak lagi terdesak. Terverifikasi playwright-core (channel msedge) 390px: 0 overflow / 0 overlap / 0 chip terpotong.
  - Push: rebase di atas auto-commit workflow (285d269), konflik `data/patterns.json` resolve `--theirs` (versi lokal), autostash untuk docs/ yang tidak di-commit.
- **Watch loop anti-drop + view Riwayat** (batch 2026-09-08 malam): SUDAH di-push `f89d62f`. Batch sebelum itu (grid 2 jam, EOD, mode aman, cron cadangan): `35d016d` + `abf57cd`.
- Dispatch manual: **cukup daily.yml** — run baru mem-cancel loop lama, sync sendiri dulu, lalu analisa pakai kode baru. sync/fundamental/report tidak perlu dispatch.

## Audit 2026-09-06 — bug yang sudah diperbaiki (jangan kambuh)
- `track.py`: job daily mencatat status "entry" tapi `_resolve_one` cuma proses "active" → rekomendasi tak pernah dinilai. FIXED: normalisasi entry→active (regression test di test_p5.py). Hit-rate baru muncul setelah rekomendasi mulai teresolve (24 bar H1 ~1 hari).
- `web/public/sw.js`: index.html cache-first bikin user stuck versi lama setelah deploy. FIXED: request navigasi = network-first.
- localStorage (analyst notes) dibungkus try/catch (private mode).
- Fundamentals: event >24 jam lampau difilter dari feed.

## Gotcha Windows/Git yang pernah terjadi
- Credential Manager pernah cache akun salah (miawopen) → 403. Pakai remote URL dengan hint username `https://ponimazino@github.com/...`.
- Push dari shell Claude pernah gagal "Cannot prompt / could not read Username" → kalau git butuh interaksi auth, minta user push sendiri.

## Gotcha GitHub Actions (terverifikasi 2026-09-08)
- **GitHub sering MENUNDA/DROP run terjadwal** — bukan jitter 10 menit: 2026-09-08 Hourly Sync cuma jalan 2×/hari (dari 48 slot), grid daily 10 slot jalan 0×. Slot cron cadangan menit-menit TIDAK cukup (yang di-drop jamnya juga). Solusi definitif: **watch loop** di sync.yml + daily.yml — run yang lolos tetap hidup ~6 jam dan bekerja sendiri secara berkala (sync tiap 30 mnt; daily: sync+analisa tiap 2 jam). Asal 3–4 run/hari lolos, data & rekomendasi segar terus. Job idempotent + `cancel-in-progress: true` jadi dobel-run aman dan kuota API terkendali.
- `data/*.json` konflik saat `git pull --rebase` (auto-commit workflow remote vs commit lokal kita): saat rebase, versi commit lokal = `--theirs`.