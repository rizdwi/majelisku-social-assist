import argparse
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import requests
from config import (
    get_threads_token,
    get_threads_api_base_url,
    KEYWORDS,
    DB_PATH
)
from db import save_raw_posts, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# CATATAN AUDIT 2026-10-22: Seluruh data contoh/karangan (MOCK_SAMPLE_POSTS dan
# CURATED_PUBLIC_THREADS) DIHAPUS PERMANEN. Versi lama run_fetch() otomatis
# memasukkan postingan karangan itu ke database setiap kali token Threads
# kosong atau API mengembalikan 0 hasil — itulah sebabnya "mockup" terus
# muncul di dashboard menyamar sebagai data asli. Sekarang: tidak ada token /
# tidak ada hasil = 0 postingan, titik. Data hanya boleh masuk dari
# (1) Threads API resmi, (2) endpoint /api/import-posts (hasil scrape nyata),
# (3) input manual admin, (4) seed data/leads_seed.json (leads ASLI hasil
# scrape, dimuat sekali — lihat api/index.py).

def _own_usernames() -> set:
    """Username akun sendiri yang tidak boleh ditarik/dibalas.

    Diisi dari env OWN_THREADS_USERNAME (boleh koma, tanpa @) dan, bila token
    tersedia, dari endpoint /me. Tidak ada username yang di-hardcode.
    """
    ignored = set()
    raw = os.getenv("OWN_THREADS_USERNAME", "")
    for part in raw.split(","):
        part = part.strip().lstrip("@").lower()
        if part:
            ignored.add(part)
    return ignored

def fetch_from_threads_api() -> List[Dict[str, Any]]:
    token = get_threads_token()
    api_base = get_threads_api_base_url()

    if not token:
        logger.warning("Token Threads belum diisi. Lewati pemanggilan API Threads.")
        return []

    # Dapatkan akun sendiri agar TIDAK PERNAH menarik atau membalas status sendiri
    ignored_users = _own_usernames()
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

def run_fetch(db_path: Optional[str] = None) -> int:
    """Tarik postingan dari Threads API resmi. Tanpa token = 0 (jujur, tanpa data karangan)."""
    init_db(db_path or DB_PATH)
    token = get_threads_token()

    if not token:
        logger.warning("Kredensial Threads belum terisi — tidak ada postingan yang ditarik (data karangan/fallback sudah dihapus).")
        return 0

    posts = fetch_from_threads_api()
    if not posts:
        logger.info("Threads API mengembalikan 0 postingan. Tidak ada yang disimpan.")
        return 0

    inserted = save_raw_posts(posts, db_path or DB_PATH)
    logger.info(f"Berhasil menyimpan {inserted} postingan baru ke raw_posts.")
    return inserted

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tarik postingan dari Threads API resmi.")
    parser.parse_args()
    run_fetch()
