import argparse
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import requests
from config import (
    THREADS_ACCESS_TOKEN,
    THREADS_USER_ID,
    THREADS_API_BASE_URL,
    KEYWORDS,
    DB_PATH
)
from db import save_raw_posts, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MOCK_SAMPLE_POSTS = [
    {
        "post_id": "thread_mock_001",
        "author": "ahmad_bekasi",
        "text": "Teman-teman ada yang tau info kajian sunnah rutin di sekitaran Bekasi Barat gak ya? Mau mulai ikut ngaji lagi.",
        "url": "https://www.threads.net/@ahmad_bekasi/post/mock001",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_mock_002",
        "author": "fajar_jaksel",
        "text": "Minta saran dong majelis taklim atau kajian yang bahas kitab aqidah sekitar Tebet jaksel, infonya dimana ya?",
        "url": "https://www.threads.net/@fajar_jaksel/post/mock002",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_mock_003",
        "author": "sarkas_user",
        "text": "Banyak yang nyari kajian sunnah tapi giliran diajak ngaji beneran wkwk doang gak action cuma teori :)",
        "url": "https://www.threads.net/@sarkas_user/post/mock003",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_mock_004",
        "author": "dina_ramadhani",
        "text": "Lagi nyari tempat ngaji bareng ustadz ahlus sunnah yang ramah pemula di Depok, barangkali ada jadwal rutin mingguan?",
        "url": "https://www.threads.net/@dina_ramadhani/post/mock004",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_mock_005",
        "author": "random_curhat",
        "text": "Sedih banget hari ini kerjaan numpuk gak ada habisnya, pengen liburan sejenak.",
        "url": "https://www.threads.net/@random_curhat/post/mock005",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
]

def fetch_from_threads_api() -> List[Dict[str, Any]]:
    if not THREADS_ACCESS_TOKEN:
        logger.warning("THREADS_ACCESS_TOKEN belum diisi di .env. Lewati pemanggilan API Threads.")
        return []

    collected_posts: List[Dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {THREADS_ACCESS_TOKEN}"}

    # Coba endpoint pencarian atau feed resmi Threads Graph API
    for kw in KEYWORDS:
        try:
            # Endpoint standar pencarian Threads Graph API
            url = f"{THREADS_API_BASE_URL}/threads/search"
            params = {
                "q": kw,
                "fields": "id,text,permalink,timestamp,username"
            }
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json().get("data", [])
                for item in data:
                    collected_posts.append({
                        "post_id": str(item.get("id")),
                        "author": item.get("username", "threads_user"),
                        "text": item.get("text", ""),
                        "url": item.get("permalink", f"https://www.threads.net/post/{item.get('id')}"),
                        "created_at": item.get("timestamp", datetime.now(timezone.utc).isoformat())
                    })
            else:
                logger.info(f"Pencarian kata kunci '{kw}' mengembalikan status {res.status_code}")
        except Exception as e:
            logger.error(f"Error memanggil Threads API untuk kata kunci '{kw}': {e}")

    # Fallback endpoint user threads jika query search memerlukan approval khusus
    if not collected_posts:
        try:
            url = f"{THREADS_API_BASE_URL}/{THREADS_USER_ID}/threads"
            params = {"fields": "id,text,permalink,timestamp,username"}
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json().get("data", [])
                for item in data:
                    collected_posts.append({
                        "post_id": str(item.get("id")),
                        "author": item.get("username", "threads_user"),
                        "text": item.get("text", ""),
                        "url": item.get("permalink", f"https://www.threads.net/post/{item.get('id')}"),
                        "created_at": item.get("timestamp", datetime.now(timezone.utc).isoformat())
                    })
        except Exception as e:
            logger.error(f"Error mengambil user threads: {e}")

    return collected_posts

def run_fetch(mock_mode: bool = False, db_path: Optional[str] = None) -> int:
    init_db(db_path or DB_PATH)

    if mock_mode or (not THREADS_ACCESS_TOKEN):
        if not THREADS_ACCESS_TOKEN and not mock_mode:
            logger.info("Kredensial Threads belum terisi. Memuat data sampel untuk verifikasi alur.")
        posts = MOCK_SAMPLE_POSTS
    else:
        posts = fetch_from_threads_api()

    if not posts:
        logger.info("Tidak ada post baru yang ditemukan.")
        return 0

    inserted = save_raw_posts(posts, db_path or DB_PATH)
    logger.info(f"Berhasil menyimpan {inserted} postingan baru ke raw_posts.")
    return inserted

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tarik postingan dari Threads API resmi.")
    parser.add_argument("--mock", action="store_true", help="Gunakan data sampel lokal untuk pengujian.")
    args = parser.parse_args()
    run_fetch(mock_mode=args.mock)
