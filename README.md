# Majelisku Social Assist

Sistem pendukung tim media sosial Majelisku (majelisku.web.id) untuk mendeteksi postingan Threads yang mencari kajian sunnah dan menyiapkan draft balasan yang natural dan relevan.

## Prinsip Utama

1. Human-in-the-loop: Tidak ada auto-posting. Semua balasan wajib melalui persetujuan admin manusia.
2. Relevansi ketat: Rekomendasi Majelisku hanya disertakan jika penanya memang mencari informasi kajian.
3. Tanpa kredensial: App ini TIDAK memakai API resmi Threads dan TIDAK memakai layanan AI eksternal. Tidak ada token/kunci apa pun yang perlu diisi — postingan masuk dari sapuan agen (pencarian sosial, pola yang sama dengan majelisku.web.id).

## Alur Kerja

1. **Masuk** — Agen menyapu Threads tiap 15 menit (frasa pencari kajian, umum + per-kota) lalu mengimport hasilnya lewat `POST /api/import-posts` dengan body `{"posts": [...]}`:
   ```json
   [{"author": "namaakun", "text": "kajian sunnah di bandung dimana ya?", "url": "https://www.threads.com/@akun/post/xxx", "created_at": "2026-10-14T08:00:00+00:00"}]
   ```
   Field `text` wajib; `post_id` opsional (dibuat otomatis dari URL+teks bila kosong). Import manual juga tersedia di dashboard (tombol "Import Manual", satu JSON per baris).
2. **Saring & Draft (otomatis)** — Setiap import, classifier kata kunci menyaring postingan (skor >= ambang = kandidat) dan mesin lokal `generate_draft.py` menyusun draft personal berbasis sinyal teks postingan (maksud/kota/emosi TS) dengan bridging ke Majelisku.web.id. Tombol "Proses Antrean" menjalankan tahap ini untuk postingan yang masih pending.
3. **Review** — Admin memeriksa draft di Antrean Review (tautan "Lihat di Threads" membuka postingan aslinya untuk verifikasi sumber), mengedit bila perlu, menyetujui, menyalin balasan, menempelnya manual ke postingan asli, lalu menandai "Sudah Dibalas". Tidak ada auto-posting.

Catatan: berkas `data/leads_seed.json` berisi leads asli hasil scrape; isinya dimuat otomatis satu kali saat basis data masih kosong (mis. cold start serverless).

## Struktur Berkas

- `config.py`: Path database, ambang skor, dan daftar kata kunci classifier.
- `db.py`: Manajemen basis data SQLite (raw_posts dan drafts).
- `classifier.py`: Filter scoring berbasis aksi, objek, booster, dan pola pengecualian.
- `generate_draft.py`: Mesin lokal penyusun draft personal dari sinyal teks (tanpa AI eksternal).
- `api/index.py`: Aplikasi FastAPI + dashboard review (satu-satunya antarmuka).
- `test_pipeline.py`: Skrip pengujian otomatis mandiri untuk memverifikasi logika alur kerja.

## Cara Menjalankan

```bash
python -m uvicorn api.index:app --port 8000
```
Buka peramban di alamat `http://localhost:8000`. Di produksi, Vercel mengarahkan semua rute ke `api/index.py`.

## Audit 2026-10-22 — penghapusan data karangan & hardcode

Temuan utama: dashboard terus menampilkan postingan "mockup" karena
`fetch_threads.run_fetch()` otomatis memasukkan postingan karangan
(`CURATED_PUBLIC_THREADS`) setiap kali token Threads kosong atau API
mengembalikan 0 hasil. Perbaikan:

- `fetch_threads.py`: seluruh data contoh/karangan (`MOCK_SAMPLE_POSTS`,
  `CURATED_PUBLIC_THREADS`, flag `--mock`) DIHAPUS. Tanpa token / API kosong
  = 0 postingan. Username akun sendiri tidak lagi di-hardcode — dari env
  `OWN_THREADS_USERNAME` dan endpoint `/me`.
- `api/index.py`: endpoint `/api/seed-fresh-samples`,
  `/api/pull-public-threads`, `/api/clear-mock` beserta tombol UI-nya
  DIHAPUS. URL Meta di test-calls kini dari konfigurasi, bukan hardcode.
- Seed `data/leads_seed.json` adalah leads ASLI hasil scrape (bukan mock),
  dimuat SEKALI; flag settings `seed_v1_done` mencegahnya bangkit lagi
  setelah database dikosongkan.
- `config.py`: `DB_PATH` bisa dioverride via env di semua lingkungan;
  logika menyalin `data.db` terbundel dari repo DIHAPUS.
- `generate_draft.py`: fallback tanpa AI kini JUJUR berlabel
  "FALLBACK TANPA AI" (dulu menyamar sebagai hasil validasi AI).
- Ambang skor disamakan: default 2 di kode, `.env.example`, dan API.

Sumber data yang sah sekarang hanya: Threads API resmi, `/api/import-posts`
(hasil scrape nyata), input manual admin, dan seed leads asli (sekali).

Catatan persistensi: di Vercel, SQLite berada di `/tmp` (ephemeral per
instance) — data review bisa hilang saat instance berganti/deploy. Untuk
persistensi sejati arahkan env `DB_PATH` ke volume persisten atau migrasi
ke database eksternal.

## Rombak 2026-10-22 — dashboard review murni, tanpa kredensial

Perintah user: seleksi dirombak meniru pola majelisku.web.id (sapuan agen,
tanpa mengisi kredensial Threads) dan seluruh tombol/kredensial lama dihapus.

- DIHAPUS: `fetch_threads.py` (API Threads resmi), `credentials_checker.py`,
  `dashboard.py` (Streamlit lama), `run.bat`; endpoint `/api/settings`,
  `/api/validate-key`, `/api/trigger-meta-test-calls`, `/api/add-thread`,
  `/api/reset-queue`, `/api/wipe-database`, `/api/raw-posts`,
  `/api/force-candidate`, `/api/reclassify`; middleware penelan kredensial
  dari header; seluruh panel Pengaturan & input token/kunci di UI.
- `generate_draft.py`: cabang AI eksternal dibuang — mesin lokal berbasis
  sinyal teks menjadi satu-satunya penyusun draft.
- `config.py`: tinggal path DB, ambang skor, dan kata kunci classifier.
- UI baru ringkas: statistik, panel Postingan Masuk (Proses Antrean +
  Import Manual), Antrean Review, Disetujui, Dilewati. Tiap kartu punya
  tautan profil penulis + tombol "Lihat di Threads" ke postingan asli.
- Fix: antrean Disetujui kini juga memuat status 'posted' (sebelumnya item
  yang ditandai sudah dibalas hilang dari semua daftar).
