# Panduan Deploy — Gold Analisator (P1–P5 selesai)

Langkah detail dari kondisi sekarang (kode selesai, diuji, data bersih)
sampai app live di HP. Ikuti berurutan. Perkiraan total waktu:
±30–45 menit (backfill 3 tahun berjalan otomatis di GitHub, ±10–15 menit).

---

## Bagian 0 — Persiapan akun (sekali saja)

| Yang perlu | Cara dapat | Gratis? |
|---|---|---|
| Akun GitHub | github.com/signup | ya |
| API key Twelve Data | twelvedata.com → daftar → Dashboard → API Keys | ya (free: 800 kredit/hari) |
| Akun Vercel **atau** Cloudflare | vercel.com/signup **atau** dash.cloudflare.com (Pages) | ya |

Tidak butuh: VM, server, kartu kredit. Semua jalan di GitHub Actions
(repo public = menit runner gratis tak terbatas) + static hosting.

---

## Bagian 1 — Bersih-bersih lokal ✅ SUDAH DILAKUKAN

Folder `data/` (data sintetis + statistik yang dihitung darinya) **sudah
dihapus** — repo sekarang bersih, tinggal kode. Kode otomatis membuat
ulang folder `data/` saat pertama menulis (lihat `_atomic_write` di
`analyzer/store.py`), jadi tidak ada yang perlu dilakukan di sini.

Tinggal verifikasi sekali (1 menit):

```powershell
git status --short     # TIDAK boleh ada folder data/ dan file .env di daftar
```

- Kalau suatu saat kamu bikin `.env` lokal (berisi API key), file itu
  otomatis di-ignore (`.gitignore`) — tidak akan pernah ikut commit.
- Satu-satunya credential di seluruh sistem: `TWELVEDATA_API_KEY`, dan itu
  hanya hidup di GitHub Secrets — frontend tidak memegang kunci apa pun.

---

## Bagian 2 — Commit pertama + push ke GitHub

```powershell
git add .
git commit -m "feat: gold analyzer P1-P5 (data, chart PWA, backtest, fundamental, rekomendasi)"
```

Buat repo di GitHub:
1. github.com → **New repository**.
2. Nama bebas (mis. `gold-analyzer`), **Public** (wajib — menit Actions
   gratis tak terbatas hanya untuk repo public), **tanpa** README/.gitignore
   auto (sudah ada di repo).
3. Copy alamat repo, lalu:

```powershell
git remote add origin https://github.com/USERNAME/gold-analyzer.git
git push -u origin main
```

Setelah push: buka repo di browser → pastikan **tidak ada** folder `data/`
yang berisi file, dan tidak ada `.env`.

---

## Bagian 3 — Pasang API key di GitHub Secrets

1. Ambil key gratis: twelvedata.com → login → **API Keys** → copy.
2. Di halaman repo GitHub: **Settings → Secrets and variables → Actions →
   New repository secret**.
3. Name: `TWELVEDATA_API_KEY` (persis, huruf besar semua) — Secret value:
   paste key → Add secret.

Key tidak pernah muncul di log Actions, tidak pernah masuk commit,
dan tidak pernah dikirim ke browser.

---

## Bagian 4 — Isi data riil (jalankan dari GitHub, bukan lokal)

Kenapa di GitHub: hasil backfill langsung di-commit ke repo, dan tidak
perlu `.env` di komputer.

1. Tab **Actions** (kalau baru push, tunggu ±10 detik sampai 4 workflow
   muncul: Hourly Sync, Fundamental, Analisa Harian, Backfill).
2. Kiri: pilih **Backfill** → **Run workflow** → Years: `3` → Run.
3. Tunggu ±10–15 menit (job menarik ~18.000 candle H1 + ~4.500 H4 dengan
   paginasi 5.000 bar per request, sopan terhadap rate limit). Selesai
   ditandai commit `data: backfill 3y` di halaman depan repo.
4. Lanjut isi fundamental + rekomendasi pertama (keduanya punya tombol
   Run workflow juga):
   - **Fundamental** → Run workflow (isi `calendar.json` + `news.json`)
   - **Analisa Harian** → Run workflow (isi `patterns.json` +
     `recommendation.json` + `tracking.json`)

