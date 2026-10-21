import os
import tempfile
from db import (
    init_db,
    save_raw_posts,
    get_pending_posts,
    get_candidates_without_draft,
    save_draft,
    get_review_queue,
    update_draft_review,
    get_approved_queue,
    get_stats
)
from classifier import score_post, classify_pending_posts
from generate_draft import process_candidates

def run_tests():
    # Gunakan temporary SQLite database untuk pengujian terisolasi
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db = tf.name

    try:
        init_db(temp_db)

        # 1. Uji fungsi skoring classifier
        score_good, reasons_good = score_post(
            "Teman-teman ada yang tau info kajian sunnah rutin di Bekasi Barat?"
        )
        assert score_good >= 3, f"Ekspektasi skor >= 3, didapat: {score_good}"
        assert any("kombinasi kata aksi dan objek" in r for r in reasons_good)

        # Uji exclude pattern (sarkasme)
        score_bad, _ = score_post("Mau ngaji tapi wkwk doang cuma teori")
        assert score_bad < 3, f"Post dengan sarkasme harus bernilai rendah, didapat: {score_bad}"

        # 2. Uji penyimpanan idempotent raw posts
        posts = [
            {
                "post_id": "p_01",
                "author": "user1",
                "text": "Ada info kajian sunnah di Jakarta?",
                "url": "https://threads.net/p01",
                "created_at": "2026-10-21T00:00:00Z"
            },
            {
                "post_id": "p_02",
                "author": "user2",
                "text": "Curhat capek lembur hari ini",
                "url": "https://threads.net/p02",
                "created_at": "2026-10-21T00:00:00Z"
            }
        ]
        inserted = save_raw_posts(posts, temp_db)
        assert inserted == 2, f"Ekspektasi 2 post tersimpan, didapat: {inserted}"

        # Simpan ulang (idempotensi)
        inserted_again = save_raw_posts(posts, temp_db)
        assert inserted_again == 0, f"Post duplikat tidak boleh diinsert ulang, didapat: {inserted_again}"

        # 3. Uji klasifikasi status
        pending = get_pending_posts(temp_db)
        assert len(pending) == 2
        processed = classify_pending_posts(temp_db)
        assert processed == 2

        candidates = get_candidates_without_draft(temp_db)
        assert len(candidates) == 1, f"Hanya 1 post yang harus lolos jadi candidate, didapat: {len(candidates)}"
        assert candidates[0]["post_id"] == "p_01"

        # 4. Uji pemrosesan draft (menggunakan mode fallback lokal)
        draft_count = process_candidates(temp_db)
        assert draft_count == 1, f"Harus menghasilkan 1 draft, didapat: {draft_count}"

        queue = get_review_queue(temp_db)
        assert len(queue) == 1
        assert queue[0]["review_status"] == "pending_review"

        # 5. Uji proses approval oleh admin
        update_draft_review(
            post_id="p_01",
            review_status="approved",
            final_reply="Halo kak, cek Majelisku.web.id ya untuk kajian sunnah terdekat.",
            db_path=temp_db
        )

        approved = get_approved_queue(temp_db)
        assert len(approved) == 1
        assert approved[0]["final_reply"] == "Halo kak, cek Majelisku.web.id ya untuk kajian sunnah terdekat."

        # Cek statistik
        stats = get_stats(temp_db)
        assert stats["total_raw"] == 2
        assert stats["total_candidates"] == 1
        assert stats["pending_review"] == 0
        assert stats["approved"] == 1

        print("Semua pengujian pipeline berhasil tanpa error!")
    finally:
        if os.path.exists(temp_db):
            os.remove(temp_db)

if __name__ == "__main__":
    run_tests()
