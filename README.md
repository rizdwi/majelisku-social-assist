# Majelisku Social Assist

Sistem pendukung tim media sosial Majelisku (majelisku.web.id) untuk mendeteksi postingan Threads yang mencari kajian sunnah dan menyiapkan draft balasan yang natural dan relevan.

## Prinsip Utama

1. Human-in-the-loop: Tidak ada auto-posting. Semua balasan wajib melalui persetujuan admin manusia.
2. Relevansi ketat: Rekomendasi Majelisku hanya disertakan jika penanya memang mencari informasi kajian.
3. Kepatuhan platform: Menggunakan Threads API resmi milik pengguna.

## Pipeline (3 Langkah Mudah)

1. **Ambil** — Tarik postingan pencari kajian dari Threads API (tombol "Tarik dari Threads" / "Thread Orang Lain"), **atau import hasil scrape eksternal** lewat panel Pipeline di dashboard web: tempel JSON berisi daftar postingan, formatnya:
   ```json
   [{"author": "namaakun", "text": "kajian sunnah di bandung dimana ya?", "url": "https://www.threads.com/@akun/post/xxx", "created_at": "2026-10-14T08:00:00+00:00"}]
   ```
   Field `text` wajib; `post_id` opsional (dibuat otomatis dari URL+teks bila kosong). Endpoint yang sama tersedia sebagai API: `POST /api/import-posts` dengan body `{"posts": [...]}`.
2. **Saring & Draft (otomatis)** — Setiap selesai ambil/import, classifier menyaring postingan (skor >= ambang = kandidat) dan AI langsung menyusun draft balasan bridging ke Majelisku.web.id. Tidak ada tombol terpisah.
3. **Review** — Admin memeriksa draft di Antrean Review, menyetujui/melewati, lalu menempel balasan ke postingan asli. Tidak ada auto-posting.

Catatan: berkas `data/leads_seed.json` berisi contoh leads asli hasil scrape; isinya dimuat otomatis satu kali saat basis data masih kosong (mis. cold start serverless).

## Struktur Berkas

- `config.py`: Konfigurasi kata kunci, bobot skor filter, dan pembacaan berkas .env.
- `db.py`: Manajemen basis data SQLite (raw_posts dan drafts).
- `fetch_threads.py`: Pengambil postingan dari Threads API resmi.
- `classifier.py`: Filter scoring berbasis aksi, objek, booster, dan pola pengecualian.
- `generate_draft.py`: Pemanggil model AI untuk validasi relevansi dan pembuatan draft balasan.
- `dashboard.py`: Antarmuka web lokal berbasis Streamlit untuk proses review manual.
- `test_pipeline.py`: Skrip pengujian otomatis mandiri untuk memverifikasi logika alur kerja.
- `run.bat`: Peluncur sekali klik untuk menjalankan dasbor pada Windows.

## Cara Mengisi Kredensial

Salin berkas `.env.example` ke `.env` lalu lengkapi isinya:

```env
THREADS_ACCESS_TOKEN=kredensial_token_threads_anda
THREADS_USER_ID=me
THREADS_API_BASE_URL=https://graph.threads.net/v1.0

AI_API_KEY=kunci_api_ai_anda
AI_MODEL=claude-3-5-sonnet-20241022
```

## Cara Menjalankan

1. Menggunakan berkas batch:
   Klik dua kali pada berkas `run.bat`.

2. Menggunakan terminal:
   ```bash
   .venv/Scripts/python -m streamlit run dashboard.py
   ```
   Buka peramban di alamat `http://localhost:8501`.
