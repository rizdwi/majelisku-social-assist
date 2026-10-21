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

def get_config_val(key: str, default: str = "") -> str:
    try:
        from db import get_setting
        val = get_setting(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    except Exception:
        pass
    env_val = os.getenv(key)
    if env_val is not None and str(env_val).strip() != "":
        return str(env_val).strip()
    return default

def get_threads_token() -> str:
    return get_config_val("THREADS_ACCESS_TOKEN", "")

def get_threads_user_id() -> str:
    return get_config_val("THREADS_USER_ID", "me")

def get_threads_api_base_url() -> str:
    return get_config_val("THREADS_API_BASE_URL", "https://graph.threads.net/v1.0").rstrip("/")

def get_ai_api_key() -> str:
    return get_config_val("AI_API_KEY", "")

def get_ai_model() -> str:
    return get_config_val("AI_MODEL", "claude-3-5-sonnet-20241022")

def get_ai_base_url() -> str:
    return get_config_val("AI_BASE_URL", "")

def get_score_threshold() -> int:
    try:
        raw = get_config_val("SCORE_THRESHOLD", "3")
        return int(raw)
    except Exception:
        return 3

def sync_to_env_file(settings: dict) -> None:
    if os.getenv("VERCEL"):
        return
    env_path = BASE_DIR / ".env"
    try:
        lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        existing_keys = set()
        new_lines = []
        for line in lines:
            line_stripped = line.strip()
            if "=" in line_stripped and not line_stripped.startswith("#"):
                k, _ = line_stripped.split("=", 1)
                k = k.strip()
                if k in settings:
                    new_lines.append(f"{k}={settings[k]}\n")
                    existing_keys.add(k)
                    continue
            new_lines.append(line)
        for k, v in settings.items():
            if k not in existing_keys:
                new_lines.append(f"{k}={v}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception:
        pass

def save_app_settings(settings: dict) -> None:
    from db import set_setting
    for k, v in settings.items():
        if v is not None:
            set_setting(k, str(v).strip())
    sync_to_env_file(settings)

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
