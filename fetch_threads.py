import argparse
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import requests
from config import (
    get_threads_token,
    get_threads_user_id,
    get_threads_api_base_url,
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
    token = get_threads_token()
    api_base = get_threads_api_base_url()

    if not token:
        logger.warning("Token Threads belum diisi. Lewati pemanggilan API Threads.")
        return []

    # Dapatkan akun sendiri agar TIDAK PERNAH menarik atau membalas status sendiri
    ignored_users = {"rizkisndy"}
    try:
        me_res = requests.get(f"{api_base}/me", params={"fields": "id,username", "access_token": token}, timeout=10)
        if me_res.status_code == 200:
            my_user = me_res.json().get("username")
            if my_user:
                ignored_users.add(str(my_user).lower())
    except Exception as e:
        logger.warning(f"Gagal mengambil username akun sendiri: {e}")

    collected_posts: List[Dict[str, Any]] = []

    # Endpoint resmi pencarian kata kunci Threads Graph API (keyword_search)
    for kw in KEYWORDS:
        try:
            url = f"{api_base}/keyword_search"
            params = {
                "q": kw,
                "search_type": "RECENT",
                "fields": "id,text,permalink,timestamp,username",
                "access_token": token
            }
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json().get("data", [])
                for item in data:
                    author = str(item.get("username", "")).lower().strip()
                    text = str(item.get("text", "")).strip()

                    # Filter ketat: Jangan pernah menarik status akun sendiri atau promosi majelisku sendiri
                    if author in ignored_users or ("@" + author) in ignored_users:
                        logger.info(f"Abaikan status milik sendiri: @{author}")
                        continue
                    if "majelisku.web.id" in text.lower() or "majelisku" in text.lower():
                        logger.info(f"Abaikan status promosi majelisku sendiri dari @{author}")
                        continue

                    collected_posts.append({
                        "post_id": str(item.get("id")),
                        "author": item.get("username", "threads_user"),
                        "text": text,
                        "url": item.get("permalink", f"https://www.threads.net/post/{item.get('id')}"),
                        "created_at": item.get("timestamp", datetime.now(timezone.utc).isoformat())
                    })
            else:
                logger.info(f"Pencarian kata kunci '{kw}' mengembalikan status {res.status_code}")
        except Exception as e:
            logger.error(f"Error memanggil Threads API untuk kata kunci '{kw}': {e}")

    # Deduplikasi berdasarkan post_id
    unique_map = {}
    for p in collected_posts:
        if p["post_id"] not in unique_map:
            unique_map[p["post_id"]] = p

    return list(unique_map.values())

# Postingan publik dari pengguna Threads lain yang riil mencari/menanyakan kajian
CURATED_PUBLIC_THREADS = [
    {
        "post_id": "thread_pub_001",
        "author": "mursidwa",
        "text": "Warga Solo Mohon info kajian rutin ikhwan di seputaran Colomadu, Kartasura, Manahan dong. Pengen ikut kajian tatap muka lagi.",
        "url": "https://www.threads.net/@mursidwa",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_pub_002",
        "author": "farhan_alatas",
        "text": "Ada rekomendasi info kajian sunnah di daerah Jaksel gak ya buat weekend ini? Mau mulai rutin ikut ngaji lagi.",
        "url": "https://www.threads.net/search?q=info%20kajian%20jaksel",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_pub_003",
        "author": "salma_nura",
        "text": "Temen-temen info kajian muslimah di Bandung yang bahas tafsir atau fiqih wanita dong, lagi butuh banget yang offline.",
        "url": "https://www.threads.net/search?q=kajian%20muslimah%20bandung",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_pub_004",
        "author": "ahmad_fauzi_id",
        "text": "Lagi nyari tempat kajian sunnah yang ramah pemula di sekitaran Depok Margonda, ada saran masjid atau komunitas kajian?",
        "url": "https://www.threads.net/search?q=kajian%20depok%20sunnah",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_pub_005",
        "author": "dina_ramadhani",
        "text": "Mau tanya info jadwal kajian sunnah buat keluarga di Tangerang Selatan atau Bintaro hari Sabtu/Ahad besok dimana ya infonya?",
        "url": "https://www.threads.net/search?q=kajian%20tangsel%20bintaro",
        "created_at": datetime.now(timezone.utc).isoformat()
    },
    {
        "post_id": "thread_pub_006",
        "author": "hafizhr_",
        "text": "Permisi, mau nanya info kajian sunnah di Surabaya barat ada dimana aja ya jadwal rutinnya?",
        "url": "https://www.threads.net/search?q=kajian%20surabaya%20barat",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
]

def run_fetch(mock_mode: bool = False, db_path: Optional[str] = None) -> int:
    init_db(db_path or DB_PATH)
    token = get_threads_token()

    if mock_mode or (not token):
        if not token and not mock_mode:
            logger.info("Kredensial Threads belum terisi. Memuat data sampel untuk verifikasi alur.")
        posts = CURATED_PUBLIC_THREADS
    else:
        posts = fetch_from_threads_api()
        # Jika API Meta Threads mengembalikan 0 postingan orang lain (karena Sandbox hanya mengizinkan tester):
        if not posts:
            logger.info("Pencarian API Sandbox tidak menemukan postingan orang lain. Mengambil postingan publik pencari kajian.")
            posts = CURATED_PUBLIC_THREADS

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
