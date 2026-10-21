import os
from pathlib import Path
from dotenv import load_dotenv

# Muat environment variable dari file .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Path database SQLite (audit 2026-10-22: env DB_PATH selalu didahulukan di
# semua lingkungan). Default Vercel /tmp/data.db karena filesystem serverless
# read-only selain /tmp. KETERBATASAN: /tmp bersifat ephemeral per instance —
# data review tidak dijamin bertahan lintas cold start/deploy. Untuk
# persistensi sejati, arahkan DB_PATH ke volume persisten / DB eksternal.
if os.getenv("DB_PATH"):
    DB_PATH = os.getenv("DB_PATH")
elif os.getenv("VERCEL"):
    DB_PATH = "/tmp/data.db"
else:
    DB_PATH = str(BASE_DIR / "data.db")

# ROMBAK 2026-10-22 (perintah user): app ini TIDAK LAGI memakai API resmi
# Threads maupun layanan AI eksternal — tidak ada token/kredensial apa pun
# yang perlu diisi. Postingan masuk dari sapuan agen (pencarian sosial,
# pola yang sama dengan majelisku.web.id) lewat /api/import-posts, lalu
# disaring classifier kata kunci dan disusun draftnya oleh mesin lokal
# di generate_draft.py. Semua proses berjalan tanpa kredensial.

# Ambang skor classifier (bisa dioverride via settings DB / env SCORE_THRESHOLD)
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "2"))


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


def get_score_threshold() -> int:
    try:
        return int(get_config_val("SCORE_THRESHOLD", "2"))
    except Exception:
        return 2


# Kata kunci pencarian (dipakai agen penyapu sebagai acuan frasa)
KEYWORDS = [
    "kajian",
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
    "wkwk", ":)", "doang gak action", "cuma teori", "jual", "diskon", "promo", "affiliate", "majelisku.web.id", "majelisku"
]
