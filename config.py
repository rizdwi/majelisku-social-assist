import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Muat environment variable dari file .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Pada lingkungan Vercel Serverless, filesystem bersifat read-only kecuali /tmp
if os.getenv("VERCEL"):
    DB_PATH = "/tmp/data.db"
    bundled_db = BASE_DIR / "data.db"
    if not os.path.exists(DB_PATH) and bundled_db.exists():
        try:
            shutil.copyfile(bundled_db, DB_PATH)
        except Exception:
            pass
else:
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data.db"))

# Konfigurasi Threads API
# User akan mengisi kredensial ini secara mandiri di file .env
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "me").strip()
THREADS_API_BASE_URL = os.getenv("THREADS_API_BASE_URL", "https://graph.threads.net/v1.0").rstrip("/")

# Konfigurasi AI API
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()

# Parameter Filter & Scheduler
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "3"))
FETCH_INTERVAL_HOURS = int(os.getenv("FETCH_INTERVAL_HOURS", "1"))

# Kata kunci pencarian Threads
KEYWORDS = [
    "cari kajian",
    "nyari kajian",
    "info kajian",
    "tempat ngaji",
    "rekomendasi kajian",
    "kajian sunnah",
    "majelis ilmu terdekat",
    "pengajian sunnah"
]

# Komponen scoring classifier (sesuai design.md §2.2)
MUST_HAVE_ACTION = [
    "cari", "nyari", "ada yang tau", "rekomendasi", "dimana ya", "minta saran", "info"
]

MUST_HAVE_OBJECT = [
    "kajian", "ngaji", "majelis ilmu", "majelis taklim", "tempat ngaji", "pengajian"
]

BOOSTERS = [
    "sunnah", "ahlus sunnah", "manhaj salaf", "ustadz", "jadwal", "rutin",
    "jaksel", "bekasi", "depok", "bogor", "tangerang", "bandung", "surabaya"
]

EXCLUDE_PATTERNS = [
    "wkwk", ":)", "doang gak action", "cuma teori", "jual", "diskon", "promo", "affiliate"
]