Cek hasil: file di `data/` di repo harus ada `xauusd_1h.json`,
`xauusd_4h.json`, `meta.json`, `patterns.json`, `calendar.json`,
`news.json`, `recommendation.json`, `tracking.json`. Buka `meta.json` —
`coverage` harus menunjukkan ~3 tahun dan `status: "ok"`.

Kalau Backfill gagal di tengah: aman diulang — job idempotent dan
merging (bar yang sudah ada tidak diambil dua kali), kredit cuma terpakai
sedikit.

---

## Bagian 5 — Deploy frontend (pilih SATU)

Frontend ini murni static (HTML/CSS/JS vanilla, tanpa build) — root repo
sudah siap deploy apa adanya.

### Opsi A — Vercel
1. vercel.com → **Add New → Project** → import repo `gold-analyzer`.
2. Konfigurasi: Framework Preset = **Other**, Build Command = *(kosong)*,
   Output Directory = *(kosong / `./`)*.
3. Deploy. Domain: `gold-analyzer.vercel.app` (bisa custom domain di
   Settings → Domains).

### Opsi B — Cloudflare Pages
1. dash.cloudflare.com → **Workers & Pages → Create → Pages → Connect to
   Git** → pilih repo.
2. Build configuration: Framework preset = **None**, Build command =
   *(kosong)*, Build output directory = `/`.
3. Save and Deploy. Domain: `gold-analyzer.pages.dev`.

Setelah deploy: buka URL di HP → chart harus tampil dengan data 3 tahun,
harga live (titik hijau "● live" dari XAUS.com), kartu rekomendasi, panel
statistik pola, dan seksi Fundamental. Menu browser → **Add to Home
Screen** → jadi app (PWA, offline shell tetap terbuka dengan data cache).

Catatan: setiap commit baru (termasuk commit data otomatis dari Actions)
memicu redeploy otomatis — tidak ada yang perlu di-klik lagi.

---

## Bagian 6 — Apa yang berjalan otomatis setelah ini

| Workflow | Jadwal | Isi apa |
|---|---|---|
| Hourly Sync | tiap jam :17 UTC (Sen–Sab) | candle H1/H4 baru → `data/xauusd_*.json` |
| Fundamental | tiap 4 jam :43 UTC | kalender 3★ US + berita → `calendar.json`, `news.json` |
| Analisa Harian | 05:37 WIB (Sen–Jum) | statistik + rekomendasi + nilai kemarin → `patterns.json`, `recommendation.json`, `tracking.json` |

- Konsumsi Twelve Data: ~48 dari 800 kredit/hari.
- Panel "Feedback loop" di app mulai terisi setelah rekomendasi pertama
  selesai dinilai (1–2 hari kerja). Hit-rate riil di bawah ~50% setelah
  sampel ≥20 → saatnya tuning parameter pola/ATR (jangan dijudge dari
  sampel kecil).

## Masalah umum

| Gejala | Sebab & solusi |
|---|---|
| Backfill gagal, log bilang 429 | rate limit Twelve Data — tunggu beberapa menit, Run ulang. Job sudah punya backoff otomatis, gagal total jarang. |
| Actions tiba-tiba mati setelah lama tidak ada aktivitas | GitHub menonaktifkan cron repo yang 60 hari tanpa commit — tidak akan terjadi di sini (data di-commit tiap jam), tapi kalau terjadi: tab Actions → enable workflow. |
| Chart kosong di app | `data/xauusd_1h.json` belum ada → Bagian 4 belum dijalankan. |
| Harga live mati ("○ tersimpan") | XAUS.com sedang tidak bisa diakses — chart tetap pakai close tersimpan; cek lagi nanti. |
| Rekomendasi selalu "netral" | Itu perilaku benar — sistem hanya entry saat pola H1 searah trend H4 **dan** tidak ada jeda event. Bukan bug. |
| Aku ubah kode, app tidak berubah | Service worker cache — tutup semua tab app, buka lagi (SW versi baru akan mengganti). |